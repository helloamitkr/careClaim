"""The FastAPI layer. Builds the whole pipeline once at startup — one shared
EventBus, one shared HumanReviewGate — and exposes it over REST. The
frontend never talks to agents or the bus directly, only this API.

Run with: uvicorn carebridge.api.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger

from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.agents.risk_escalation import RiskEscalationAgent
from carebridge.api import schemas
from carebridge.audit import AuditLogRecord, AuditTrail
from carebridge.bus import Event, EventBus
from carebridge.fixtures import new_case_from_template
from carebridge.guardrails import InputGuardrail
from carebridge.llm import llm_available
from carebridge.logging import (
    configure_logging,
    newest_log_file,
    parse_log_line,
    tail_records,
)
from carebridge.middleware import RateLimitMiddleware
from carebridge.models import CaseStatus, TransitionCase
from carebridge.persistence import AgentDecisionRecord, CaseRecord, Database, EventRecord
from carebridge.portal.routes import router as portal_router
from carebridge.services import workflow
from carebridge.services.drafting import build_sections, compose_draft, is_ready
from carebridge.services.rag import KnowledgeBase, PostgresKnowledgeBase, default_knowledge_base
from carebridge.services.review_gate import HumanReviewGate, ReviewStatus
from carebridge.services.router import ConfidenceRouter
from carebridge.staff_auth import STAFF_COOKIE, require_staff, verify_staff_token


def build_pipeline(db: Database) -> tuple[EventBus, HumanReviewGate, RiskEscalationAgent, AuditTrail]:
    audit = AuditTrail(db)
    bus = EventBus(db=db, audit=audit)

    # Step 14 — RAG store. Postgres-backed when migration 004 has been
    # loaded; otherwise the in-memory seed keeps the pipeline fully working.
    kb: KnowledgeBase = PostgresKnowledgeBase(db)
    if not kb.is_available():
        logger.bind(component="rag").warning(
            "knowledge_base table not found — using in-memory seed "
            "(run `python dbmigration/migrate.py --with-knowledge-base` for the "
            "Postgres-backed store)"
        )
        kb = default_knowledge_base()

    ReferralRoutingAgent(bus, kb=kb)
    FollowUpSchedulingAgent(bus, kb=kb)
    if llm_available():
        # Constructed only when the LLM is usable — each of these builds an
        # LLM client on init, which would fail fast without a token.
        MedicationInstructionAgent(bus, kb=kb)
        PatientOutreachAgent(bus, kb=kb)
        DischargeReadinessAgent(bus, kb=kb)
    else:
        logger.bind(component="pipeline").warning(
            "LLM_AVAILABLE=false — LLM agents not started; incoming cases will "
            "be stored at status 'received' and left unprocessed"
        )
    risk_agent = RiskEscalationAgent(bus)
    ConfidenceRouter(bus, listens_to="case.risk_assessed", threshold=0.75)
    gate = HumanReviewGate(bus)

    return bus, gate, risk_agent, audit


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()  # Step 15 — console + rotating JSON file
    db = Database()
    db.init_schema()
    bus, gate, risk_agent, audit = build_pipeline(db)

    # Step 12 — replay any case a previous process left mid-pipeline. Also the
    # mechanism that drains cases parked at 'received' while LLM_AVAILABLE was
    # false: flip the flag back on, restart, and they run one by one.
    if llm_available():
        recovered = await risk_agent.recover_from_db()
        if recovered:
            logger.bind(component="recovery").info(
                "replayed {n} stranded case(s): {cases}",
                n=len(recovered),
                cases=", ".join(recovered),
            )

    app.state.db = db
    app.state.bus = bus
    app.state.gate = gate
    app.state.audit = audit
    app.state.bulk_tasks = set()  # keeps background batch tasks alive until done
    yield


app = FastAPI(title="CareBridge AI API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # allow_credentials is required for the staff/portal session cookies. It is
    # also why allow_origins must stay an explicit list — the browser refuses
    # "*" with credentials, and CORS was never a server-side control anyway.
    allow_origins=os.environ.get(
        "CORS_ALLOW_ORIGINS", "http://localhost:3010,http://localhost:3011"
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)  # Step 13 — RATE_LIMIT_PER_MINUTE to tune

app.include_router(portal_router)  # patient portal — separate trust zone


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Step 15 — one line per API request with status and duration. Health
    checks are skipped so pollers don't drown the log."""
    if request.url.path == "/api/health":
        return await call_next(request)

    started = time.monotonic()
    response = await call_next(request)
    logger.bind(component="api", status=response.status_code).info(
        "{method} {path} → {status} in {ms:.0f}ms",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        ms=(time.monotonic() - started) * 1000,
    )
    return response


