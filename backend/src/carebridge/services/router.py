"""Step 6 — the confidence-threshold router. Sits between an agent's output
and the two possible outcomes: auto-complete (confidence at or above
threshold) or the human review gate (below threshold). This is the amber
diamond in the architecture diagram."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from carebridge.bus import Event, EventBus
from carebridge.models import CaseStatus

DEFAULT_CONFIDENCE_THRESHOLD = 0.75


class ConfidenceRouter:
    def __init__(
        self,
        bus: EventBus,
        listens_to: str,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.bus = bus
        self.threshold = threshold
        bus.subscribe(listens_to, self.route)

    async def route(self, event: Event) -> None:
        started = time.monotonic()
        decision = event.payload["decision"]
        confidence = decision["confidence"]

        if confidence >= self.threshold:
            event_type = "case.auto_completed"
            status = CaseStatus.AUTO_COMPLETED
        else:
            event_type = "case.needs_review"
            status = CaseStatus.NEEDS_REVIEW

        updated_case = event.case.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc)}
        )
        duration_ms = (time.monotonic() - started) * 1000
        await self.bus.publish(
            Event(
                event_type=event_type,
                case=updated_case,
                payload={"decision": decision},
                produced_by="confidence_router",
                duration_ms=duration_ms,
            )
        )
