"""Causal feasibility: can this data support an uplift model at all? Run before trusting any estimate."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def feasibility(train: pd.DataFrame, features: list[str], control_value: str) -> dict:
    X = train[features].astype(float).fillna(0.0)
    treated = (train["T"] != control_value).astype(int).to_numpy()
    y = train["Y"].astype(int).to_numpy()

    prop_model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=500))
    prop_model.fit(X, treated)
    prop = prop_model.predict_proba(X)[:, 1]
    overlap = float(((prop > 0.05) & (prop < 0.95)).mean())

    leak = []
    for f in features:
        col = X[f].to_numpy()
        if np.nanstd(col) == 0:
            continue
        auc = roc_auc_score(y, col)
        auc = max(auc, 1 - auc)
        if auc > 0.9:
            leak.append({"feature": f, "solo_auc": round(float(auc), 3)})

    arms = train["T"].value_counts().to_dict()
    checks = [
        {"check": "treatment_prevalence", "value": round(float(treated.mean()), 3), "healthy": "0.20-0.80",
         "passed": 0.2 <= treated.mean() <= 0.8},
        {"check": "control_rows", "value": int(arms.get(control_value, 0)), "healthy": "> 2,000",
         "passed": arms.get(control_value, 0) > 2000},
        {"check": "smallest_arm_rows", "value": int(min(v for k, v in arms.items() if k != control_value)),
         "healthy": "> 300", "passed": min(v for k, v in arms.items() if k != control_value) > 300},
        {"check": "overlap_share", "value": round(overlap, 3), "healthy": "> 0.80 of propensities in 0.05-0.95",
         "passed": overlap > 0.8},
        {"check": "outcome_base_rate", "value": round(float(y.mean()), 3), "healthy": "0.05-0.60",
         "passed": 0.05 <= y.mean() <= 0.60},
        {"check": "leakage_scan", "value": len(leak), "healthy": "no feature with solo AUC > 0.9",
         "passed": not leak},
    ]
    for c in checks:  # plain Python types: numpy scalars would serialise as strings
        c["passed"] = bool(c["passed"])
        c["value"] = c["value"].item() if hasattr(c["value"], "item") else c["value"]
    return {"checks": checks, "arms": {k: int(v) for k, v in arms.items()}, "suspected_leaks": leak,
            "propensity_histogram": np.histogram(prop, bins=10, range=(0, 1))[0].tolist(),
            "feasible": all(c["passed"] for c in checks)}
