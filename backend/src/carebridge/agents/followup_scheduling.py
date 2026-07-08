"""Follow-Up Scheduling Agent — pure Python, no LLM. Calendar math against
the knowledge base's 'followup' rules (retrieved by receiving specialty;
metadata.followup_days is the machine-actionable lead time); no real
calendar system wired in yet."""

from __future__ import annotations

from datetime import timedelta

from carebridge.agents.base import Agent, AgentDecision
from carebridge.bus import EventBus
from carebridge.models import DischargeDisposition, TransitionCase
from carebridge.rag import KnowledgeBase, default_knowledge_base

DEFAULT_LEAD_DAYS = 14


class FollowUpSchedulingAgent(Agent):
    name = "followup_scheduling"
    agent_id = "AGT-FUP-002"
    listens_to = "case.created"
    emits = "followup.scheduled"

    def __init__(self, bus: EventBus, kb: KnowledgeBase | None = None) -> None:
        super().__init__(bus)
        self.kb = kb or default_knowledge_base()

    def _decide(self, case: TransitionCase) -> AgentDecision:
        confidence = 0.90
        reasons: list[str] = []

        rule = self.kb.lookup_by_specialty("followup", case.referral_specialty or "")
        if rule is None or "followup_days" not in rule.metadata:
            confidence -= 0.35
            lead_days = DEFAULT_LEAD_DAYS
            reasons.append(
                f"no known scheduling lead time for specialty "
                f"'{case.referral_specialty or 'unspecified'}' — defaulting to {DEFAULT_LEAD_DAYS} days"
            )
        else:
            lead_days = int(rule.metadata["followup_days"])

        if case.discharge_disposition == DischargeDisposition.HOSPICE:
            confidence -= 0.50
            reasons.append("hospice disposition — standard follow-up scheduling may not apply")

        if case.risk_flags:
            confidence -= 0.05 * len(case.risk_flags)
            reasons.append(f"{len(case.risk_flags)} active risk flag(s) may require expedited scheduling")

        confidence = max(0.0, min(1.0, confidence))
        scheduled_date = case.discharge_date + timedelta(days=lead_days)

        if not reasons:
            reasons.append(
                f"booked standard {case.referral_specialty} follow-up {lead_days} days "
                f"post-discharge per '{rule.title}'"
            )

        return AgentDecision(
            agent_name=self.name,
            decision=f"schedule_followup_{scheduled_date.isoformat()}",
            confidence=round(confidence, 2),
            rationale="; ".join(reasons),
        )
