"""LLM_PROVIDER dispatch, and the lazy-import promise that makes it cheap.

The point of `llm/providers/` is that a deployment running Ollama locally never
touches the Anthropic or Google SDKs. That is a property of *when* the import
happens, so it needs a test — a stray top-level `import anthropic` would keep
every other test green while quietly making the package a hard dependency.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from carebridge.llm import (
    AnthropicClient,
    GeminiClient,
    LLMClient,
    MissingCredentials,
    OllamaClient,
    create_llm_client,
    llm_available,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_AVAILABLE", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("alias", ["local", "ollama", "LOCAL", " Ollama "])
def test_ollama_aliases(monkeypatch, alias):
    monkeypatch.setenv("LLM_PROVIDER", alias)
    assert isinstance(create_llm_client(), OllamaClient)


def test_default_provider_is_local(monkeypatch):
    assert isinstance(create_llm_client(), OllamaClient)


@pytest.mark.parametrize("alias", ["anthropic", "claude"])
def test_anthropic_aliases(monkeypatch, alias):
    monkeypatch.setenv("LLM_PROVIDER", alias)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert isinstance(create_llm_client(), AnthropicClient)


@pytest.mark.parametrize("alias", ["gemini", "google"])
def test_gemini_aliases(monkeypatch, alias):
    pytest.importorskip("google.genai")
    monkeypatch.setenv("LLM_PROVIDER", alias)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert isinstance(create_llm_client(), GeminiClient)


def test_unknown_provider_names_the_alternatives(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        create_llm_client()


@pytest.mark.parametrize(
    "provider,missing",
    [("anthropic", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY")],
)
def test_a_missing_key_fails_at_construction(monkeypatch, provider, missing):
    """Not mid-case, inside a background task that swallows the traceback."""
    if provider == "gemini":
        pytest.importorskip("google.genai")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    with pytest.raises(MissingCredentials, match=missing):
        create_llm_client()


def test_missing_credentials_is_still_a_runtime_error():
    """Callers that predate the class catch RuntimeError."""
    assert issubclass(MissingCredentials, RuntimeError)


def test_selecting_ollama_imports_no_vendor_sdk():
    """Importing the package and building the local client must not drag in
    `anthropic` or `google.genai`.

    Runs in a subprocess: by the time this file executes, both SDKs are already
    in this interpreter's sys.modules (the tests above construct them), so an
    in-process assertion would be measuring the test suite, not the package.
    """
    probe = """
import sys
import carebridge.llm as llm
llm.OllamaClient()
leaked = [m for m in sys.modules if m == "anthropic" or m.startswith("google.genai")]
print(",".join(sorted(leaked)))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "LLM_PROVIDER": "local"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"vendor SDKs imported eagerly: {result.stdout.strip()}"


def test_every_client_satisfies_the_protocol():
    assert isinstance(OllamaClient(), LLMClient)


@pytest.mark.parametrize(
    "value,expected",
    [(None, True), ("true", True), ("1", True), ("false", False), ("FALSE", False),
     ("0", False), ("no", False), ("off", False), (" false ", False)],
)
def test_llm_available_flag(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("LLM_AVAILABLE", value)
    assert llm_available() is expected
