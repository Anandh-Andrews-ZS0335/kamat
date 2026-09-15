"""HTTP adapter to the bank. Returns frames with the bank's own column names - no business logic."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import pandas as pd


class BankApiError(RuntimeError):
    pass


@dataclass
class FeedResult:
    feed: str
    path: str
    frame: pd.DataFrame
    as_of: str | None
    pages: int
    rows: int
    ms: int
    calls: list[dict] = field(default_factory=list)


class BankClient:
    def __init__(self, base_url: str, page_size: int = 5000, client: httpx.Client | None = None, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    def _get(self, path: str, params: dict | None = None) -> tuple[dict, int]:
        t0 = time.perf_counter()
        try:
            r = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise BankApiError(f"bank API unreachable at {self.base_url}{path}: {e}") from e
        ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            raise BankApiError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json(), ms

    def health(self) -> dict:
        return self._get("/api/v1/health")[0]

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._get(path, params)[0]

    def feeds(self) -> dict:
        return self._get("/api/v1/feeds")[0]

    def fetch_feed(self, feed: str, path: str) -> FeedResult:
        page, items, calls, total_ms, as_of = 1, [], [], 0, None
        while True:
            body, ms = self._get(path, {"page": page, "page_size": self.page_size})
            total_ms += ms
            items.extend(body["items"])
            as_of = body.get("as_of")
            calls.append({"method": "GET", "path": path, "page": page, "rows": len(body["items"]),
                          "total": body["total"], "ms": ms})
            if not body.get("has_more"):
                break
            page += 1
        return FeedResult(feed=feed, path=path, frame=pd.DataFrame(items), as_of=as_of, pages=page,
                          rows=len(items), ms=total_ms, calls=calls)

    def post(self, path: str, payload: dict) -> dict:
        try:
            r = self._client.post(path, json=payload)
        except httpx.HTTPError as e:
            raise BankApiError(f"bank API unreachable at {self.base_url}{path}: {e}") from e
        if r.status_code != 200:
            raise BankApiError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()
