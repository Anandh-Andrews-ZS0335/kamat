"""Split a day's plan into one queue per collector, so every queue fits that person's shift.

The solver works with the team's total minutes. A call cannot be split between two people, so the
total alone does not guarantee each queue fits. Packing is first-fit decreasing: the longest tasks
are placed first, each into the collector with the most room left (which also balances the load).
Each queue is then worked highest value first. Tasks that take no staff time (SMS) go to an
automated queue.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

AUTOMATED = "automated"


@dataclass
class Packing:
    plan: pd.DataFrame          # every packed row, with collector_id and queue_position
    unassigned: pd.DataFrame    # voice tasks that did not fit into any single shift
    load: dict[str, float]      # minutes assigned per collector


def pack(plan: pd.DataFrame, capacities: dict[str, float]) -> Packing:
    """capacities: collector id -> plannable minutes (shift minus planning buffer), in roster order."""
    p = plan.copy()
    p["collector_id"] = None
    room = dict(capacities)
    load = {cid: 0.0 for cid in capacities}
    voice = p[p["minutes"] > 0].sort_values(["minutes", "value"], ascending=[False, False])
    unfit = []
    for i, r in voice.iterrows():
        m = float(r["minutes"])
        fits = [cid for cid in room if room[cid] + 1e-9 >= m]
        if not fits:
            unfit.append(i)
            continue
        cid = max(fits, key=lambda c: (room[c], -list(capacities).index(c)))
        p.at[i, "collector_id"] = cid
        room[cid] -= m
        load[cid] += m
    p.loc[p["minutes"] <= 0, "collector_id"] = AUTOMATED
    unassigned = p.loc[unfit].drop(columns=["collector_id"])
    p = p.drop(index=unfit)
    p = p.sort_values("value", ascending=False)
    p["queue_position"] = p.groupby("collector_id").cumcount() + 1
    return Packing(p.sort_index(), unassigned, load)


def queue_summary(plan: pd.DataFrame, capacities: dict[str, float], names: dict[str, str]) -> list[dict]:
    rows = []
    for cid in [*capacities, AUTOMATED]:
        q = plan[plan["collector_id"] == cid] if "collector_id" in plan else plan.iloc[0:0]
        if cid == AUTOMATED and q.empty:
            continue
        rows.append({"collector_id": cid, "name": names.get(cid, "Automated SMS" if cid == AUTOMATED else cid),
                     "tasks": int(len(q)), "minutes": float(q["minutes"].sum()),
                     "plannable_minutes": None if cid == AUTOMATED else round(capacities[cid], 1),
                     "est_value": round(float(q["value"].sum()), 2),
                     "actions": q["action"].value_counts().to_dict()})
    return rows
