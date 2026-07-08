"""The contract every vendor client honours, and the one log line they share.

`LLMClient` is a Protocol, not a base class or a union of concrete types. A
provider is anything with a `model`, a `generate()`, and an `is_reachable()` —
so a test fake satisfies it without importing anything from here, and adding a
fourth vendor does not mean editing a union that three agent modules import.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class LLMClient(Protocol):
    """`temperature` is part of the surface even though not every vendor honours
    it — Claude Opus 4.7+ rejects sampling parameters outright. A provider that
    cannot apply it must accept and ignore it rather than raise, so agent code
    never branches on which vendor is configured."""

    model: str

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 200,
    ) -> str: ...

    def is_reachable(self) -> bool: ...


class MissingCredentials(RuntimeError):
    """Raised at construction, not mid-case. A provider selected without its key
    should fail on the startup line that builds the pipeline, where the message
    is read, rather than inside a background task that swallows it."""


def log_round_trip(*, model: str, prompt: str, system: str | None, text: str, started: float) -> str:
    """DEBUG on purpose — one line per LLM round-trip is noise at INFO, but it's
    the first thing you want when case creation feels slow. Returns `text` so
    callers can `return log_round_trip(...)`."""
    logger.bind(component="llm", model=model).debug(
        "generate: {chars_in} chars in → {chars_out} chars out in {ms:.0f}ms",
        chars_in=len(prompt) + len(system or ""),
        chars_out=len(text),
        ms=(time.monotonic() - started) * 1000,
    )
    return text
