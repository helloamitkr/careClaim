"""Thin client for the local LLM the hybrid and LLM-based agents call into
(Medication Instruction, Patient Outreach, Discharge Readiness). Runs against
a local Ollama server — no cloud API key needed. Start it with `ollama serve`
and make sure the model below is pulled (`ollama pull gemma3:4b`)."""

from __future__ import annotations

import os
import time

import requests
from loguru import logger

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = base_url or os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 200,
    ) -> str:
        started = time.monotonic()
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
            timeout=60,
        )
        response.raise_for_status()
        text = response.json()["response"].strip()
        # DEBUG on purpose — one line per LLM round-trip is noise at INFO,
        # but it's the first thing you want when case creation feels slow.
        logger.bind(component="llm", model=self.model).debug(
            "generate: {chars_in} chars in → {chars_out} chars out in {ms:.0f}ms",
            chars_in=len(prompt) + len(system or ""),
            chars_out=len(text),
            ms=(time.monotonic() - started) * 1000,
        )
        return text

    def is_reachable(self) -> bool:
        try:
            requests.get(f"{self.base_url}/api/tags", timeout=2).raise_for_status()
            return True
        except requests.RequestException:
            return False
