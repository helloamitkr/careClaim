"""LLM clients for the hybrid and LLM-based agents (Medication Instruction,
Patient Outreach, Discharge Readiness).

Two interchangeable backends, selected by LLM_PROVIDER in the environment or
a .env file (see .env.example at the repo root):

  LLM_PROVIDER=local      → OllamaClient (default; `ollama serve` + gemma3:4b)
  LLM_PROVIDER=anthropic  → AnthropicClient (needs ANTHROPIC_API_KEY)

Agents should call create_llm_client() instead of constructing a client
directly, so the backend can be swapped without touching agent code."""

from __future__ import annotations

import os
import time

import requests
from dotenv import find_dotenv, load_dotenv
from loguru import logger

# Pull in .env before any client reads os.environ. usecwd=True walks up from
# the process working directory (backend/ under uvicorn), so backend/.env and
# a repo-root .env both work.
load_dotenv(find_dotenv(usecwd=True))

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:4b"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"


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


class AnthropicClient:
    """Same generate()/is_reachable() surface as OllamaClient, backed by the
    Claude API. Reads ANTHROPIC_API_KEY / ANTHROPIC_MODEL from the environment
    (populated from .env above)."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        import anthropic  # imported lazily so local-only setups don't need the package

        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
        # anthropic.Anthropic() falls back to ANTHROPIC_API_KEY on its own,
        # but resolve it here so a missing key fails at startup, not mid-case.
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set — "
                "add it to your .env file (see .env.example)"
            )
        self._client = anthropic.Anthropic(api_key=key)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,  # accepted for interface parity; see below
        max_tokens: int = 200,
    ) -> str:
        started = time.monotonic()
        # temperature is intentionally not forwarded: Claude Opus 4.7+ rejects
        # sampling parameters with a 400.
        kwargs: dict = {}
        if system:
            kwargs["system"] = system
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        text = next(
            (block.text for block in response.content if block.type == "text"), ""
        ).strip()
        logger.bind(component="llm", model=self.model).debug(
            "generate: {chars_in} chars in → {chars_out} chars out in {ms:.0f}ms",
            chars_in=len(prompt) + len(system or ""),
            chars_out=len(text),
            ms=(time.monotonic() - started) * 1000,
        )
        return text

    def is_reachable(self) -> bool:
        try:
            self._client.models.retrieve(self.model)
            return True
        except Exception:
            return False


LLMClient = OllamaClient | AnthropicClient


def create_llm_client() -> LLMClient:
    """Build the LLM client selected by LLM_PROVIDER (default: local Ollama)."""
    provider = os.environ.get("LLM_PROVIDER", "local").strip().lower()
    if provider == "anthropic":
        client = AnthropicClient()
    elif provider in ("local", "ollama"):
        client = OllamaClient()
    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {provider!r} — use 'local' or 'anthropic'"
        )
    logger.bind(component="llm").info(
        "LLM provider: {provider} (model: {model})", provider=provider, model=client.model
    )
    return client
