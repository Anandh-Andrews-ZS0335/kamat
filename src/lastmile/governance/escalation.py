"""Rule-triggered escalations. Escalated accounts are never included in a bulk approval."""

from __future__ import annotations

import pandas as pd

from lastmile.config.schema import Escalation


def flag(plan: pd.DataFrame, rules: Escalation) -> pd.Series:
    out = []
    for _, r in plan.iterrows():
        f = []
        if r["exposure"] >= rules.exposure_threshold:
            f.append({"code": "HIGH_EXPOSURE", "detail": "balance at or above the senior review threshold"})
        if (r["uplift_upper"] - r["uplift_lower"]) > rules.max_interval_width:
            f.append({"code": "WIDE_INTERVAL", "detail": "uplift estimate is too uncertain to act on unreviewed"})
        if r["action"] in rules.escalate_actions:
            f.append({"code": "HARDSHIP_OFFER", "detail": "hardship referrals always need a named reviewer"})
        out.append(f)
    return pd.Series(out, index=plan.index, dtype=object)
