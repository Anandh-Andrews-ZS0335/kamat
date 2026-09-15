"""Synthetic benchmark: score a run's plans against the bank's sealed true effects.

This is the ONLY non-test code that reads data/bank/sealed/. It is a separate process on purpose: on a real
portfolio the true effect is unknowable, and nothing in the engine may depend on it.

    uv run python scripts/evaluate_against_truth.py --run-id run_...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from lastmile.agents.tools import limits_from
from lastmile.config.resolve import resolve
from lastmile.engine import optimise
from lastmile.pipeline.run import run_roster
from lastmile.store import artifacts, db

ROOT = Path(__file__).resolve().parents[1]
SEALED = ROOT / "data" / "bank" / "sealed"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    run_id = ap.parse_args().run_id
    cfg = resolve()
    cfg.roster = run_roster(run_id)  # the oracle gets the same team the run planned for
    with db.connect() as con:
        as_of = con.execute("SELECT as_of_date FROM runs WHERE run_id = ?", (run_id,)).fetchone()["as_of_date"]
    # The bank moves on day by day: score against the truth for the run's own business date.
    dated = SEALED / f"truth_{as_of}.csv"
    truth = pd.read_csv(dated if dated.exists() else SEALED / "truth_today.csv", dtype={"ACCT_NBR": str})
    ident = pd.read_parquet(artifacts.identity_path(run_id))[["account_token", "account_id"]]
    t = ident.merge(truth, left_on="account_id", right_on="ACCT_NBR", how="inner")
    cand = artifacts.load_frame(run_id, "candidates")
    cand = cand.merge(t[["account_token", "SEGMENT", *[c for c in t.columns if c.startswith("TRUE_UPLIFT_")]]],
                      on="account_token", how="inner")
    cand["true_uplift"] = [row[f"TRUE_UPLIFT_{row['action']}"] for _, row in cand.iterrows()]
    # Same value definition as the engine; only the uplift is replaced by the sealed true effect.
    cand["true_value"] = cand["true_uplift"] * cand["expected_loss"] - cand["cost_cash"]

    key = cand.set_index(["account_token", "action"])
    plans = {}
    for name in ("optimised", "sort_by_risk", "sort_by_value"):
        p = artifacts.load_frame(run_id, f"plan_{name}")
        idx = list(zip(p["account_token"], p["action"], strict=True))
        tv = key.loc[idx, "true_value"].to_numpy() if len(p) else np.array([])
        plans[name] = {"selected": int(len(p)), "estimated_value": float(p["value"].sum()), "true_value": float(tv.sum()),
                       "true_segments": key.loc[idx, "SEGMENT"].value_counts().to_dict() if len(p) else {},
                       "harmful_actions": int((tv < 0).sum())}

    oracle_cand = cand.assign(value=cand["true_value"])
    oracle = optimise.assign(oracle_cand, limits_from(cfg))
    corr = {a: float(np.corrcoef(g["uplift"], g["true_uplift"])[0, 1]) for a, g in cand.groupby("action")}

    opt, risk, val = plans["optimised"]["true_value"], plans["sort_by_risk"]["true_value"], plans["sort_by_value"]["true_value"]
    report = {
        "run_id": run_id, "source": "synthetic benchmark - sealed truth from the simulated bank",
        "plans": plans, "oracle_true_value": float(oracle.objective),
        "capture_of_oracle": opt / oracle.objective if oracle.objective else None,
        "true_lift_vs_sort_by_risk": (opt - risk) / abs(risk) if risk else None,
        "true_lift_vs_sort_by_value": (opt - val) / abs(val) if val else None,
        "uplift_correlation_with_truth": corr,
        "estimate_to_truth_ratio": plans["optimised"]["estimated_value"] / opt if opt else None,
    }
    artifacts.save_json(run_id, "evaluation", report)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
