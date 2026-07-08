"""Gemini, via Google's `google-genai` SDK.

Two traps this client closes, both specific to Gemini 2.5:

  * Thinking is on by default and its tokens count against `max_output_tokens`.
    Our agents ask for ~200 tokens of structured text; thinking would eat the
    whole budget and `response.text` would come back empty. `thinking_budget=0`
    turns it off — these are short, deterministic extractions, not reasoning.

  * `response.text` is None (not "") when the model returns no text part, e.g.
    when generation stops on a safety filter. Normalize before `.strip()`.
"""

from __future__ import annotations

import os
import time

from carebridge.llm.base import MissingCredentials, log_round_trip

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover — depends on the install
            raise MissingCredentials(
                "LLM_PROVIDER=gemini but the google-genai package is not installed — "
                "run `pip install google-genai`"
            ) from exc

        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise MissingCredentials(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set — "
                "add it to your .env file (see .env.example)"
            )
        self._client = genai.Client(api_key=key)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 200,
    ) -> str:
        from google.genai import types

        started = time.monotonic()
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        text = (response.text or "").strip()
        return log_round_trip(
            model=self.model, prompt=prompt, system=system, text=text, started=started
        )

    def is_reachable(self) -> bool:
        try:
            self._client.models.get(model=self.model)
            return True
        except Exception:
            return False
