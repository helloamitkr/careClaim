"""Step 9 — all five agents built so far (Referral Routing, Follow-Up
Scheduling, Medication Instruction, Patient Outreach, Discharge Readiness),
each independently wired to case.created, run against the 3 fixtures.

Note: only Referral Routing feeds the confidence router / human review gate
right now — combining every agent's output into one composite score is
Step 10's job (Risk & Escalation Agent), not this one.

Requires: ollama serve   (gemma3:4b pulled)
Run with: source venv/bin/activate && python demo_agents.py
"""

import asyncio

from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK


async def main() -> None:
    bus = EventBus()
    ReferralRoutingAgent(bus)
    FollowUpSchedulingAgent(bus)
    MedicationInstructionAgent(bus)
    PatientOutreachAgent(bus)
    DischargeReadinessAgent(bus)

    decisions_by_case: dict[str, list[tuple[str, dict]]] = {}

    async def capture(event: Event) -> None:
        decisions_by_case.setdefault(event.case.case_id, []).append(
            (event.event_type, event.payload["decision"])
        )

    for event_type in (
        "referral.routed",
        "followup.scheduled",
        "medication.instructions_ready",
        "outreach.attempted",
        "discharge.assessed",
    ):
        bus.subscribe(event_type, capture)

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        print("=" * 78)
        print(f"{case.case_id} — {case.summary()}")
        print("=" * 78)
        await bus.publish(Event(event_type="case.created", case=case))
        for event_type, decision in decisions_by_case[case.case_id]:
            print(f"\n  [{decision['agent_name']}] confidence={decision['confidence']}")
            print(f"    decision : {decision['decision']}")
            print(f"    rationale: {decision['rationale']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
