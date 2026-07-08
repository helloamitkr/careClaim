"""Discharge Readiness Agent — the one pure-LLM agent. Reviews the case for
coherence problems the doc calls out directly (e.g. "home" disposition with
no PCP on file). The model is asked to reply in a single constrained line so
a 4B local model's output stays reliably parseable without needing JSON."""

from __future__ import annotations

from carebridge.agents.base import Agent, AgentDecision
from carebridge.bus import EventBus
from carebridge.llm import OllamaClient
from carebridge.models import TransitionCase
from carebridge.rag import KnowledgeBase, default_knowledge_base

SYSTEM_PROMPT = (
    "You are a discharge-readiness checker for a hospital transition-of-care system. "
    "Given a patient case summary, decide if the discharge plan is internally coherent "
    "and safe. When a hospital discharge policy is provided, check the plan against it. "
    "Reply with exactly one line, no other text:\n"
    "READY — if the plan is coherent.\n"
    "ISSUE: <one short sentence> — if you spot a problem (e.g. home disposition with no "
    "PCP on file, or a mismatch between diagnosis severity and follow-up urgency)."
)


class DischargeReadinessAgent(Agent):
    name = "discharge_readiness"
    agent_id = "AGT-RDY-005"
    listens_to = "case.created"
    emits = "discharge.assessed"

    def __init__(
        self,
        bus: EventBus,
        llm: OllamaClient | None = None,
        kb: KnowledgeBase | None = None,
    ) -> None:
        super().__init__(bus)
        self.llm = llm or OllamaClient()
        self.kb = kb or default_knowledge_base()

    def _decide(self, case: TransitionCase) -> AgentDecision:
        # Step 14 — retrieve the hospital's discharge policy for this
        # diagnosis so the LLM judges against real criteria, not vibes.
        policy = self.kb.match_diagnosis("policy", case.primary_diagnosis)
        policy_line = (
            f"Hospital discharge policy ({policy.title}): {policy.content}\n"
            if policy is not None
            else ""
        )
        prompt = (
            f"Discharge disposition: {case.discharge_disposition.value}\n"
            f"Primary diagnosis: {case.primary_diagnosis}\n"
            f"PCP on file: {'yes' if case.has_pcp_on_file else 'no'}\n"
            f"Referral specialty: {case.referral_specialty or 'none'}\n"
            f"Risk flags: {', '.join(case.risk_flags) or 'none'}\n"
            f"{policy_line}"
        )
        raw = self.llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=60)
        first_line = raw.strip().splitlines()[0].strip() if raw.strip() else ""

        if first_line.upper().startswith("READY"):
            return AgentDecision(
                agent_name=self.name,
                decision="discharge_plan_coherent",
                confidence=0.90,
                rationale=f"LLM discharge-readiness check: {raw.strip()}",
            )

        if first_line.upper().startswith("ISSUE"):
            return AgentDecision(
                agent_name=self.name,
                decision="discharge_plan_flagged",
                confidence=0.30,
                rationale=f"LLM discharge-readiness check: {raw.strip()}",
            )

        return AgentDecision(
            agent_name=self.name,
            decision="discharge_plan_unparsed",
            confidence=0.50,
            rationale=f"LLM response did not match the expected format, treating as uncertain: {raw.strip()!r}",
        )
