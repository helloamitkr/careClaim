"""Claude, via the official `anthropic` SDK.

This module is named `anthropic` and imports a package named `anthropic`. That
is safe: Python 3 imports are absolute, so `import anthropic` inside here
resolves to the installed SDK, never to this file. It is deliberately a lazy
import all the same, so a local-only deployment need not install the package.
"""

from __future__ import annotations

import os
import time

from carebridge.llm.base import MissingCredentials, log_round_trip

DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        import anthropic

        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        # anthropic.Anthropic() would fall back to ANTHROPIC_API_KEY on its own,
        # but resolve it here so a missing key fails at startup, not mid-case.
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MissingCredentials(
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
        # temperature is deliberately not forwarded: Claude Opus 4.7+ rejects
        # sampling parameters (temperature/top_p/top_k) with a 400.
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
        return log_round_trip(
            model=self.model, prompt=prompt, system=system, text=text, started=started
        )

    def is_reachable(self) -> bool:
        try:
            self._client.models.retrieve(self.model)
            return True
        except Exception:
            return False
