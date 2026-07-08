"""Step 11 — the guardrails layer. Two checkpoints wrap the pipeline:

Input guardrail — runs at the API front door, *before* case.created is
published. Hard violations (impossible dates, blank diagnosis) reject the
case outright — the "Reject" branch of the architecture diagram, surfaced
as an HTTP 422. Free-text fields are scrubbed of PII (SSN, phone, email,
MRN) rather than rejected: the clinical content is still useful, the
identifier inside it is not, and nothing downstream — agent prompts, DB
rows, audit log — should ever see it.

Output guardrail — runs inside every agent before its decision is recorded
or published. It clamps confidence into [0, 1] (an LLM-backed agent can
return junk), scrubs PII out of the rationale, and replaces an empty
rationale so the audit trail never holds a blank explanation.

Both are deterministic regex/rule checks — no model in the loop, so a
guardrail can never hallucinate its own judgement."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from carebridge.models import TransitionCase

if TYPE_CHECKING:
    # Type-only: agents.base imports this module at runtime, so importing it
    # back here would be circular.
    from carebridge.agents.base import AgentDecision

# How far in the future a discharge date may plausibly be. Transition-of-care
# cases arrive at or shortly before discharge; anything past this is a feed
# error, not a real patient.
MAX_FUTURE_DISCHARGE_DAYS = 30
# And anything discharged longer ago than this is too stale to action.
MAX_PAST_DISCHARGE_DAYS = 365

REDACTED = "[REDACTED]"

# Ordered so the most specific pattern wins (SSN before phone — both are
# digit runs, and a scrubbed SSN must not be half-eaten by the phone regex).
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("mrn", re.compile(r"\b(?:MRN|mrn)[:#\s]*\d{5,10}\b")),
]


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Redact PII from free text. Returns the scrubbed text plus the names
    of every pattern that fired, so callers can report what was removed
    without ever repeating the removed value."""
    found: list[str] = []
    for name, pattern in PII_PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        if count:
            found.append(name)
    return text, found


class GuardrailReport(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)  # hard rejects
    redactions: list[str] = Field(default_factory=list)  # PII scrubbed, case kept
    case: TransitionCase | None = None  # the scrubbed case, when passed


class InputGuardrail:
    """Validates and scrubs a case before it enters the pipeline."""

    def check(self, case: TransitionCase) -> GuardrailReport:
        violations: list[str] = []
        today = date.today()

        if case.discharge_date > today + timedelta(days=MAX_FUTURE_DISCHARGE_DAYS):
            violations.append(
                f"discharge_date {case.discharge_date} is more than "
                f"{MAX_FUTURE_DISCHARGE_DAYS} days in the future"
            )
        if case.discharge_date < today - timedelta(days=MAX_PAST_DISCHARGE_DAYS):
            violations.append(
                f"discharge_date {case.discharge_date} is more than "
                f"{MAX_PAST_DISCHARGE_DAYS} days in the past"
            )
        if not case.primary_diagnosis.strip():
            violations.append("primary_diagnosis is blank")

        if violations:
            logger.bind(component="guardrails", case_id=case.case_id).warning(
                "input rejected: {violations}", violations="; ".join(violations)
            )
            return GuardrailReport(passed=False, violations=violations)

        redactions: list[str] = []
        updates: dict[str, object] = {}

        scrubbed, found = scrub_pii(case.primary_diagnosis)
        if found:
            updates["primary_diagnosis"] = scrubbed
            redactions.extend(f"primary_diagnosis:{name}" for name in found)

        if case.referral_specialty:
            scrubbed, found = scrub_pii(case.referral_specialty)
            if found:
                updates["referral_specialty"] = scrubbed
                redactions.extend(f"referral_specialty:{name}" for name in found)

        scrubbed_flags: list[str] = []
        flags_dirty = False
        for i, flag in enumerate(case.risk_flags):
            scrubbed, found = scrub_pii(flag)
            scrubbed_flags.append(scrubbed)
            if found:
                flags_dirty = True
                redactions.extend(f"risk_flags[{i}]:{name}" for name in found)
        if flags_dirty:
            updates["risk_flags"] = scrubbed_flags

        if redactions:
            # Pattern names only — logging the redacted values would leak
            # the very PII the guardrail just removed.
            logger.bind(component="guardrails", case_id=case.case_id).warning(
                "PII scrubbed: {redactions}", redactions=", ".join(redactions)
            )

        clean_case = case.model_copy(update=updates) if updates else case
        return GuardrailReport(passed=True, redactions=redactions, case=clean_case)


def apply_output_guardrail(decision: "AgentDecision") -> "AgentDecision":
    """Sanitize an agent's decision before it is recorded or published.
    Always returns a decision — output problems are corrected, not fatal,
    because by this point the agent has already done its work."""
    confidence = min(max(decision.confidence, 0.0), 1.0)
    rationale, _ = scrub_pii(decision.rationale)
    if not rationale.strip():
        rationale = f"{decision.agent_name} gave no rationale (replaced by output guardrail)"

    if confidence == decision.confidence and rationale == decision.rationale:
        return decision
    return decision.model_copy(update={"confidence": confidence, "rationale": rationale})
