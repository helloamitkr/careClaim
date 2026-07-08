"""Vendor selection. The one place `LLM_PROVIDER` is interpreted.

Adding a vendor means adding a module under `providers/` and one line to
`_PROVIDERS`. Nothing else in the codebase names a vendor — the agents take an
`LLMClient` and never ask which one they got.

The factories are lambdas so that importing this module does not import three
SDKs. `providers.anthropic` imports `anthropic` at construction; a local-only
deployment that never selects it never pays for the import.
"""

from __future__ import annotations

import os
from typing import Callable

from loguru import logger

from carebridge.llm.base import LLMClient

# Aliases are deliberate: 'local' is what a human writes, 'ollama' is what the
# thing is actually called, and both should work.
_PROVIDERS: dict[str, Callable[[], LLMClient]] = {}


def _register() -> None:
    from carebridge.llm.providers.anthropic import AnthropicClient
    from carebridge.llm.providers.gemini import GeminiClient
    from carebridge.llm.providers.ollama import OllamaClient

    _PROVIDERS.update(
        {
            "local": OllamaClient,
            "ollama": OllamaClient,
            "anthropic": AnthropicClient,
            "claude": AnthropicClient,
            "gemini": GeminiClient,
            "google": GeminiClient,
        }
    )


_FALSEY = {"0", "false", "no", "off"}


def llm_available() -> bool:
    """Whether the agent pipeline may run. Set LLM_AVAILABLE=false in .env to
    simulate an outage (no API token, Ollama down): cases are still accepted and
    stored, but they stay at status 'received' until the flag is flipped back
    and the backend restarts. Default: true.

    Read per-request, but `build_pipeline()` decides at startup whether to
    construct the agents at all — so flipping this without a restart is not
    supported in either direction. python-dotenv loads .env once, at import;
    editing the file does not reach a running process.
    """
    return os.environ.get("LLM_AVAILABLE", "true").strip().lower() not in _FALSEY


def provider_name() -> str:
    return os.environ.get("LLM_PROVIDER", "local").strip().lower()


def create_llm_client() -> LLMClient:
    """Build the client selected by LLM_PROVIDER (default: local Ollama)."""
    if not _PROVIDERS:
        _register()

    name = provider_name()
    factory = _PROVIDERS.get(name)
    if factory is None:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {name!r} — use one of: "
            f"{', '.join(sorted(_PROVIDERS))}"
        )

    client = factory()
    logger.bind(component="llm").info(
        "LLM provider: {provider} (model: {model})", provider=name, model=client.model
    )
    return client
