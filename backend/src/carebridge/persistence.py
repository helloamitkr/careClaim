"""Step 7 — Postgres persistence. Every agent run and every state transition
gets written here: the `cases` table holds the latest snapshot of each case,
`events` is the full event log, `agent_decisions` is one row per agent run.

Local dev DB is started with `docker compose up -d` (see docker-compose.yml).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

if TYPE_CHECKING:
    from carebridge.agents.base import AgentDecision
    from carebridge.bus import Event
    from carebridge.models import TransitionCase

DEFAULT_DATABASE_URL = "postgresql+psycopg2://carebridge:carebridge@localhost:5432/carebridge"


class Base(DeclarativeBase):
    pass


class CaseRecord(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    produced_by: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class AgentDecisionRecord(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.engine = create_engine(url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL), future=True)
        self.Session = sessionmaker(bind=self.engine, future=True)

    def init_schema(self) -> None:
        from carebridge.audit import AuditLogRecord  # noqa: F401 — registers audit_log on Base

        Base.metadata.create_all(self.engine)

    def reset_schema(self) -> None:
        from carebridge.audit import AuditLogRecord  # noqa: F401 — registers audit_log on Base

        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)

    def upsert_case(self, case: "TransitionCase") -> None:
        now = datetime.now(timezone.utc)
        snapshot = case.model_dump(mode="json")
        with self.Session() as session:
            record = session.get(CaseRecord, case.case_id)
            if record is None:
                session.add(
                    CaseRecord(
                        case_id=case.case_id,
                        patient_id=case.patient_id,
                        status=case.status.value,
                        snapshot=snapshot,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                record.status = case.status.value
                record.snapshot = snapshot
                record.updated_at = now
            session.commit()

    def record_event(self, event: "Event") -> None:
        with self.Session() as session:
            session.add(
                EventRecord(
                    case_id=event.case.case_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    occurred_at=datetime.now(timezone.utc),
                    produced_by=event.produced_by,
                    duration_ms=event.duration_ms,
                )
            )
            session.commit()

    def record_agent_decision(self, case_id: str, decision: "AgentDecision") -> None:
        with self.Session() as session:
            session.add(
                AgentDecisionRecord(
                    case_id=case_id,
                    agent_name=decision.agent_name,
                    decision=decision.decision,
                    confidence=decision.confidence,
                    rationale=decision.rationale,
                    recorded_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
