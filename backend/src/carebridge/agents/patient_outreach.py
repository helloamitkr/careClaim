"""Patient Outreach Agent — hybrid. Channel selection is deterministic Python
(the doc's phone -> SMS -> portal protocol, respecting patient language and
preferred channel, needs fields TransitionCase doesn't carry yet — so this
starts every case on the first channel in the sequence and stops after one
attempt until that data exists). Drafting the actual outreach message is
delegated to a local LLM via OllamaClient."""

from __future__ import annotations

from carebridge.agents.base import Agent, AgentDecision
from carebridge.bus import EventBus
from carebridge.llm import LLMClient, create_llm_client
from carebridge.models import TransitionCase
from carebridge.rag import KnowledgeBase, default_knowledge_base

CHANNEL_SEQUENCE = ["phone", "sms", "portal"]
MAX_ATTEMPTS = len(CHANNEL_SEQUENCE)

SYSTEM_PROMPT = (
    "You write short, warm outreach messages reminding patients about an "
    "upcoming follow-up appointment after a hospital stay. Exactly 2-3 "
    "sentences, friendly, no medical jargon. Output ONLY the message itself "
    "— no headers, no notes, no alternatives, no questions back to the caller."
)


class PatientOutreachAgent(Agent):
    name = "patient_outreach"
    agent_id = "AGT-OUT-004"
    listens_to = "case.created"
    emits = "outreach.attempted"

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
        channel = CHANNEL_SEQUENCE[0]
        attempt = 1

        # Step 14 — ground the draft in the approved template for this
        # diagnosis when one exists; the LLM personalizes, it doesn't invent.
        template = self.kb.match_diagnosis("outreach", case.primary_diagnosis)
        grounding = (
            f"Base your message on this approved template ({template.title}):\n"
            f"{template.content}\n"
            if template is not None
            else ""
        )
        prompt = (
            f"Patient is being discharged with a follow-up needed for: {case.primary_diagnosis}.\n"
            f"Referral specialty: {case.referral_specialty or 'a specialist'}.\n"
            f"{grounding}"
            "Draft the outreach message for attempt 1, sent by phone call script."
        )
        message = self.llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=100)

        confidence = 0.85
        if not case.has_pcp_on_file:
            confidence -= 0.15  # no PCP to loop in if outreach doesn't land
        if case.risk_flags:
            confidence -= 0.05 * len(case.risk_flags)
        confidence = max(0.0, min(1.0, confidence))

        return AgentDecision(
            agent_name=self.name,
            decision=f"outreach_attempted_via_{channel}",
            confidence=round(confidence, 2),
            rationale=(
                f"attempt {attempt}/{MAX_ATTEMPTS} via {channel}; "
                f"message drafted: {message}"
            ),
        )
