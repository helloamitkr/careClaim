"""Local inference via `ollama serve`. No key, no network egress, no per-token
cost — which is why it stays the default provider."""

from __future__ import annotations

import os
import time

import requests

from carebridge.llm.base import log_round_trip

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = base_url or os.environ.get("OLLAMA_URL", DEFAULT_URL)
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
        return log_round_trip(
            model=self.model, prompt=prompt, system=system, text=text, started=started
        )

    def is_reachable(self) -> bool:
        try:
            requests.get(f"{self.base_url}/api/tags", timeout=2).raise_for_status()
            return True
        except requests.RequestException:
            return False
