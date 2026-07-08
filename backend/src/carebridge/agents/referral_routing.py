"""Referral Routing Agent — pure Python, no LLM. Rule-based payer + network +
specialty lookup, exactly the kind of deterministic work that shouldn't cost
an LLM call. Since Step 14 the network/specialty knowledge comes from the
knowledge base ('insurance' category, payer:specialty keys with payer:*
wildcards) instead of hardcoded sets."""

from __future__ import annotations

from carebridge.agents.base import Agent, AgentDecision
from carebridge.bus import EventBus
from carebridge.models import TransitionCase
from carebridge.services.rag import KnowledgeBase, default_knowledge_base


class ReferralRoutingAgent(Agent):
    name = "referral_routing"
    agent_id = "AGT-REF-001"
    listens_to = "case.created"
    emits = "referral.routed"

    def __init__(self, bus: EventBus, kb: KnowledgeBase | None = None) -> None:
        super().__init__(bus)
        self.kb = kb or default_knowledge_base()

    def _decide(self, case: TransitionCase) -> AgentDecision:
        confidence = 0.95
        reasons: list[str] = []

        network = self.kb.lookup_insurance(case.payer, case.referral_specialty or "")
        if network is None:
            confidence -= 0.30
            reasons.append(
                f"payer '{case.payer}' is not in the known network — cannot "
                "confirm an in-network provider automatically"
            )
        elif network.metadata.get("prior_auth_required"):
            reasons.append(f"in-network per {network.title}, but prior authorization is required")

        if not case.referral_specialty or not self.kb.known_specialty(case.referral_specialty):
            confidence -= 0.30
            reasons.append("referral specialty missing or not recognized")

        if not case.has_pcp_on_file:
            confidence -= 0.40
            reasons.append("no PCP on file to route the follow-up to")

        if case.risk_flags:
            confidence -= 0.10 * len(case.risk_flags)
            reasons.append(
                f"{len(case.risk_flags)} active risk flag(s): {', '.join(case.risk_flags)}"
            )

        confidence = max(0.0, min(1.0, confidence))

        if not reasons:
            reasons.append(
                f"payer and specialty confirmed in-network per {network.title}, PCP on "
                f"file, no risk flags — routed to in-network "
                f"{case.referral_specialty} provider"
            )

        specialty = case.referral_specialty or "unspecified"
        decision = f"route_to_in_network_{specialty}_provider"

        return AgentDecision(
            agent_name=self.name,
            decision=decision,
            confidence=round(confidence, 2),
            rationale="; ".join(reasons),
        )
