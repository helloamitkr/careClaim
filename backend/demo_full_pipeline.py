"""Step 10 — the full pipeline: all 5 agents -> Risk & Escalation composite
score -> confidence router -> auto-complete or human review gate. This is
the first point where the router acts on the whole case, not just one
agent's opinion.

Requires: ollama serve   (gemma3:4b pulled)
Run with: source venv/bin/activate && python demo_full_pipeline.py
"""

import asyncio

from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.agents.risk_escalation import RiskEscalationAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.review_gate import HumanReviewGate, fake_human_reviewer
from carebridge.router import ConfidenceRouter

OUTCOME_LABEL = {
    "case.auto_completed": "AUTO-COMPLETED",
    "case.needs_review": "SENT TO HUMAN REVIEW GATE",
}


async def main() -> None:
    bus = EventBus()
    ReferralRoutingAgent(bus)
    FollowUpSchedulingAgent(bus)
    MedicationInstructionAgent(bus)
    PatientOutreachAgent(bus)
    DischargeReadinessAgent(bus)
    RiskEscalationAgent(bus)
    ConfidenceRouter(bus, listens_to="case.risk_assessed", threshold=0.75)
    gate = HumanReviewGate(bus)

    outcomes: dict[str, tuple[str, dict]] = {}

    async def record_outcome(event: Event) -> None:
        outcomes[event.case.case_id] = (event.event_type, event.payload["decision"])

    bus.subscribe("case.auto_completed", record_outcome)
    bus.subscribe("case.needs_review", record_outcome)

    print("=" * 78)
    print("CareBridge AI — full pipeline: 5 agents -> composite score -> router")
    print("=" * 78)

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        print(f"\n--- {case.case_id}: {case.summary()} ---")
        await bus.publish(Event(event_type="case.created", case=case))
        event_type, composite = outcomes[case.case_id]
        print(f"  composite confidence : {composite['confidence']}")
        print(f"  composite rationale  : {composite['rationale']}")
        print(f"  -> {OUTCOME_LABEL[event_type]}")

    print("\n" + "=" * 78)
    print("Clearing the human review queue (fake_human_reviewer)")
    print("=" * 78)
    for case_id in list(gate.pending.keys()):
        review = gate.pending[case_id]
        status, note = fake_human_reviewer(review)
        event = await gate.act(case_id, status, reviewer="demo-reviewer", note=note)
        print(f"  {case_id} -> {event.event_type}")


if __name__ == "__main__":
    asyncio.run(main())
