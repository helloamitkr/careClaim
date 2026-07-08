"""Risk & Escalation Agent — the aggregator. Unlike every other agent, this
one doesn't map one case.created event to one decision: it fans in the
outputs of all five other agents for a case and combines them into a single
composite confidence score before the router ever sees it.

Composite confidence is the *minimum* across agents, not an average — one
agent flagging a real problem shouldn't be diluted away by four agents that
are fine. The rationale names the specific agent(s) that dragged the score
down, matching the doc's "names the specific factors when it escalates."
Because it aggregates rather than transforms a single input, it doesn't
subclass the base Agent — but its output is the same AgentDecision shape as
everything else."""

from __future__ import annotations

import time
from collections import defaultdict

from loguru import logger

from carebridge.agents.base import AgentDecision
from carebridge.bus import Event, EventBus
from carebridge.guardrails import apply_output_guardrail

WEAK_SIGNAL_THRESHOLD = 0.75

SOURCE_EVENT_TYPES = (
    "referral.routed",
    "followup.scheduled",
    "medication.instructions_ready",
    "outreach.attempted",
    "discharge.assessed",
)

# The five agents whose decisions feed the composite — used by crash
# recovery to tell source decisions apart from our own composite rows.
SOURCE_AGENT_NAMES = (
    "referral_routing",
    "followup_scheduling",
    "medication_instruction",
    "patient_outreach",
    "discharge_readiness",
)


class RiskEscalationAgent:
    name = "risk_escalation"
    agent_id = "AGT-RSK-006"
    emits = "case.risk_assessed"

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._collected: dict[str, dict[str, dict]] = defaultdict(dict)
        for event_type in SOURCE_EVENT_TYPES:
            bus.subscribe(event_type, self._on_agent_decision)

    async def _on_agent_decision(self, event: Event) -> None:
        case_id = event.case.case_id
        decision = event.payload["decision"]
        self._collected[case_id][decision["agent_name"]] = decision

        if len(self._collected[case_id]) < len(SOURCE_EVENT_TYPES):
            return  # still waiting on other agents for this case

        started = time.monotonic()
        decisions = self._collected.pop(case_id)
        composite = apply_output_guardrail(self._aggregate(decisions))
        duration_ms = (time.monotonic() - started) * 1000

        logger.bind(
            component="agent",
            agent_id=self.agent_id,
            agent=self.name,
            case_id=case_id,
            confidence=composite.confidence,
        ).info(
            f"[{self.agent_id}] {self.name} → {composite.decision} "
            f"(composite confidence {composite.confidence}) in {duration_ms:.0f}ms"
        )

        if self.bus.db is not None:
            self.bus.db.record_agent_decision(case_id, composite)
        if self.bus.audit is not None:
            self.bus.audit.record(
                case_id=case_id,
                agent_id=self.name,
                input_summary=event.case.summary(),
                confidence=composite.confidence,
                decision=composite.decision,
                rationale=composite.rationale,
            )

        await self.bus.publish(
            Event(
                event_type=self.emits,
                case=event.case,
                payload={"decision": composite.model_dump()},
                produced_by=self.name,
                duration_ms=duration_ms,
            )
        )

    async def recover_from_db(self) -> list[str]:
        """Step 12 — crash recovery. `_collected` lives only in memory, so a
        process restart strands any case that was mid-pipeline: its agent
        decisions are safely in Postgres but the composite never fired and
        the router never saw it. On startup this replays those cases from
        what the database already knows.

        A stranded case is one still in status 'received' — every case that
        made it to the router was moved past that status. For each one:
        - all five source decisions persisted → aggregate and publish the
          composite now, exactly as if the last event had just arrived;
        - fewer than five persisted → republish case.created so the agents
          re-run. Agents are deterministic given the same case, so the extra
          decision rows this writes are duplicates, not contradictions.

        Returns the recovered case_ids so the caller can log them."""
        db = self.bus.db
        if db is None:
            return []

        from carebridge.models import TransitionCase
        from carebridge.persistence import AgentDecisionRecord, CaseRecord

        complete: list[tuple[TransitionCase, dict[str, dict]]] = []
        rerun: list[TransitionCase] = []

        with db.Session() as session:
            stranded = session.query(CaseRecord).filter_by(status="received").all()
            for row in stranded:
                case = TransitionCase.model_validate(row.snapshot)
                rows = (
                    session.query(AgentDecisionRecord)
                    .filter_by(case_id=row.case_id)
                    .order_by(AgentDecisionRecord.id)
                    .all()
                )
                # Latest row per source agent wins, mirroring _collected.
                decisions = {
                    r.agent_name: {
                        "agent_name": r.agent_name,
                        "decision": r.decision,
                        "confidence": r.confidence,
                        "rationale": r.rationale,
                    }
                    for r in rows
                    if r.agent_name in SOURCE_AGENT_NAMES
                }
                if len(decisions) == len(SOURCE_AGENT_NAMES):
                    complete.append((case, decisions))
                else:
                    rerun.append(case)

        if complete or rerun:
            logger.bind(component="recovery").warning(
                "found {n} stranded case(s): {complete} recoverable from DB, {rerun} need re-run",
                n=len(complete) + len(rerun),
                complete=len(complete),
                rerun=len(rerun),
            )

        recovered: list[str] = []
        for case, decisions in complete:
            composite = apply_output_guardrail(self._aggregate(decisions))
            db.record_agent_decision(case.case_id, composite)
            if self.bus.audit is not None:
                self.bus.audit.record(
                    case_id=case.case_id,
                    agent_id=self.name,
                    input_summary=case.summary(),
                    confidence=composite.confidence,
                    decision=composite.decision,
                    rationale=f"[recovered after restart] {composite.rationale}",
                )
            await self.bus.publish(
                Event(
                    event_type=self.emits,
                    case=case,
                    payload={"decision": composite.model_dump()},
                    produced_by=self.name,
                )
            )
            recovered.append(case.case_id)

        for case in rerun:
            await self.bus.publish(Event(event_type="case.created", case=case))
            recovered.append(case.case_id)

        return recovered

    def _aggregate(self, decisions: dict[str, dict]) -> AgentDecision:
        ordered = sorted(decisions.values(), key=lambda d: d["confidence"])
        composite_confidence = ordered[0]["confidence"]
        weak_signals = [d for d in ordered if d["confidence"] < WEAK_SIGNAL_THRESHOLD]

        if weak_signals:
            factors = "; ".join(
                f"{d['agent_name']} ({d['confidence']}): {d['rationale']}" for d in weak_signals
            )
            decision = "escalate_for_review"
            rationale = f"composite confidence set by weakest signal(s) — {factors}"
        else:
            decision = "clear_for_auto_complete"
            rationale = (
                f"all {len(ordered)} agents reported confidence >= "
                f"{WEAK_SIGNAL_THRESHOLD} — no escalation factors"
            )

        return AgentDecision(
            agent_name=self.name,
            decision=decision,
            confidence=composite_confidence,
            rationale=rationale,
        )
