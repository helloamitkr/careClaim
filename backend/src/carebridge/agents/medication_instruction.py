"""Medication Instruction Agent — hybrid. The regimen lookup is retrieval
from the knowledge base ('medication' category, matched against the case's
diagnosis); the plain-language rewrite for the patient is delegated to a
local LLM via OllamaClient. Confidence is decided by Python, never by the
model — the LLM only rephrases the retrieved regimen, it cannot invent one."""

from __future__ import annotations

from carebridge.agents.base import Agent, AgentDecision
from carebridge.bus import EventBus
from carebridge.llm import LLMClient, create_llm_client
from carebridge.models import TransitionCase
from carebridge.services.rag import KnowledgeBase, default_knowledge_base

SYSTEM_PROMPT = (
    "You write short, plain-language medication instructions for patients leaving "
    "the hospital. Use simple words, no medical jargon, 2-4 sentences, plain prose "
    "with no headers or markdown. Do not add medications that weren't given to you. "
    "Output ONLY the instructions themselves — no notes, no questions back to the caller."
)


class MedicationInstructionAgent(Agent):
    name = "medication_instruction"
    agent_id = "AGT-MED-003"
    listens_to = "case.created"
    emits = "medication.instructions_ready"

    def __init__(
        self,
        bus: EventBus,
        llm: LLMClient | None = None,
        kb: KnowledgeBase | None = None,
    ) -> None:
        super().__init__(bus)
        self.llm = llm or create_llm_client()
        self.kb = kb or default_knowledge_base()

    def _decide(self, case: TransitionCase) -> AgentDecision:
        regimen = self.kb.match_diagnosis("medication", case.primary_diagnosis)

        if regimen is None:
            return AgentDecision(
                agent_name=self.name,
                decision="medication_instructions_unavailable",
                confidence=0.30,
                rationale=(
                    f"no known medication regimen matched diagnosis "
                    f"'{case.primary_diagnosis}' — needs manual review"
                ),
            )

        prompt = (
            f"Patient diagnosis: {case.primary_diagnosis}\n"
            f"Prescribed regimen ({regimen.title}):\n{regimen.content}\n\n"
            "Rewrite this as plain-language instructions the patient can follow at home."
        )
        plain_language = self.llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=120)

        confidence = 0.90
        if case.risk_flags:
            confidence -= 0.05 * len(case.risk_flags)
        confidence = max(0.0, min(1.0, confidence))

        return AgentDecision(
            agent_name=self.name,
            decision="medication_instructions_generated",
            confidence=round(confidence, 2),
            rationale=(
                f"matched regimen '{regimen.title}' for '{case.primary_diagnosis}'; "
                f"plain-language instructions: {plain_language}"
            ),
        )