@app.get("/api/health")
async def health() -> dict[str, str | bool]:
    # llm_available tells the caller whether new cases will be processed or
    # parked at 'received'. start.sh's readiness probe only checks "status".
    return {"status": "ok", "llm_available": llm_available()}


@app.post("/api/staff/session")
async def staff_session(response: Response, token: str = Body(embed=True)) -> dict[str, str]:
    """Exchange the staff token for an httpOnly cookie.

    Exists because EventSource cannot set an Authorization header, so the log
    viewer has no other way to authenticate. httpOnly means page scripts — and
    anything that manages to inject one — cannot read the token back out.
    """
    if not verify_staff_token(token):
        raise HTTPException(status_code=401, detail="Invalid staff token")
    response.set_cookie(
        STAFF_COOKIE,
        token,
        httponly=True,
        secure=os.environ.get("PORTAL_COOKIE_SECURE", "true").lower() != "false",
        samesite="strict",
        max_age=8 * 3600,
        path="/api",
    )
    return {"status": "ok"}


@app.get("/api/logs/tail", dependencies=[Depends(require_staff)])
async def tail_logs(lines: int = 100) -> list[dict]:
    """Step 16 — last N log records, for the viewer's initial backfill.

    Staff-only: the event log names case ids and agent decisions, and was
    previously readable by anyone who could reach this port."""
    path = newest_log_file()
    if path is None:
        return []
    return tail_records(path, min(max(lines, 1), 1000))


