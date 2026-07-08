"""What a guardrail hands back to its caller."""

from __future__ import annotations

from pydantic import BaseModel, Field

from carebridge.models import TransitionCase


class GuardrailReport(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)  # hard rejects
    redactions: list[str] = Field(default_factory=list)  # PII scrubbed, case kept
    case: TransitionCase | None = None  # the scrubbed case, when passed
