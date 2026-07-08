"""The shape every agent shares, whether _decide() is a lookup table or an
LLM call. The bus doesn't care what's inside the box."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from loguru import logger
from pydantic import BaseModel

from carebridge.bus import Event, EventBus
from carebridge.guardrails import apply_output_guardrail
from carebridge.models import TransitionCase


class AgentDecision(BaseModel):
    agent_name: str
    decision: str
    confidence: float  # 0.0-1.0
    rationale: str


class Agent(ABC):
    name: str
    agent_id: str  # stable ID stamped on every log line this agent produces
    listens_to: str  # event type this agent subscribes to
    emits: str  # event type this agent publishes once it decides

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(self.listens_to, self.run)

    @abstractmethod
    def _decide(self, case: TransitionCase) -> AgentDecision:
        """Case in, decision out. Deterministic agents keep this pure and
        I/O-free; hybrid/LLM agents call out to a model here instead —
        same shape either way, which is why the bus doesn't care."""

    async def run(self, event: Event) -> None:
        started = time.monotonic()
        decision = apply_output_guardrail(self._decide(event.case))
        duration_ms = (time.monotonic() - started) * 1000

        logger.bind(
            component="agent",
            agent_id=self.agent_id,
            agent=self.name,
            case_id=event.case.case_id,
            confidence=decision.confidence,
        ).info(
            f"[{self.agent_id}] {self.name} → {decision.decision} "
            f"(confidence {decision.confidence}) in {duration_ms:.0f}ms"
        )

        if self.bus.db is not None:
            self.bus.db.record_agent_decision(event.case.case_id, decision)
        if self.bus.audit is not None:
            self.bus.audit.record(
                case_id=event.case.case_id,
                agent_id=self.name,
                input_summary=event.case.summary(),
                confidence=decision.confidence,
                decision=decision.decision,
                rationale=decision.rationale,
            )
        await self.bus.publish(
            Event(
                event_type=self.emits,
                case=event.case,
                payload={"decision": decision.model_dump()},
                produced_by=self.name,
                duration_ms=duration_ms,
            )
        )
