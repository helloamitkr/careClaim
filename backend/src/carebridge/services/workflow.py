"""Who owns a case right now, and what has been approved.

The clinical pipeline decides *what* a case needs. This module tracks *who must
act next* and *what text was signed off*:

    doctor uploads JSON
        -> agents run
        -> admin reviews the draft
             |- approve            -> summary published to patient + doctor
             `- request_review     -> back to a named doctor
                    -> doctor edits and resubmits -> admin reviews again

Two rules worth stating, because they are the whole point of the table:

  * `summary_text` is what a human approved, not what an agent generated. The
    draft is regenerated from agent decisions on every GET; the approved text is
    frozen the moment somebody signs it.
  * `approved_at IS NULL` means no patient sees anything. Patient visibility is
    a consequence of approval, never of the agents finishing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from carebridge.persistence import Base


class Stage(str, Enum):
    """Where the case sits in the human workflow, independent of CaseStatus
    (which tracks the *agent* pipeline)."""

    PROCESSING = "processing"          # agents still working
    AWAITING_ADMIN = "awaiting_admin"  # draft ready, admin must review
    AWAITING_DOCTOR = "awaiting_doctor"  # admin sent it back to a named doctor
    APPROVED = "approved"              # signed off; visible to patient + doctor


class CaseWorkflow(Base):
    __tablename__ = "case_workflow"

    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.case_id", ondelete="CASCADE"), primary_key=True
    )
    uploaded_by: Mapped[str] = mapped_column(String, nullable=False)
    # Set when an admin bounces the case back; cleared when the doctor resubmits.
    assigned_reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The frozen, human-approved narrative. NULL until somebody signs it.
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Actor:
    """No authentication: the UI supplies a name and a role. Recorded in the
    audit trail so an approval is at least attributable, even if not proven."""

    username: str
    role: str  # "doctor" | "admin"


class NotPermitted(RuntimeError):
    pass


class WrongStage(RuntimeError):
    pass


def stage_of(row: CaseWorkflow | None, agents_ready: bool) -> Stage:
    """Derived, never stored — a stored copy is one more thing to keep in sync."""
    if row is None:
        return Stage.PROCESSING
    if row.approved_at is not None:
        return Stage.APPROVED
    if row.assigned_reviewer is not None:
        return Stage.AWAITING_DOCTOR
    return Stage.AWAITING_ADMIN if agents_ready else Stage.PROCESSING


UNKNOWN_UPLOADER = "unknown"


def claim(session, case_id: str, uploaded_by: str = UNKNOWN_UPLOADER) -> CaseWorkflow:
    """Record who uploaded a case. Idempotent — a re-ingest keeps the original
    uploader, because that is who a review gets routed back to.

    Called for *every* ingest, including those with no uploader (the dashboard's
    ingest modal, fixtures, the bulk endpoint). A case with no workflow row is
    invisible to both the doctor and admin panels and cannot be approved, so the
    row must exist even when we don't know who to attribute it to."""
    row = session.get(CaseWorkflow, case_id)
    if row is None:
        row = CaseWorkflow(
            case_id=case_id,
            uploaded_by=uploaded_by or UNKNOWN_UPLOADER,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(row)
        session.flush()  # so a later session.get() in this transaction sees it
    return row


def approve(session, case_id: str, actor: Actor, summary_text: str) -> CaseWorkflow:
    if actor.role != "admin":
        raise NotPermitted("only an admin can approve a case")
    # Cases ingested before the workflow existed (or without an uploader) have
    # no row. Create one rather than refusing to approve them.
    row = claim(session, case_id)
    if row.approved_at is not None:
        raise WrongStage("case is already approved")

    row.summary_text = summary_text
    row.approved_by = actor.username
    row.approved_at = _now()
    row.assigned_reviewer = None
    row.updated_at = _now()
    return row


def request_review(
    session, case_id: str, actor: Actor, reviewer: str, note: str | None
) -> CaseWorkflow:
    """Admin bounces the case to a doctor. `reviewer` defaults to the uploader —
    the "simple case, same doctor" path."""
    if actor.role != "admin":
        raise NotPermitted("only an admin can request a doctor review")
    row = claim(session, case_id)
    if row.approved_at is not None:
        raise WrongStage("case is already approved")
    if not reviewer and row.uploaded_by == UNKNOWN_UPLOADER:
        raise WrongStage("no uploader on this case — name a reviewer explicitly")

    row.assigned_reviewer = reviewer or row.uploaded_by
    row.review_note = note
    row.updated_at = _now()
    return row


def submit_review(session, case_id: str, actor: Actor, summary_text: str) -> CaseWorkflow:
    """The doctor edits the draft and hands it back to the admin."""
    row = session.get(CaseWorkflow, case_id)
    if row is None:
        raise WrongStage("case has no workflow record")
    if row.approved_at is not None:
        raise WrongStage("case is already approved")
    if row.assigned_reviewer is None:
        raise WrongStage("case is not awaiting a doctor review")
    if actor.role != "doctor" or actor.username != row.assigned_reviewer:
        # A doctor may only act on a case actually routed to them. Without login
        # this is honour-system, but it still prevents accidental cross-editing.
        raise NotPermitted("case is assigned to a different reviewer")

    # Park the doctor's edit as the proposed text; the admin approves it.
    row.summary_text = summary_text
    row.assigned_reviewer = None
    row.updated_at = _now()
    return row
