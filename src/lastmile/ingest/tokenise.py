"""PII boundary. Everything downstream of here sees pseudonymous tokens, never names or phone numbers."""

from __future__ import annotations

import hashlib
import hmac

import pandas as pd

from lastmile.config.settings import TOKEN_SECRET


def token(prefix: str, value: str, secret: str = TOKEN_SECRET) -> str:
    digest = hmac.new(secret.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:12]
    return f"{prefix}_{digest}"


def tokenise(frames: dict[str, pd.DataFrame], pii_fields: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict]:
    out: dict[str, pd.DataFrame] = {}
    for feed, df in frames.items():
        d = df.copy()
        if "account_id" in d.columns:
            d.insert(0, "account_token", d["account_id"].map(lambda v: token("acc", v)))
        if "member_id" in d.columns:
            d.insert(0, "member_token", d["member_id"].map(lambda v: token("mem", v)))
        out[feed] = d

    members = out["members"]
    identity = out["accounts"][["account_token", "member_token", "account_id", "member_id"]].merge(
        members[["member_token", *[f for f in pii_fields if f in members.columns], "language"]],
        on="member_token", how="left")

    dropped = {}
    for feed, d in out.items():
        cols = [c for c in d.columns if c in pii_fields or c in ("account_id", "member_id")]
        dropped[feed] = cols
        out[feed] = d.drop(columns=cols)
    return out, identity, {"dropped_columns": dropped, "identity_rows": int(len(identity))}
