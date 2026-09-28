"""Choose the LLM client from the environment. No key -> deterministic templates, clearly labelled.

The mode string returned here is a *display* label and deliberately names no vendor: the consoles
show whether a model wrote the wording, not whose. The exact provider and model are recorded on
every call in `llm_calls`, so an auditor can still tell who answered.
"""

from __future__ import annotations

from lastmile.agents.llm.base import LLMClient
from lastmile.config.settings import llm_settings

LIVE = "llm"          # a model wrote it
TEMPLATE_PREFIX = "template"   # deterministic fallback; consoles branch on this, not on a vendor


def get_llm() -> tuple[LLMClient | None, str]:
    s = llm_settings()
    if s["provider"] == "gemini" and s["gemini_api_key"]:
        from lastmile.agents.llm.gemini import GeminiClient
        return GeminiClient(s["gemini_api_key"], s["gemini_model"], s["timeout_s"]), LIVE
    if s["provider"] == "gemini":
        return None, "template (no LLM key configured)"
    if s["provider"] == "ollama":
        from lastmile.agents.llm.ollama import OllamaClient
        return OllamaClient(s["ollama_base_url"], s["ollama_model"], s["timeout_s"]), LIVE
    return None, "template (no LLM configured)"
