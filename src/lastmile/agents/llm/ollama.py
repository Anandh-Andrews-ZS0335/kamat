"""Ollama local LLM client via the REST /api/chat endpoint. Plain httpx - no extra dependencies."""

from __future__ import annotations

import json
import time

import httpx

from lastmile.agents.llm.base import LLMError


class OllamaClient:
    provider = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.2", timeout_s: float = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = timeout_s

    def generate_json(self, system: str, prompt: str) -> dict:
        url = f"{self.base_url}/api/chat"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2},
        }
        self.last_exchange = ex = {
            "endpoint": url,
            "request": body,
            "http_status": None,
            "response_text": None,
            "usage": None,
            "finish_reason": None,
            "latency_ms": None,
        }
        t0 = time.perf_counter()
        try:
            r = httpx.post(url, json=body, timeout=self._timeout)
        except httpx.HTTPError as e:
            ex["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            raise LLMError(f"Ollama unreachable at {url}: {e}") from e

        ex.update(latency_ms=int((time.perf_counter() - t0) * 1000), http_status=r.status_code, response_text=r.text)
        if r.status_code != 200:
            raise LLMError(f"Ollama returned {r.status_code}: {r.text[:300]}")

        try:
            payload = r.json()
            ex["usage"] = {
                "prompt_eval_count": payload.get("prompt_eval_count"),
                "eval_count": payload.get("eval_count"),
                "total_duration": payload.get("total_duration"),
            }
            ex["finish_reason"] = payload.get("done_reason", "stop" if payload.get("done") else None)
            content = payload["message"]["content"]
            return json.loads(content)
        except (KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"Ollama response was not valid JSON: {e}") from e
