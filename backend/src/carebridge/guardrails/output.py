"""The exit checkpoint. Runs inside every agent before its decision is recorded
or published.

Unlike the input guardrail, this one never rejects: by the time an agent has
produced a decision the work is already done, and throwing it away would lose a
clinical judgement over a formatting problem. Output problems are corrected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from carebridge.guardrails.pii import scrub_pii

if TYPE_CHECKING:
    # Type-only: agents.base imports this module at runtime, so importing it
    # back here would be circular.
    from carebridge.agents.base import AgentDecision


def apply_output_guardrail(decision: "AgentDecision") -> "AgentDecision":
    """Clamp confidence into [0, 1] (an LLM-backed agent can return junk), scrub
    PII out of the rationale, and replace an empty rationale so the audit trail
    never holds a blank explanation."""
    confidence = min(max(decision.confidence, 0.0), 1.0)
    rationale, _ = scrub_pii(decision.rationale)
    if not rationale.strip():
        rationale = f"{decision.agent_name} gave no rationale (replaced by output guardrail)"

    if confidence == decision.confidence and rationale == decision.rationale:
        return decision
    return decision.model_copy(update={"confidence": confidence, "rationale": rationale})
