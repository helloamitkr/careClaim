"""Step 5 — the human review gate. A case that lands here is blocked: no
downstream agent sees it again until a human calls act(). The "human" is
faked with a hardcoded response for now (fake_human_reviewer) — the real
UI-driven version comes in Step 15."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from carebridge.bus import Event, EventBus
from carebridge.models import CaseStatus, TransitionCase


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    OVERRIDDEN = "overridden"
    REJECTED = "rejected"


class PendingReview(BaseModel):
    case_id: str
    case: TransitionCase
    proposed_decision: dict
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    reviewer_note: str | None = None
    held_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


_EVENT_TYPE_BY_STATUS = {
    ReviewStatus.APPROVED: "case.review_approved",
    ReviewStatus.OVERRIDDEN: "case.review_overridden",
    ReviewStatus.REJECTED: "case.review_rejected",
}

_CASE_STATUS_BY_REVIEW_STATUS = {
    ReviewStatus.APPROVED: CaseStatus.COMPLETED,
    ReviewStatus.OVERRIDDEN: CaseStatus.COMPLETED,
    ReviewStatus.REJECTED: CaseStatus.REJECTED,
}


class HumanReviewGate:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.pending: dict[str, PendingReview] = {}
        bus.subscribe("case.needs_review", self.hold)

    async def hold(self, event: Event) -> None:
        """Blocks the case: records it as pending and does nothing else.
        This is the point where the workbench queue would light up."""
        self.pending[event.case.case_id] = PendingReview(
            case_id=event.case.case_id,
            case=event.case,
            proposed_decision=event.payload.get("decision", {}),
        )

    async def act(
        self,
        case_id: str,
        action: ReviewStatus,
        reviewer: str,
        note: str | None = None,
    ) -> Event:
        """A human (or, for now, fake_human_reviewer) acts on a pending case.
        The decision re-enters the bus as its own event."""
        if action == ReviewStatus.PENDING:
            raise ValueError("act() requires a terminal status, not PENDING")

        review = self.pending.pop(case_id)
        wait_ms = (datetime.now(timezone.utc) - review.held_at).total_seconds() * 1000
        review.status = action
        review.reviewer = reviewer
        review.reviewer_note = note
        review.case = review.case.model_copy(
            update={
                "status": _CASE_STATUS_BY_REVIEW_STATUS[action],
                "updated_at": datetime.now(timezone.utc),
            }
        )

        if self.bus.audit is not None:
            self.bus.audit.record(
                case_id=case_id,
                agent_id="human_review_gate",
                input_summary=review.case.summary(),
                confidence=review.proposed_decision.get("confidence"),
                decision=action.value,
                rationale=note or "",
                reviewer=reviewer,
            )

        event = Event(
            event_type=_EVENT_TYPE_BY_STATUS[action],
            case=review.case,
            payload={"review": review.model_dump(mode="json")},
            produced_by="human_review_gate",
            duration_ms=wait_ms,
        )
        await self.bus.publish(event)
        return event


def fake_human_reviewer(review: PendingReview) -> tuple[ReviewStatus, str]:
    """Hardcoded stand-in for a care manager: approves everything sent to
    review, so the pipeline can be exercised end-to-end before there's a UI."""
    return ReviewStatus.APPROVED, "auto-approved by fake reviewer (no UI yet)"
