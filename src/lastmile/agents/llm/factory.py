"""Choose the LLM client from the environment. No key -> deterministic templates, clearly labelled."""

from __future__ import annotations

from lastmile.agents.llm.base import LLMClient
from lastmile.config.settings import llm_settings


def get_llm() -> tuple[LLMClient | None, str]:
    s = llm_settings()
    if s["provider"] == "gemini" and s["gemini_api_key"]:
        from lastmile.agents.llm.gemini import GeminiClient
        return GeminiClient(s["gemini_api_key"], s["gemini_model"], s["timeout_s"]), f"gemini:{s['gemini_model']}"
    if s["provider"] == "gemini":
        return None, "template (GEMINI_API_KEY not set)"
    if s["provider"] == "ollama":
        from lastmile.agents.llm.ollama import OllamaClient
        return OllamaClient(s["ollama_base_url"], s["ollama_model"], s["timeout_s"]), f"ollama:{s['ollama_model']}"
    return None, f"template (provider '{s['provider']}' not implemented)"
