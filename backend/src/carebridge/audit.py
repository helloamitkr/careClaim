"""Step 8 — the audit trail. Same underlying facts as Step 7's
agent_decisions rows, but modeled as its own append-only store: agent id,
input summary, confidence, decision, rationale, reviewer. Kept conceptually
separate from general event/case logging — even though it's the same
Postgres instance for now — because this is the record a compliance review
reads, not a debugging log. AuditTrail exposes only record(), never
update/delete."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from carebridge.persistence import Base

if TYPE_CHECKING:
    from carebridge.persistence import Database


class AuditLogRecord(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str | None] = mapped_column(String, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditTrail:
    def __init__(self, db: "Database") -> None:
        self.db = db

    def record(
        self,
        *,
        case_id: str,
        agent_id: str,
        input_summary: str,
        confidence: float | None,
        decision: str,
        rationale: str,
        reviewer: str | None = None,
    ) -> None:
        with self.db.Session() as session:
            session.add(
                AuditLogRecord(
                    case_id=case_id,
                    agent_id=agent_id,
                    input_summary=input_summary,
                    confidence=confidence,
                    decision=decision,
                    rationale=rationale,
                    reviewer=reviewer,
                    recorded_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
