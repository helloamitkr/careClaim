from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.services.review_gate import HumanReviewGate
from carebridge.services.router import ConfidenceRouter


def _build_pipeline() -> tuple[EventBus, HumanReviewGate, list[Event]]:
    bus = EventBus()
    ReferralRoutingAgent(bus)
    ConfidenceRouter(bus, listens_to="referral.routed", threshold=0.75)
    gate = HumanReviewGate(bus)

    auto_completed: list[Event] = []

    async def capture(event: Event) -> None:
        auto_completed.append(event)

    bus.subscribe("case.auto_completed", capture)
    return bus, gate, auto_completed


async def test_clean_case_auto_completes():
    bus, gate, auto_completed = _build_pipeline()
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert len(auto_completed) == 1
    assert CASE_A_CLEAN.case_id not in gate.pending


async def test_payer_delay_case_goes_to_review():
    bus, gate, auto_completed = _build_pipeline()
    await bus.publish(Event(event_type="case.created", case=CASE_B_PAYER_DELAY))

    assert auto_completed == []
    assert CASE_B_PAYER_DELAY.case_id in gate.pending


async def test_high_risk_case_goes_to_review():
    bus, gate, auto_completed = _build_pipeline()
    await bus.publish(Event(event_type="case.created", case=CASE_C_HIGH_RISK))

    assert auto_completed == []
    assert CASE_C_HIGH_RISK.case_id in gate.pending


async def test_all_three_fixtures_take_the_expected_path_on_one_bus():
    bus, gate, auto_completed = _build_pipeline()

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        await bus.publish(Event(event_type="case.created", case=case))

    assert {e.case.case_id for e in auto_completed} == {CASE_A_CLEAN.case_id}
    assert set(gate.pending.keys()) == {CASE_B_PAYER_DELAY.case_id, CASE_C_HIGH_RISK.case_id}
