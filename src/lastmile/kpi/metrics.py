"""Pure metric functions over arrays. No imports from other lastmile layers."""

from __future__ import annotations

import numpy as np


def qini_coefficient(y: np.ndarray, treated: np.ndarray, uplift: np.ndarray) -> float:
    """Normalised area between the Qini curve and random targeting. > 0 means ranking beats random."""
    order = np.argsort(-uplift)
    y, t = y[order].astype(float), treated[order].astype(bool)
    nt, nc = np.cumsum(t), np.cumsum(~t)
    yt, yc = np.cumsum(y * t), np.cumsum(y * ~t)
    with np.errstate(divide="ignore", invalid="ignore"):
        curve = yt - np.where(nc > 0, yc * nt / nc, 0.0)
    n = len(y)
    random_line = curve[-1] * np.arange(1, n + 1) / n
    return float((curve - random_line).sum() / (n * max(t.sum(), 1)))


def uplift_by_decile(y: np.ndarray, treated: np.ndarray, uplift: np.ndarray) -> list[dict]:
    order = np.argsort(-uplift)
    out = []
    for k, idx in enumerate(np.array_split(order, 10), start=1):
        t = treated[idx].astype(bool)
        obs = (y[idx][t].mean() - y[idx][~t].mean()) if t.any() and (~t).any() else float("nan")
        out.append({"decile": k, "predicted": float(uplift[idx].mean()), "observed": None if np.isnan(obs) else float(obs),
                    "n": int(len(idx)), "treated": int(t.sum())})
    return out


def lift(optimised: float, baseline: float) -> float | None:
    if baseline is None or abs(baseline) < 1e-9:
        return None
    return (optimised - baseline) / abs(baseline)
