"""LLM backends for the hybrid and LLM-based agents (Medication Instruction,
Patient Outreach, Discharge Readiness).

Selected by LLM_PROVIDER in the environment or a .env file (see .env.example):

    LLM_PROVIDER=local      → Ollama      (default; `ollama serve` + gemma3:4b)
    LLM_PROVIDER=anthropic  → Claude      (needs ANTHROPIC_API_KEY)
    LLM_PROVIDER=gemini     → Gemini      (needs GEMINI_API_KEY, google-genai)

Agents call create_llm_client() and type against the LLMClient protocol, so the
vendor can be swapped without touching agent code.

    llm/
      base.py             the protocol every vendor honours
      registry.py         LLM_PROVIDER -> client; the only place a vendor is named
      providers/          one module per vendor

This package replaced the single-file `llm.py`; the public names below are the
same, so existing `from carebridge.llm import ...` imports are unaffected.
"""

from carebridge.llm.base import LLMClient, MissingCredentials
from carebridge.llm.providers.anthropic import AnthropicClient
from carebridge.llm.providers.gemini import GeminiClient
from carebridge.llm.providers.ollama import OllamaClient
from carebridge.llm.registry import create_llm_client, llm_available, provider_name

__all__ = [
    "AnthropicClient",
    "GeminiClient",
    "LLMClient",
    "MissingCredentials",
    "OllamaClient",
    "create_llm_client",
    "llm_available",
    "provider_name",
]
