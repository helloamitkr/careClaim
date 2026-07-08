"""Runs the 3 fixture patients through the Steps 1-6 pipeline and prints what
happened to each, in plain language. This is the "demo something working"
checkpoint the build plan calls for after Step 6.

Run with:  source venv/bin/activate && python demo.py
"""

import asyncio

from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.review_gate import HumanReviewGate, fake_human_reviewer
from carebridge.router import ConfidenceRouter

OUTCOME_LABEL = {
    "case.auto_completed": "AUTO-COMPLETED (confidence at/above threshold)",
    "case.needs_review": "SENT TO HUMAN REVIEW GATE (confidence below threshold)",
}


async def main() -> None:
    bus = EventBus()
    ReferralRoutingAgent(bus)
    ConfidenceRouter(bus, listens_to="referral.routed", threshold=0.75)
    gate = HumanReviewGate(bus)

    outcomes: dict[str, str] = {}

    async def record_outcome(event: Event) -> None:
        outcomes[event.case.case_id] = event.event_type

    bus.subscribe("case.auto_completed", record_outcome)
    bus.subscribe("case.needs_review", record_outcome)

    print("=" * 78)
    print("CareBridge AI — Steps 1-6 walkthrough")
    print("=" * 78)

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        print(f"\n--- {case.case_id}: {case.summary()} ---")
        await bus.publish(Event(event_type="case.created", case=case))
        print(f"  -> {OUTCOME_LABEL[outcomes[case.case_id]]}")

    print("\n" + "=" * 78)
    print("Cases waiting at the human review gate:")
    print("=" * 78)
    for case_id, review in gate.pending.items():
        decision = review.proposed_decision
        print(f"\n  {case_id}")
        print(f"    proposed decision : {decision['decision']}")
        print(f"    confidence        : {decision['confidence']}")
        print(f"    rationale         : {decision['rationale']}")

    print("\n" + "=" * 78)
    print("Fake human (fake_human_reviewer) clears the queue:")
    print("=" * 78)
    for case_id in list(gate.pending.keys()):
        review = gate.pending[case_id]
        status, note = fake_human_reviewer(review)
        event = await gate.act(case_id, status, reviewer="demo-reviewer", note=note)
        print(f"\n  {case_id} -> {event.event_type} (reviewer note: \"{note}\")")


if __name__ == "__main__":
    asyncio.run(main())
