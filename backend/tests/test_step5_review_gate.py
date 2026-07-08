import pytest

from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_C_HIGH_RISK
from carebridge.review_gate import HumanReviewGate, ReviewStatus, fake_human_reviewer


async def test_case_is_blocked_and_nothing_downstream_fires():
    bus = EventBus()
    gate = HumanReviewGate(bus)
    downstream_calls = []
    bus.subscribe("case.review_approved", lambda e: downstream_calls.append(e))

    await bus.publish(
        Event(
            event_type="case.needs_review",
            case=CASE_C_HIGH_RISK,
            payload={"decision": {"confidence": 0.25}},
        )
    )

    assert CASE_C_HIGH_RISK.case_id in gate.pending
    assert downstream_calls == []  # blocked — nothing re-published yet


async def test_human_action_republishes_as_new_event():
    bus = EventBus()
    gate = HumanReviewGate(bus)
    approved_events = []

    async def on_approved(event: Event) -> None:
        approved_events.append(event)

    bus.subscribe("case.review_approved", on_approved)
    await bus.publish(
        Event(
            event_type="case.needs_review",
            case=CASE_C_HIGH_RISK,
            payload={"decision": {"confidence": 0.25}},
        )
    )

    review = gate.pending[CASE_C_HIGH_RISK.case_id]
    status, note = fake_human_reviewer(review)
    await gate.act(CASE_C_HIGH_RISK.case_id, status, reviewer="demo-reviewer", note=note)

    assert CASE_C_HIGH_RISK.case_id not in gate.pending  # unblocked
    assert len(approved_events) == 1
    assert approved_events[0].payload["review"]["reviewer"] == "demo-reviewer"
    assert approved_events[0].payload["review"]["status"] == "approved"


async def test_acting_on_unknown_case_raises():
    bus = EventBus()
    gate = HumanReviewGate(bus)
    with pytest.raises(KeyError):
        await gate.act("no-such-case", ReviewStatus.APPROVED, reviewer="demo-reviewer")
