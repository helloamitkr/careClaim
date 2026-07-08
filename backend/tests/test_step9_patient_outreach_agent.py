import pytest

from carebridge.agents.patient_outreach import PatientOutreachAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_C_HIGH_RISK
from carebridge.llm import OllamaClient
from tests.fakes import FakeLLM


def test_first_attempt_always_uses_phone():
    llm = FakeLLM("Hi, just calling to remind you about your upcoming appointment.")
    agent = PatientOutreachAgent(EventBus(), llm=llm)

    decision = agent._decide(CASE_A_CLEAN)

    assert len(llm.calls) == 1
    assert decision.decision == "outreach_attempted_via_phone"
    assert "attempt 1/3" in decision.rationale


def test_no_pcp_and_risk_flags_lower_confidence():
    llm = FakeLLM("message")
    agent = PatientOutreachAgent(EventBus(), llm=llm)

    clean_confidence = agent._decide(CASE_A_CLEAN).confidence
    risky_confidence = agent._decide(CASE_C_HIGH_RISK).confidence  # no PCP + 3 risk flags

    assert risky_confidence < clean_confidence


async def test_agent_wired_to_bus_emits_outreach_event():
    bus = EventBus()
    PatientOutreachAgent(bus, llm=FakeLLM("message"))
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("outreach.attempted", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert len(received) == 1
    assert received[0].payload["decision"]["agent_name"] == "patient_outreach"


def test_live_ollama_drafts_a_plausible_message():
    llm = OllamaClient()
    if not llm.is_reachable():
        pytest.skip("Ollama not reachable — run `ollama serve`")

    agent = PatientOutreachAgent(EventBus(), llm=llm)
    decision = agent._decide(CASE_A_CLEAN)

    assert decision.decision == "outreach_attempted_via_phone"
    assert 0.0 <= decision.confidence <= 1.0
    assert len(decision.rationale) > 20
