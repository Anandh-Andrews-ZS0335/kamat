"""Process-level settings from the environment. The lowest layer: imports nothing from lastmile."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

CONFIG_DIR = Path(os.environ.get("LASTMILE_CONFIG_DIR", ROOT / "config"))
DATA_DIR = Path(os.environ.get("LASTMILE_DATA_DIR", ROOT / "data" / "engine"))
TOKEN_SECRET = os.environ.get("LASTMILE_TOKEN_SECRET", "dev-only-token-secret-change-me")


def bank_base_url(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default).rstrip("/")


def llm_settings() -> dict:
    return {
        "provider": os.environ.get("LLM_PROVIDER", "gemini").lower(),
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")),
        "ollama_model": os.environ.get("OLLAMA_MODEL", "llama3.2"),
        "timeout_s": float(os.environ.get("LLM_TIMEOUT_S", "45")),
    }
