"""What the assistant is allowed to know about one case.

Reads through `portal.portal_case_reason_view`, which is RLS-bound to the
session's patient (migration 006). Like `repository.py`, every function here
takes `session_patient_id` first, and it comes from the server-side session —
never from the request.

The rows this returns contain raw agent rationale. They must never be serialized
to a response. They exist only to be summarized by `answer.py`, whose output goes
through `redact.py` before a patient sees it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from carebridge.guardrails import scrub_pii
from carebridge.portal.repository import portal_engine, scoped_to_patient


@dataclass(frozen=True)
class Reason:
    agent_name: str
    decision: str
    confidence: float
    rationale: str


@dataclass(frozen=True)
class CaseContext:
    """Everything the assistant may reason over. Internal — never a response body."""

    case_id: str
    internal_status: str
    reasons: tuple[Reason, ...]

    @property
    def blockers(self) -> tuple[Reason, ...]:
        """The agents that were unsure. These are what a patient is asking about.

        0.75 is the ConfidenceRouter's escalation threshold — below it, a human
        is asked to look. Using the same number here means the assistant explains
        exactly the signals that caused the hold-up, not an arbitrary subset.
        """
        return tuple(r for r in self.reasons if r.confidence < 0.75)


def fetch_case_context(session_patient_id: str, case_id: str) -> CaseContext | None:
    """None both when the case does not exist and when it is somebody else's —
    the caller answers 404 for both, as in `repository.fetch_my_case`."""
    with portal_engine().begin() as conn:
        scoped_to_patient(conn, session_patient_id)
        rows = conn.execute(
            text(
                "SELECT internal_status, agent_name, decision, confidence, rationale "
                "FROM portal.portal_case_reason_view "
                "WHERE patient_id = :pid AND case_id = :cid "
                "ORDER BY confidence"
            ),
            {"pid": session_patient_id, "cid": case_id},
        ).all()

    if not rows:
        return None

    return CaseContext(
        case_id=case_id,
        internal_status=rows[0].internal_status,
        reasons=tuple(
            Reason(
                agent_name=r.agent_name,
                decision=r.decision,
                confidence=float(r.confidence),
                # The rationale is LLM-generated text derived from doctor-uploaded
                # JSON. Scrub before it enters a prompt: an MRN or phone number
                # that reached it must not be echoed back to the patient.
                rationale=scrub_pii(r.rationale)[0],
            )
            for r in rows
        ),
    )
