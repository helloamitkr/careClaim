"""Step 11 — the guardrails layer. Two checkpoints wrap the pipeline:

    guardrails/
      pii.py      scrub_pii — the shared redactor, used by both checkpoints
      input.py    InputGuardrail — the API front door; may reject (HTTP 422)
      output.py   apply_output_guardrail — inside every agent; corrects, never rejects
      report.py   GuardrailReport

Input runs *before* `case.created` is published; output runs before an agent's
decision is recorded. Both are deterministic regex/rule checks — no model in the
loop, so a guardrail can never hallucinate its own judgement.

This package replaced the single-file `guardrails.py`; the public names below are
the same, so existing `from carebridge.guardrails import ...` imports are
unaffected.
"""

from carebridge.guardrails.input import (
    MAX_FUTURE_DISCHARGE_DAYS,
    MAX_PAST_DISCHARGE_DAYS,
    InputGuardrail,
)
from carebridge.guardrails.output import apply_output_guardrail
from carebridge.guardrails.pii import PII_PATTERNS, REDACTED, scrub_pii
from carebridge.guardrails.report import GuardrailReport

__all__ = [
    "MAX_FUTURE_DISCHARGE_DAYS",
    "MAX_PAST_DISCHARGE_DAYS",
    "PII_PATTERNS",
    "REDACTED",
    "GuardrailReport",
    "InputGuardrail",
    "apply_output_guardrail",
    "scrub_pii",
]
