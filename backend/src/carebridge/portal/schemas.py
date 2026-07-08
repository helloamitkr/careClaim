"""What a patient is allowed to see.

An allowlist, built field by field. The staff API returns `case_row.snapshot`
wholesale — convenient there, catastrophic here, because the next column anybody
adds to the case model would appear on a patient's screen unbidden.

Absent on purpose (§7 of PATIENT_PORTAL_DESIGN.md): confidence, rationale,
payer, admitting_facility, source, source_message_id, risk_flags, agent
decisions, events, audit rows, and the internal status string.

test_portal_security.py asserts that none of those names appear in a serialized
response — so this stays true after somebody edits it in a hurry.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from carebridge.portal.repository import PortalCaseRow

# Internal status leaks operational detail ("needs_review" tells a patient that
# a machine was unsure about them). Map it to something a person can act on.
#
# Note what is absent: no entry produces "ready". A patient's status is driven by
# clinician approval, not by the agent pipeline — see _status_for() below.
_PATIENT_STATUS = {
    "received": ("preparing", "We're preparing your care plan."),
    "in_progress": ("preparing", "We're preparing your care plan."),
    "needs_review": ("in_review", "A care coordinator is reviewing your plan."),
    "auto_completed": ("in_review", "A care coordinator is reviewing your plan."),
    "completed": ("in_review", "A care coordinator is reviewing your plan."),
    "rejected": ("contact_us", "Please contact your care team."),
}
_UNKNOWN = ("preparing", "We're preparing your care plan.")
_APPROVED = ("ready", "Your care plan is ready.")


def _status_for(internal_status: str, approved_at: datetime | None) -> tuple[str, str]:
    """Approval — not the agents finishing — is what makes a plan 'ready'.

    The agent pipeline marks a case `completed` / `auto_completed` the moment the
    agents agree, which happens long before a clinician has signed anything. If
    that drove the label, a patient would be told "Your care plan is ready" while
    the summary panel below it stayed empty, because `approved_summary` is NULL
    until an admin approves. The label must agree with the content.

    `rejected` still wins over approval: it means "contact your care team", which
    is true whether or not somebody had previously signed a summary.
    """
    if internal_status == "rejected":
        return _PATIENT_STATUS["rejected"]
    if approved_at is not None:
        return _APPROVED
    return _PATIENT_STATUS.get(internal_status, _UNKNOWN)


class PortalCaseOut(BaseModel):
    case_id: str
    status: str           # patient-facing, never the internal value
    status_message: str
    primary_diagnosis: str | None = None
    discharge_date: date | None = None
    discharge_disposition: str | None = None
    # The clinician-approved narrative. Absent until an admin signs the case off.
    summary: str | None = None
    approved_at: datetime | None = None
    last_updated: datetime

    @classmethod
    def from_row(cls, row: PortalCaseRow) -> "PortalCaseOut":
        status, message = _status_for(row.internal_status, row.approved_at)
        return cls(
            case_id=row.case_id,
            status=status,
            status_message=message,
            primary_diagnosis=row.primary_diagnosis,
            discharge_date=row.discharge_date,
            discharge_disposition=row.discharge_disposition,
            summary=row.approved_summary,
            approved_at=row.approved_at,
            last_updated=row.updated_at,
        )


class DevSignInRequest(BaseModel):
    """Dev sign-in: the username *is* the patient_id."""

    username: str


class LoginRequest(BaseModel):
    email: str


class EnrollRequest(BaseModel):
    token: str
    email: str


class LoginTokenRequest(BaseModel):
    token: str


class IssueEnrollmentRequest(BaseModel):
    patient_id: str
