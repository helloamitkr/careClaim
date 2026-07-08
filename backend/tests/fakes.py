"""Test doubles shared across agent tests."""

from __future__ import annotations

from typing import Callable


class FakeLLM:
    """Stands in for OllamaClient in unit tests — deterministic, no network."""

    def __init__(self, response: str | Callable[[str], str] = "") -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 200,
    ) -> str:
        self.calls.append(
            {"prompt": prompt, "system": system, "temperature": temperature, "max_tokens": max_tokens}
        )
        if callable(self.response):
            return self.response(prompt)
        return self.response
