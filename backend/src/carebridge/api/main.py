"""The FastAPI layer. Builds the whole pipeline once at startup — one shared
EventBus, one shared HumanReviewGate — and exposes it over REST. The
frontend never talks to agents or the bus directly, only this API.

Run with: uvicorn carebridge.api.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
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
from carebridge.logging_setup import (
    configure_logging,
    newest_log_file,
    parse_log_line,
    tail_records,
)
from carebridge.middleware import RateLimitMiddleware
from carebridge.models import TransitionCase
from carebridge.persistence import AgentDecisionRecord, CaseRecord, Database, EventRecord
from carebridge.rag import KnowledgeBase, PostgresKnowledgeBase, default_knowledge_base
from carebridge.review_gate import HumanReviewGate, ReviewStatus
from carebridge.router import ConfidenceRouter


def build_pipeline(db: Database) -> tuple[EventBus, HumanReviewGate, RiskEscalationAgent]:
    audit = AuditTrail(db)
    bus = EventBus(db=db, audit=audit)

    # Step 14 — RAG store. Postgres-backed when knowledegebase.sql has been
    # loaded; otherwise the in-memory seed keeps the pipeline fully working.
    kb: KnowledgeBase = PostgresKnowledgeBase(db)
    if not kb.is_available():
        logger.bind(component="rag").warning(
            "knowledge_base table not found — using in-memory seed "
            "(load knowledegebase.sql for the Postgres-backed store)"
        )
        kb = default_knowledge_base()

    ReferralRoutingAgent(bus, kb=kb)
    FollowUpSchedulingAgent(bus, kb=kb)
    MedicationInstructionAgent(bus, kb=kb)
    PatientOutreachAgent(bus, kb=kb)
    DischargeReadinessAgent(bus, kb=kb)
    risk_agent = RiskEscalationAgent(bus)
    ConfidenceRouter(bus, listens_to="case.risk_assessed", threshold=0.75)
    gate = HumanReviewGate(bus)

    return bus, gate, risk_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()  # Step 15 — console + rotating JSON file
    db = Database()
    db.init_schema()
    bus, gate, risk_agent = build_pipeline(db)

    # Step 12 — replay any case a previous process left mid-pipeline.
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
    yield


app = FastAPI(title="CareBridge AI API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3010"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)  # Step 13 — RATE_LIMIT_PER_MINUTE to tune


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
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/logs/tail")
async def tail_logs(lines: int = 100) -> list[dict]:
    """Step 16 — last N log records, for the viewer's initial backfill."""
    path = newest_log_file()
    if path is None:
        return []
    return tail_records(path, min(max(lines, 1), 1000))


@app.get("/api/logs/stream")
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
    await bus.publish(Event(event_type="case.created", case=case))

    with app.state.db.Session() as session:
        row = session.get(CaseRecord, case.case_id)
    return schemas.CaseCreatedOut(case_id=case.case_id, status=row.status)


@app.post("/api/cases/ingest", response_model=schemas.CaseCreatedOut)
async def ingest_case(body: schemas.IngestCaseRequest) -> schemas.CaseCreatedOut:
    """Manual front door to the EHR normalization layer — paste JSON in
    roughly TransitionCase shape and it runs through the same pipeline as
    a real feed would."""
    now = datetime.now(timezone.utc)
    case_id = body.case_id or f"case-{uuid4().hex[:8]}"

    with app.state.db.Session() as session:
        if session.get(CaseRecord, case_id) is not None:
            raise HTTPException(status_code=409, detail=f"Case '{case_id}' already exists")

    case = TransitionCase(
        case_id=case_id,
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
    await bus.publish(Event(event_type="case.created", case=case))

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
