"""Monte Carlo policy comparison: a distribution of outcomes, not a point guess.

Each run samples an uplift from its estimated interval, draws whether the account cures with and
without the action using common random numbers, and converts the incremental cure to avoided loss
(incremental cure x expected loss if the account does not cure).
Staff minutes are sampled around each action's standard handling time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def simulate(plan: pd.DataFrame, runs: int, capacity_minutes: float, seed: int) -> dict:
    if plan.empty:
        return {"runs": runs, "value_p10": 0.0, "value_p50": 0.0, "value_p90": 0.0, "value_mean": 0.0,
                "minutes_p50": 0.0, "minutes_p90": 0.0, "prob_over_capacity": 0.0, "histogram": [], "edges": []}
    rng = np.random.default_rng(seed)
    n = len(plan)
    p0 = plan["p0"].to_numpy(float)
    u = plan["uplift"].to_numpy(float)
    sd = plan["uplift_std"].to_numpy(float)
    stake = plan["expected_loss"].to_numpy(float)
    minutes = plan["minutes"].to_numpy(float)

    u_draw = rng.normal(u, np.maximum(sd, 1e-6), size=(runs, n))
    p1 = np.clip(p0 + u_draw, 0.0, 1.0)
    shared = rng.random((runs, n))
    incremental = (shared < p1).astype(float) - (shared < p0).astype(float)
    value = (incremental * stake).sum(axis=1)

    voice = minutes > 0
    handle = np.zeros((runs, n))
    handle[:, voice] = rng.lognormal(np.log(minutes[voice]), 0.35, size=(runs, int(voice.sum())))
    total_minutes = handle.sum(axis=1)

    hist, edges = np.histogram(value, bins=24)
    return {
        "runs": runs,
        "value_p10": float(np.percentile(value, 10)), "value_p50": float(np.percentile(value, 50)),
        "value_p90": float(np.percentile(value, 90)), "value_mean": float(value.mean()),
        "minutes_p50": float(np.percentile(total_minutes, 50)), "minutes_p90": float(np.percentile(total_minutes, 90)),
        "prob_over_capacity": float((total_minutes > capacity_minutes).mean()),
        "histogram": hist.tolist(), "edges": [float(e) for e in edges],
    }
