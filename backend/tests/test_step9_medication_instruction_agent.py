import pytest

from carebridge.agents.medication_instruction import MedicationInstructionAgent
from carebridge.bus import Event, EventBus
from carebridge.fixtures import CASE_A_CLEAN, CASE_C_HIGH_RISK
from carebridge.llm import OllamaClient
from tests.fakes import FakeLLM


def test_unmatched_diagnosis_skips_the_llm_entirely():
    llm = FakeLLM("should never be called")
    agent = MedicationInstructionAgent(EventBus(), llm=llm)
    unmatched = CASE_A_CLEAN.model_copy(update={"primary_diagnosis": "unclassified condition"})

    decision = agent._decide(unmatched)

    assert llm.calls == []
    assert decision.confidence == 0.30
    assert decision.decision == "medication_instructions_unavailable"


def test_matched_diagnosis_calls_llm_and_uses_its_wording():
    llm = FakeLLM("Take your metformin twice a day with meals and check your sugar each morning.")
    agent = MedicationInstructionAgent(EventBus(), llm=llm)

    decision = agent._decide(CASE_A_CLEAN)  # "Type 2 diabetes, controlled"

    assert len(llm.calls) == 1
    assert decision.confidence == 0.90
    assert decision.decision == "medication_instructions_generated"
    assert "metformin" in decision.rationale.lower()


def test_risk_flags_lower_confidence_on_matched_regimen():
    llm = FakeLLM("plain language instructions")
    agent = MedicationInstructionAgent(EventBus(), llm=llm)

    decision = agent._decide(CASE_C_HIGH_RISK)  # "heart failure" + 3 risk flags

    assert decision.confidence == pytest.approx(0.90 - 0.05 * 3)


async def test_agent_wired_to_bus_emits_medication_event():
    bus = EventBus()
    MedicationInstructionAgent(bus, llm=FakeLLM("instructions"))
    received = []

    async def capture(event: Event) -> None:
        received.append(event)

    bus.subscribe("medication.instructions_ready", capture)
    await bus.publish(Event(event_type="case.created", case=CASE_A_CLEAN))

    assert len(received) == 1
    assert received[0].payload["decision"]["agent_name"] == "medication_instruction"


def test_live_ollama_generates_a_plausible_decision():
    llm = OllamaClient()
    if not llm.is_reachable():
        pytest.skip("Ollama not reachable — run `ollama serve`")

    agent = MedicationInstructionAgent(EventBus(), llm=llm)
    decision = agent._decide(CASE_A_CLEAN)

    assert decision.decision == "medication_instructions_generated"
    assert 0.0 <= decision.confidence <= 1.0
    assert len(decision.rationale) > 20
