from datetime import date

from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.models import DischargeDisposition


def test_known_specialty_gets_high_confidence_and_correct_date():
    agent = FollowUpSchedulingAgent(EventBus())
    decision = agent._decide(CASE_A_CLEAN)
    assert decision.confidence >= 0.85
    assert decision.decision == f"schedule_followup_{date(2026, 7, 6).isoformat()}"  # +5 days, endocrinology


def test_unknown_specialty_lowers_confidence():
    agent = FollowUpSchedulingAgent(EventBus())
    unknown_specialty_case = CASE_A_CLEAN.model_copy(update={"referral_specialty": "podiatry"})
    decision = agent._decide(unknown_specialty_case)
    assert decision.confidence < 0.7
    assert "no known scheduling lead time" in decision.rationale


def test_hospice_disposition_heavily_lowers_confidence():
    agent = FollowUpSchedulingAgent(EventBus())
    hospice_case = CASE_A_CLEAN.model_copy(update={"discharge_disposition": DischargeDisposition.HOSPICE})
    decision = agent._decide(hospice_case)
    assert decision.confidence < 0.5
    assert "hospice" in decision.rationale.lower()


def test_risk_flags_reduce_confidence_relative_to_clean_case():
    agent = FollowUpSchedulingAgent(EventBus())
    conf_a = agent._decide(CASE_A_CLEAN).confidence
    conf_c = agent._decide(CASE_C_HIGH_RISK).confidence
    assert conf_c < conf_a


async def test_agent_wired_to_bus_emits_followup_scheduled_event():
    bus = EventBus()
    FollowUpSchedulingAgent(bus)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("followup.scheduled", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_B_PAYER_DELAY))

    assert len(received) == 1
    assert received[0].payload["decision"]["agent_name"] == "followup_scheduling"
