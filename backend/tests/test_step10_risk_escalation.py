from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.agents.followup_scheduling import FollowUpSchedulingAgent
from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.agents.referral_routing import ReferralRoutingAgent
from carebridge.agents.risk_escalation import RiskEscalationAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK
from carebridge.services.review_gate import HumanReviewGate, fake_human_reviewer
from carebridge.services.router import ConfidenceRouter
from tests.fakes import FakeLLM


def _wire_all_agents(bus: EventBus) -> None:
    ReferralRoutingAgent(bus)
    FollowUpSchedulingAgent(bus)
    MedicationInstructionAgent(bus, llm=FakeLLM("plain-language instructions"))
    PatientOutreachAgent(bus, llm=FakeLLM("a short friendly message"))
    DischargeReadinessAgent(bus, llm=FakeLLM("READY"))
    RiskEscalationAgent(bus)


async def test_composite_waits_for_all_five_agents():
    bus = EventBus()
    _wire_all_agents(bus)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("case.risk_assessed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert len(received) == 1  # exactly one composite per case, not five


async def test_clean_case_clears_with_no_weak_signals():
    bus = EventBus()
    _wire_all_agents(bus)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("case.risk_assessed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    composite = received[0].payload["decision"]
    assert composite["decision"] == "clear_for_auto_complete"
    assert composite["confidence"] >= 0.75


async def test_composite_confidence_is_the_weakest_agent_not_an_average():
    bus = EventBus()
    _wire_all_agents(bus)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("case.risk_assessed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_C_HIGH_RISK))

    composite = received[0].payload["decision"]
    # referral_routing scores 0.25 on case-C — the weakest of the five — so
    # the composite must equal that, not something averaged-up from it.
    assert composite["confidence"] == 0.25
    assert composite["decision"] == "escalate_for_review"
    assert "referral_routing" in composite["rationale"]


async def test_high_risk_case_names_multiple_weak_factors():
    bus = EventBus()
    _wire_all_agents(bus)  # discharge_readiness is stubbed to always say READY (0.90)
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("case.risk_assessed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_C_HIGH_RISK))

    rationale = received[0].payload["decision"]["rationale"]
    # referral_routing and patient_outreach both land below the weak-signal
    # threshold for case-C on their own deterministic logic — both named.
    assert "referral_routing" in rationale
    assert "patient_outreach" in rationale
    # discharge_readiness scored 0.90 here (stubbed LLM), so it should NOT
    # be blamed — the rationale should only name agents that actually dragged
    # the composite down.
    assert "discharge_readiness" not in rationale


async def test_full_pipeline_routes_on_composite_not_a_single_agent():
    bus = EventBus()
    _wire_all_agents(bus)
    ConfidenceRouter(bus, listens_to="case.risk_assessed", threshold=0.75)
    gate = HumanReviewGate(bus)

    auto_completed = []

    async def capture(event: Event) -> None:
        auto_completed.append(event)

    bus.subscribe("case.auto_completed", capture)

    for case in (CASE_A_CLEAN, CASE_B_PAYER_DELAY, CASE_C_HIGH_RISK):
        await bus.publish(Event(event_type="case.created", case=case))

    assert {e.case.case_id for e in auto_completed} == {CASE_A_CLEAN.case_id}
    assert set(gate.pending.keys()) == {CASE_B_PAYER_DELAY.case_id, CASE_C_HIGH_RISK.case_id}

    # clear the queue the same way earlier steps did, to prove the loop still closes
    for case_id in list(gate.pending.keys()):
        review = gate.pending[case_id]
        status, note = fake_human_reviewer(review)
        await gate.act(case_id, status, reviewer="demo-reviewer", note=note)

    assert gate.pending == {}
