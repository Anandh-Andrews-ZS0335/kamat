"""Gemini via the REST generateContent endpoint. Plain httpx - no provider SDK to replace later."""

from __future__ import annotations

import json
import time

import httpx

from lastmile.agents.llm.base import LLMError

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, timeout_s: float = 45):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.model, self._key, self._timeout = model, api_key, timeout_s

    def generate_json(self, system: str, prompt: str) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        url = ENDPOINT.format(model=self.model)
        # The raw request and response of the latest call, for the trace. The API key travels in a header and is never kept.
        self.last_exchange = ex = {"endpoint": url, "request": body, "http_status": None, "response_text": None,
                                   "usage": None, "finish_reason": None, "latency_ms": None}
        t0 = time.perf_counter()
        try:
            r = httpx.post(url, json=body, timeout=self._timeout,
                           headers={"x-goog-api-key": self._key, "Content-Type": "application/json"})
        except httpx.HTTPError as e:
            ex["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            raise LLMError(f"Gemini unreachable: {e}") from e
        ex.update(latency_ms=int((time.perf_counter() - t0) * 1000), http_status=r.status_code, response_text=r.text)
        if r.status_code != 200:
            raise LLMError(f"Gemini returned {r.status_code}: {r.text[:300]}")
        try:
            payload = r.json()
            ex["usage"] = payload.get("usageMetadata")
            ex["finish_reason"] = (payload.get("candidates") or [{}])[0].get("finishReason")
            parts = payload["candidates"][0]["content"]["parts"]
            return json.loads("".join(p.get("text", "") for p in parts))
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"Gemini response was not the expected JSON: {e}") from e
