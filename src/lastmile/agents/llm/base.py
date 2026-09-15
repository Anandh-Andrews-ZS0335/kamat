"""The LLM contract. Swap the provider by adding a class that satisfies this protocol - agents never change."""

from __future__ import annotations

from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    provider: str
    model: str

    def generate_json(self, system: str, prompt: str) -> dict:
        """Return a parsed JSON object. Raise LLMError on transport, quota or parse failure.

        Optionally set `self.last_exchange` (endpoint, request, http_status, response_text, usage, finish_reason,
        latency_ms) so the call is traced in full. Never include credentials in it.
        """
        ...
