"""PII scrubbing. Shared by both checkpoints — the input guardrail scrubs the
case, the output guardrail scrubs the agent's rationale.

Deterministic regex, no model. A guardrail that called an LLM could hallucinate
its own judgement about whether something is an identifier.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Ordered so the most specific pattern wins (SSN before phone — both are digit
# runs, and a scrubbed SSN must not be half-eaten by the phone regex).
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("mrn", re.compile(r"\b(?:MRN|mrn)[:#\s]*\d{5,10}\b")),
]


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Redact PII from free text. Returns the scrubbed text plus the names of
    every pattern that fired, so callers can report what was removed without
    ever repeating the removed value."""
    found: list[str] = []
    for name, pattern in PII_PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        if count:
            found.append(name)
    return text, found
