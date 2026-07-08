"""Step 3 — asyncio pub-sub event bus. No agents plugged in yet: this module
only proves that an event can be published and received by a subscriber."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger
from pydantic import BaseModel, Field

from carebridge.models import TransitionCase

if TYPE_CHECKING:
    from carebridge.audit import AuditTrail
    from carebridge.persistence import Database

EventHandler = Callable[["Event"], Awaitable[None]]


class Event(BaseModel):
    """Something that happened to a case, dispatched to whoever subscribes."""

    event_type: str  # e.g. "case.created", "referral.routed", "case.needs_review"
    case: TransitionCase
    payload: dict[str, Any] = Field(default_factory=dict)

    # Trace metadata — who produced this event and how long they took to
    # decide, so the case detail page can render a request/response
    # waterfall across agents. None for events with no clear single producer
    # (e.g. the case.created that kicks the whole pipeline off).
    produced_by: str | None = None
    duration_ms: float | None = None


class EventBus:
    """In-process asyncio pub-sub. Swappable for Temporal/Kafka later without
    changing agent code, since agents only ever talk to this interface."""

    def __init__(self, db: "Database | None" = None, audit: "AuditTrail | None" = None) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self.history: list[Event] = []
        self.db = db
        self.audit = audit

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: Event, *, dispatch: bool = True) -> None:
        """dispatch=False persists the event and its case but skips the
        subscribers — used to park a case at 'received' when the LLM is
        unavailable, so no agent half-processes it."""
        self.history.append(event)
        # Step 15 — the one log line that sees everything: every agent
        # decision, routing verdict, and review action is an event.
        by = f" by {event.produced_by}" if event.produced_by else ""
        ms = f" in {event.duration_ms:.0f}ms" if event.duration_ms is not None else ""
        logger.bind(
            component="bus",
            case_id=event.case.case_id,
            event_type=event.event_type,
            produced_by=event.produced_by,
            duration_ms=event.duration_ms,
        ).info(f"{event.event_type} · case {event.case.case_id}{by}{ms}")
        if self.db is not None:
            self.db.upsert_case(event.case)
            self.db.record_event(event)
        if not dispatch:
            return
        handlers = self._subscribers.get(event.event_type, [])
        await asyncio.gather(*(handler(event) for handler in handlers))