@app.get("/api/logs/stream", dependencies=[Depends(require_staff)])
async def stream_logs(request: Request) -> StreamingResponse:
    """Step 16 — follow the log file as Server-Sent Events: one `data:` line
    per log record, `tail -f` over HTTP. The /logs page connects an
    EventSource here. Survives rotation by re-checking which file is newest
    whenever the current one goes quiet."""

    async def follow():
        path = newest_log_file()
        position = path.stat().st_size if path else 0  # start at the end — tail is for backfill
        last_ping = time.monotonic()

        while not await request.is_disconnected():
            if path is None:
                await asyncio.sleep(1.0)
                path = newest_log_file()
                position = 0
                continue

            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(position)
                chunk = f.read()
                position = f.tell()

            for line in chunk.splitlines():
                record = parse_log_line(line)
                if record is not None:
                    yield f"data: {json.dumps(record)}\n\n"

            if not chunk:
                newest = newest_log_file()
                if newest != path:  # rotated to a new file
                    path, position = newest, 0
                else:
                    # A named ping event every ~10s of quiet: the viewer
                    # renders it as a heartbeat, proving the stream is alive
                    # even when no logs arrive — and it keeps proxies from
                    # timing out the idle socket.
                    if time.monotonic() - last_ping >= 10.0:
                        ping = {"time": datetime.now(timezone.utc).isoformat()}
                        yield f"event: ping\ndata: {json.dumps(ping)}\n\n"
                        last_ping = time.monotonic()
                    await asyncio.sleep(0.5)
            else:
                last_ping = time.monotonic()  # real lines are heartbeat enough

    return StreamingResponse(
        follow(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/fixtures", response_model=list[schemas.FixtureTemplateOut])
async def list_fixtures() -> list[schemas.FixtureTemplateOut]:
    return schemas.FIXTURE_TEMPLATE_OPTIONS


@app.post("/api/cases", response_model=schemas.CaseCreatedOut)
async def create_case(body: schemas.CreateCaseRequest) -> schemas.CaseCreatedOut:
    bus: EventBus = app.state.bus
    case = new_case_from_template(body.template)
    # dispatch=False parks the case at 'received' when the LLM is unavailable.
    await bus.publish(Event(event_type="case.created", case=case), dispatch=llm_available())

    with app.state.db.Session() as session:
        workflow.claim(session, case.case_id)  # uploader unknown: created from a fixture
        session.commit()

    with app.state.db.Session() as session:
        row = session.get(CaseRecord, case.case_id)
    return schemas.CaseCreatedOut(case_id=case.case_id, status=row.status)


def _case_from_ingest(body: schemas.IngestCaseRequest) -> TransitionCase:
    now = datetime.now(timezone.utc)
    return TransitionCase(
        case_id=body.case_id or f"case-{uuid4().hex[:8]}",
        patient_id=body.patient_id or f"patient-{uuid4().hex[:6]}",
        admitting_facility=body.admitting_facility,
        discharge_date=body.discharge_date,
        discharge_disposition=body.discharge_disposition,
        primary_diagnosis=body.primary_diagnosis,
        has_pcp_on_file=body.has_pcp_on_file,
        payer=body.payer,
        referral_specialty=body.referral_specialty,
        risk_flags=body.risk_flags,
        source=body.source,
        source_message_id=body.source_message_id or f"manual-ingest-{uuid4().hex[:8]}",
        received_at=body.received_at or now,
    )


MAX_BULK_CASES = 100


async def _ingest_bulk(
    items: list[schemas.IngestCaseRequest], uploaded_by: str = ""
) -> schemas.BulkIngestOut:
    """Array ingest: every item is validated and guardrail-checked up front —
    one bad row never sinks the batch. Accepted cases are queued and the
    pipeline runs them in the background (the LLM agents take seconds per
    case; a synchronous batch would time the request out), so the response
    returns immediately and progress is visible on the case list."""
    if not items:
        raise HTTPException(status_code=422, detail="Empty array — nothing to ingest")
    if len(items) > MAX_BULK_CASES:
        raise HTTPException(
            status_code=422, detail=f"Batch too large: {len(items)} > {MAX_BULK_CASES} cases"
        )

    results: list[schemas.BulkIngestItemOut] = []
    accepted: list[TransitionCase] = []
    seen_ids: set[str] = set()

    with app.state.db.Session() as session:
        for index, item in enumerate(items):
            case = _case_from_ingest(item)

            if case.case_id in seen_ids:
                results.append(schemas.BulkIngestItemOut(
                    index=index, case_id=case.case_id, accepted=False,
                    error=f"duplicate case_id '{case.case_id}' within this batch",
                ))
                continue
            if session.get(CaseRecord, case.case_id) is not None:
                results.append(schemas.BulkIngestItemOut(
                    index=index, case_id=case.case_id, accepted=False,
                    error=f"case '{case.case_id}' already exists",
                ))
                continue

            report = InputGuardrail().check(case)
            if not report.passed:
                results.append(schemas.BulkIngestItemOut(
                    index=index, case_id=case.case_id, accepted=False,
                    error=f"input guardrail: {'; '.join(report.violations)}",
                ))
                continue

            seen_ids.add(case.case_id)
            accepted.append(report.case)
            results.append(
                schemas.BulkIngestItemOut(index=index, case_id=case.case_id, accepted=True)
            )

    bus: EventBus = app.state.bus
    dispatch = llm_available()

    # Record every case in the workflow *before* the agents start. Without a row
    # the case is invisible to both panels and cannot be approved, so this runs
    # even when no uploader was supplied.
    with app.state.db.Session() as session:
        for case in accepted:
            workflow.claim(session, case.case_id, uploaded_by)
        session.commit()

    async def run_batch() -> None:
        # Sequential on purpose: the local LLM serializes requests anyway,
        # and one case at a time keeps the live log stream readable. With
        # dispatch=False every case is simply stored at 'received'.
        for case in accepted:
            await bus.publish(Event(event_type="case.created", case=case), dispatch=dispatch)

    task = asyncio.create_task(run_batch())
    app.state.bulk_tasks.add(task)
    task.add_done_callback(app.state.bulk_tasks.discard)

    logger.bind(component="ingest").info(
        "bulk ingest: {accepted}/{total} accepted, {rejected} rejected — pipeline running in background",
        accepted=len(accepted), total=len(items), rejected=len(items) - len(accepted),
    )
    return schemas.BulkIngestOut(
        total=len(items),
        accepted=len(accepted),
        rejected=len(items) - len(accepted),
        results=results,
    )


@app.post("/api/cases/ingest", response_model=schemas.CaseCreatedOut | schemas.BulkIngestOut)
async def ingest_case(
    body: schemas.IngestCaseRequest | list[schemas.IngestCaseRequest],
    uploaded_by: str = "",
) -> schemas.CaseCreatedOut | schemas.BulkIngestOut:
    """Manual front door to the EHR normalization layer. A single JSON
    object runs the pipeline synchronously and returns the final status;
    a JSON array is bulk mode — validated up front, processed in the
    background, per-item results returned immediately."""
    if isinstance(body, list):
        return await _ingest_bulk(body, uploaded_by)

    with app.state.db.Session() as session:
        if body.case_id and session.get(CaseRecord, body.case_id) is not None:
            raise HTTPException(status_code=409, detail=f"Case '{body.case_id}' already exists")

    case = _case_from_ingest(body)

    # Step 11 — input guardrail. Manual ingest is the one untrusted front
    # door, so hard violations reject here (the diagram's "Reject" branch)
    # and PII in free text is scrubbed before anything downstream sees it.
    report = InputGuardrail().check(case)
    if not report.passed:
        raise HTTPException(
            status_code=422,
            detail={"rejected_by": "input_guardrail", "violations": report.violations},
        )
    case = report.case

    bus: EventBus = app.state.bus
    await bus.publish(Event(event_type="case.created", case=case), dispatch=llm_available())

    with app.state.db.Session() as session:
        workflow.claim(session, case.case_id, uploaded_by)
        session.commit()

    with app.state.db.Session() as session:
        row = session.get(CaseRecord, case.case_id)
    return schemas.CaseCreatedOut(case_id=case.case_id, status=row.status)


@app.get("/api/cases", response_model=list[schemas.CaseListItemOut])
async def list_cases() -> list[schemas.CaseListItemOut]:
    with app.state.db.Session() as session:
        rows = session.query(CaseRecord).order_by(CaseRecord.updated_at.desc()).all()
        return [
            schemas.CaseListItemOut(
                case_id=row.case_id,
                patient_id=row.patient_id,
                status=row.status,
                primary_diagnosis=row.snapshot.get("primary_diagnosis", ""),
                discharge_disposition=row.snapshot.get("discharge_disposition", ""),
                payer=row.snapshot.get("payer", ""),
                updated_at=row.updated_at,
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Clinician workflow: doctor uploads -> agents -> admin approves -> patient sees
# ---------------------------------------------------------------------------


def _agents_ready(session, case_id: str) -> bool:
    decisions = session.query(AgentDecisionRecord).filter_by(case_id=case_id).all()
    return is_ready(decisions)


def _workflow_row_out(session, case_row: CaseRecord, wf) -> schemas.WorkflowCaseOut:
    ready = _agents_ready(session, case_row.case_id)
    return schemas.WorkflowCaseOut(
        case_id=case_row.case_id,
        patient_id=case_row.patient_id,
        primary_diagnosis=str(case_row.snapshot.get("primary_diagnosis", "")),
        stage=workflow.stage_of(wf, ready).value,
        case_status=case_row.status,
        uploaded_by=wf.uploaded_by if wf else "unknown",
        assigned_reviewer=wf.assigned_reviewer if wf else None,
        review_note=wf.review_note if wf else None,
        approved_by=wf.approved_by if wf else None,
        approved_at=wf.approved_at if wf else None,
        # Gated on approval, not on the column being populated — see WorkflowCaseOut.
        summary_text=wf.summary_text if wf and wf.approved_at else None,
        agents_ready=ready,
        updated_at=case_row.updated_at,
    )


@app.get("/api/workflow/queue", response_model=list[schemas.WorkflowCaseOut])
async def workflow_queue(role: str, username: str = "") -> list[schemas.WorkflowCaseOut]:
    """What this person must act on.

    admin  — everything not yet approved (they are the approval gate)
    doctor — cases they uploaded, plus anything routed back to them for review
    """
    if role not in ("admin", "doctor"):
        raise HTTPException(status_code=422, detail="role must be 'admin' or 'doctor'")

    with app.state.db.Session() as session:
        rows = session.query(CaseRecord).order_by(CaseRecord.updated_at.desc()).all()
        out: list[schemas.WorkflowCaseOut] = []
        for case_row in rows:
            wf = session.get(workflow.CaseWorkflow, case_row.case_id)
            if role == "doctor":
                # A doctor sees only what they uploaded or what was routed to them.
                if wf is None or username not in (wf.uploaded_by, wf.assigned_reviewer):
                    continue
            # An admin sees everything, including legacy cases with no workflow
            # row — they are exactly the ones that need attention.
            out.append(_workflow_row_out(session, case_row, wf))
    return out


@app.post("/api/workflow/{case_id}/approve", response_model=schemas.WorkflowActionOut)
async def approve_case(case_id: str, body: schemas.ApproveRequest) -> schemas.WorkflowActionOut:
    """Admin signs off. This — not the agents finishing — is what makes the
    summary visible to the patient."""
    actor = workflow.Actor(username=body.username, role=body.role)
    with app.state.db.Session() as session:
        try:
            wf = workflow.approve(session, case_id, actor, body.summary_text)
        except workflow.NotPermitted as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except workflow.WrongStage as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        case_row = session.get(CaseRecord, case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Case not found")
        case_row.status = CaseStatus.COMPLETED.value
        case_row.updated_at = datetime.now(timezone.utc)
        session.commit()
        stage = workflow.Stage.APPROVED.value

    app.state.audit.record(
        case_id=case_id,
        agent_id="workflow",
        input_summary=f"approved by {actor.username}",
        confidence=None,
        decision="approved",
        rationale="Discharge summary approved and released to the patient.",
        reviewer=actor.username,
    )
    return schemas.WorkflowActionOut(case_id=case_id, stage=stage, assigned_reviewer=None)


@app.post("/api/workflow/{case_id}/request-review", response_model=schemas.WorkflowActionOut)
async def request_doctor_review(
    case_id: str, body: schemas.RequestReviewRequest
) -> schemas.WorkflowActionOut:
    """Admin bounces the case back to a doctor. Defaults to the uploader — the
    "simple case, same doctor" path."""
    actor = workflow.Actor(username=body.username, role=body.role)
    with app.state.db.Session() as session:
        try:
            wf = workflow.request_review(session, case_id, actor, body.reviewer or "", body.note)
        except workflow.NotPermitted as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except workflow.WrongStage as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        reviewer = wf.assigned_reviewer
        session.commit()

    app.state.audit.record(
        case_id=case_id,
        agent_id="workflow",
        input_summary=f"review requested by {actor.username}",
        confidence=None,
        decision="review_requested",
        rationale=body.note or f"Routed to {reviewer} for clinical review.",
        reviewer=actor.username,
    )
    return schemas.WorkflowActionOut(
        case_id=case_id, stage=workflow.Stage.AWAITING_DOCTOR.value, assigned_reviewer=reviewer
    )


@app.post("/api/workflow/{case_id}/submit-review", response_model=schemas.WorkflowActionOut)
async def submit_doctor_review(
    case_id: str, body: schemas.SubmitReviewRequest
) -> schemas.WorkflowActionOut:
    """The doctor edits the draft and returns it to the admin for approval."""
    actor = workflow.Actor(username=body.username, role=body.role)
    with app.state.db.Session() as session:
        try:
            workflow.submit_review(session, case_id, actor, body.summary_text)
        except workflow.NotPermitted as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except workflow.WrongStage as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        session.commit()

    app.state.audit.record(
        case_id=case_id,
        agent_id="workflow",
        input_summary=f"doctor review submitted by {actor.username}",
        confidence=None,
        decision="review_submitted",
        rationale="Clinician edited the draft and returned it for approval.",
        reviewer=actor.username,
    )
    return schemas.WorkflowActionOut(
        case_id=case_id, stage=workflow.Stage.AWAITING_ADMIN.value, assigned_reviewer=None
    )


@app.get("/api/patients/search", response_model=list[schemas.PatientSearchResultOut])
async def search_patients(q: str = "", limit: int = 20) -> list[schemas.PatientSearchResultOut]:
    """Clinician patient lookup. Matches on patient_id or diagnosis, and returns
    one row per patient — their most recently updated case."""
    needle = q.strip().lower()
    with app.state.db.Session() as session:
        rows = (
            session.query(CaseRecord).order_by(CaseRecord.updated_at.desc()).all()
        )

    latest: dict[str, CaseRecord] = {}
    counts: dict[str, int] = {}
    for row in rows:
        diagnosis = str(row.snapshot.get("primary_diagnosis", ""))
        if needle and needle not in row.patient_id.lower() and needle not in diagnosis.lower():
            continue
        counts[row.patient_id] = counts.get(row.patient_id, 0) + 1
        latest.setdefault(row.patient_id, row)  # rows are newest-first

    return [
        schemas.PatientSearchResultOut(
            patient_id=pid,
            case_count=counts[pid],
            latest_case_id=row.case_id,
            latest_status=row.status,
            primary_diagnosis=str(row.snapshot.get("primary_diagnosis", "")),
            discharge_date=row.snapshot.get("discharge_date"),
            updated_at=row.updated_at,
        )
        for pid, row in list(latest.items())[: max(1, min(limit, 100))]
    ]


@app.get("/api/cases/{case_id}/draft", response_model=schemas.DraftSummaryOut)
async def case_draft(case_id: str) -> schemas.DraftSummaryOut:
    """The draft discharge summary a clinician reviews before approving.

    Deterministic — composed from what the agents already decided, not a fresh
    LLM call, so re-reading a case never changes the text under the reviewer.
    """
    with app.state.db.Session() as session:
        case_row = session.get(CaseRecord, case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Case not found")
        decisions = (
            session.query(AgentDecisionRecord)
            .filter_by(case_id=case_id)
            .order_by(AgentDecisionRecord.id)
            .all()
        )

    sections = build_sections(decisions)
    return schemas.DraftSummaryOut(
        case_id=case_id,
        patient_id=case_row.patient_id,
        status=case_row.status,
        ready=is_ready(decisions),
        draft=compose_draft(case_row.snapshot, sections),
        sections=[
            schemas.DraftSectionOut(
                agent_name=s.agent_name,
                heading=s.heading,
                body=s.body,
                confidence=s.confidence,
            )
            for s in sections
        ],
    )


@app.get("/api/cases/{case_id}", response_model=schemas.CaseDetailOut)
async def get_case(case_id: str) -> schemas.CaseDetailOut:
    with app.state.db.Session() as session:
        case_row = session.get(CaseRecord, case_id)
        if case_row is None:
            raise HTTPException(status_code=404, detail="Case not found")

        decisions = (
            session.query(AgentDecisionRecord).filter_by(case_id=case_id).order_by(AgentDecisionRecord.id).all()
        )
        events = session.query(EventRecord).filter_by(case_id=case_id).order_by(EventRecord.id).all()
        audit_rows = session.query(AuditLogRecord).filter_by(case_id=case_id).order_by(AuditLogRecord.id).all()

        gate: HumanReviewGate = app.state.gate
        pending = gate.pending.get(case_id)

        return schemas.CaseDetailOut(
            case=case_row.snapshot,
            agent_decisions=[schemas.AgentDecisionOut.model_validate(d) for d in decisions],
            events=[schemas.EventOut.model_validate(e) for e in events],
            audit=[schemas.AuditEntryOut.model_validate(a) for a in audit_rows],
            pending_review=pending.proposed_decision if pending else None,
        )


@app.get("/api/audit", response_model=list[schemas.AuditEntryOut])
async def list_audit(case_id: str | None = None) -> list[schemas.AuditEntryOut]:
    with app.state.db.Session() as session:
        query = session.query(AuditLogRecord)
        if case_id is not None:
            query = query.filter_by(case_id=case_id)
        rows = query.order_by(AuditLogRecord.id.desc()).all()
        return [schemas.AuditEntryOut.model_validate(row) for row in rows]


# Pipeline display order for the stats dashboard — name → stable agent ID.
AGENT_ROSTER = [
    (ReferralRoutingAgent.name, ReferralRoutingAgent.agent_id),
    (FollowUpSchedulingAgent.name, FollowUpSchedulingAgent.agent_id),
    (MedicationInstructionAgent.name, MedicationInstructionAgent.agent_id),
    (PatientOutreachAgent.name, PatientOutreachAgent.agent_id),
    (DischargeReadinessAgent.name, DischargeReadinessAgent.agent_id),
    (RiskEscalationAgent.name, RiskEscalationAgent.agent_id),
]

REVIEW_EVENT_TYPES = {
    "approved": "case.review_approved",
    "overridden": "case.review_overridden",
    "rejected": "case.review_rejected",
}


@app.get("/api/stats", response_model=schemas.StatsOut)
async def get_stats() -> schemas.StatsOut:
    """Aggregates for the stats dashboard, computed on demand from the same
    tables the pipeline already writes — no separate metrics store."""
    from sqlalchemy import func

    gate: HumanReviewGate = app.state.gate
    with app.state.db.Session() as session:
        status_rows = session.query(CaseRecord.status, func.count()).group_by(CaseRecord.status).all()
        cases_by_status = {status: count for status, count in status_rows}
        total_cases = sum(cases_by_status.values())

        closed = sum(
            cases_by_status.get(s, 0) for s in ("auto_completed", "completed", "rejected")
        )
        auto_complete_rate = (
            cases_by_status.get("auto_completed", 0) / closed if closed else None
        )

        avg_composite = (
            session.query(func.avg(AgentDecisionRecord.confidence))
            .filter(AgentDecisionRecord.agent_name == RiskEscalationAgent.name)
            .scalar()
        )

        avg_review_wait = (
            session.query(func.avg(EventRecord.duration_ms))
            .filter(EventRecord.produced_by == "human_review_gate")
            .scalar()
        )

        reviews = {
            label: session.query(func.count())
            .select_from(EventRecord)
            .filter(EventRecord.event_type == event_type)
            .scalar()
            for label, event_type in REVIEW_EVENT_TYPES.items()
        }

        decision_rows = session.query(
            AgentDecisionRecord.agent_name,
            func.count(),
            func.avg(AgentDecisionRecord.confidence),
        ).group_by(AgentDecisionRecord.agent_name).all()
        decisions_by_agent = {name: (count, avg) for name, count, avg in decision_rows}

        duration_rows = (
            session.query(EventRecord.produced_by, func.avg(EventRecord.duration_ms))
            .filter(EventRecord.produced_by.isnot(None))
            .group_by(EventRecord.produced_by)
            .all()
        )
        durations_by_agent = dict(duration_rows)

    agents = []
    for name, agent_id in AGENT_ROSTER:
        count, avg_conf = decisions_by_agent.get(name, (0, None))
        agents.append(
            schemas.AgentStatsOut(
                agent_name=name,
                agent_id=agent_id,
                decisions=count,
                avg_confidence=round(avg_conf, 3) if avg_conf is not None else None,
                avg_duration_ms=(
                    round(durations_by_agent[name], 1)
                    if durations_by_agent.get(name) is not None
                    else None
                ),
            )
        )

    return schemas.StatsOut(
        total_cases=total_cases,
        cases_by_status=cases_by_status,
        auto_complete_rate=round(auto_complete_rate, 4) if auto_complete_rate is not None else None,
        avg_composite_confidence=round(avg_composite, 3) if avg_composite is not None else None,
        pending_review=len(gate.pending),
        avg_review_wait_ms=round(avg_review_wait, 1) if avg_review_wait is not None else None,
        reviews=reviews,
        agents=agents,
    )


@app.post("/api/cases/{case_id}/review", response_model=schemas.ReviewResultOut)
async def review_case(case_id: str, body: schemas.ReviewRequest) -> schemas.ReviewResultOut:
    gate: HumanReviewGate = app.state.gate
    if case_id not in gate.pending:
        raise HTTPException(status_code=409, detail="Case is not pending review")

    event = await gate.act(case_id, ReviewStatus(body.action), reviewer=body.reviewer, note=body.note)
    return schemas.ReviewResultOut(case_id=case_id, event_type=event.event_type, status=event.case.status.value)
