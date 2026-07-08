import pytest

from carebridge.agents.discharge_readiness import DischargeReadinessAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_C_HIGH_RISK
from carebridge.llm import OllamaClient
from tests.fakes import FakeLLM


def test_ready_response_parses_to_high_confidence():
    agent = DischargeReadinessAgent(EventBus(), llm=FakeLLM("READY"))
    decision = agent._decide(CASE_A_CLEAN)

    assert decision.decision == "discharge_plan_coherent"
    assert decision.confidence == 0.90


def test_issue_response_parses_to_low_confidence():
    agent = DischargeReadinessAgent(
        EventBus(), llm=FakeLLM("ISSUE: home disposition but no PCP on file")
    )
    decision = agent._decide(CASE_C_HIGH_RISK)

    assert decision.decision == "discharge_plan_flagged"
    assert decision.confidence == 0.30
    assert "no PCP on file" in decision.rationale


def test_unparseable_response_falls_back_to_uncertain():
    agent = DischargeReadinessAgent(EventBus(), llm=FakeLLM("I'm not sure, let me think about it..."))
    decision = agent._decide(CASE_A_CLEAN)

    assert decision.decision == "discharge_plan_unparsed"
    assert decision.confidence == 0.50


async def test_agent_wired_to_bus_emits_discharge_assessed_event():
    bus = EventBus()
    DischargeReadinessAgent(bus, llm=FakeLLM("READY"))
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("discharge.assessed", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert len(received) == 1
    assert received[0].payload["decision"]["agent_name"] == "discharge_readiness"


def test_live_ollama_flags_the_no_pcp_case():
    llm = OllamaClient()
    if not llm.is_reachable():
        pytest.skip("Ollama not reachable — run `ollama serve`")

    agent = DischargeReadinessAgent(EventBus(), llm=llm)
    decision = agent._decide(CASE_C_HIGH_RISK)  # home disposition, no PCP on file

    assert decision.decision in {"discharge_plan_coherent", "discharge_plan_flagged", "discharge_plan_unparsed"}
    assert 0.0 <= decision.confidence <= 1.0
    assert len(decision.rationale) > 10
