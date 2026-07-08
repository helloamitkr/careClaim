"""The front-door checkpoint. Runs at the API before `case.created` is published.

Hard violations (impossible dates, blank diagnosis) reject the case outright —
the "Reject" branch of the architecture diagram, surfaced as an HTTP 422.

Free-text fields are scrubbed of PII rather than rejected: the clinical content
is still useful, the identifier inside it is not, and nothing downstream — agent
prompts, DB rows, audit log — should ever see it.
"""

from __future__ import annotations

from datetime import date, timedelta

from loguru import logger

from carebridge.guardrails.pii import scrub_pii
from carebridge.guardrails.report import GuardrailReport
from carebridge.models import TransitionCase

# How far in the future a discharge date may plausibly be. Transition-of-care
# cases arrive at or shortly before discharge; anything past this is a feed
# error, not a real patient.
MAX_FUTURE_DISCHARGE_DAYS = 30
# And anything discharged longer ago than this is too stale to action.
MAX_PAST_DISCHARGE_DAYS = 365


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
            # Pattern names only — logging the redacted values would leak the
            # very PII the guardrail just removed.
            logger.bind(component="guardrails", case_id=case.case_id).warning(
                "PII scrubbed: {redactions}", redactions=", ".join(redactions)
            )

        clean_case = case.model_copy(update=updates) if updates else case
        return GuardrailReport(passed=True, redactions=redactions, case=clean_case)
