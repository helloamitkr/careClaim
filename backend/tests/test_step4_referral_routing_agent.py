from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK


def test_decide_is_pure_and_deterministic():
    agent = ReferralRoutingAgent(EventBus())
    d1 = agent._decide(CASE_A_CLEAN)
    d2 = agent._decide(CASE_A_CLEAN)
    assert d1 == d2


def test_clean_case_gets_high_confidence():
    agent = ReferralRoutingAgent(EventBus())
    decision = agent._decide(CASE_A_CLEAN)
    assert decision.confidence >= 0.9


def test_confidence_ordering_matches_case_severity():
    agent = ReferralRoutingAgent(EventBus())
    conf_a = agent._decide(CASE_A_CLEAN).confidence
    conf_b = agent._decide(CASE_B_PAYER_DELAY).confidence
    conf_c = agent._decide(CASE_C_HIGH_RISK).confidence
    assert conf_a > conf_b > conf_c


async def test_agent_wired_to_bus_emits_referral_routed_event():
    bus = EventBus()
    agent = ReferralRoutingAgent(bus)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("referral.routed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert len(received) == 1
    assert received[0].payload["decision"]["agent_name"] == "referral_routing"
