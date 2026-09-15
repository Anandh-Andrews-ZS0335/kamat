"""Leakage guards - enforced, not advisory."""

from __future__ import annotations

import pandas as pd


class LeakageError(ValueError):
    pass


def guard_features(requested: list[str], exclude: list[str], frame: pd.DataFrame) -> dict:
    leaked = sorted(set(requested) & set(exclude))
    if leaked:
        raise LeakageError(f"excluded features requested: {leaked}")
    missing = [f for f in requested if f not in frame.columns]
    if missing:
        raise LeakageError(f"requested features not built: {missing}")
    return {"features": len(requested), "excluded_checked": list(exclude), "status": "clean"}
