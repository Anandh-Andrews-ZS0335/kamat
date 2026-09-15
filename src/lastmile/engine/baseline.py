"""The strategies the optimiser must beat: work the list top-down by risk, or by dollars at stake.

Same policy, same capacity, same per-member limits - only the selection rule differs. Baselines
use the legacy action rule and do not look at uplift, which is exactly why they call sleeping dogs.
With a roster, a task is taken only if some collector still has room for it, as a team working the
list top-down would do.
"""

from __future__ import annotations

import pandas as pd

from lastmile.engine.optimise import Limits


def legacy_action(dpd: float) -> list[str]:
    if dpd < 45:
        return ["SMS", "CALL"]
    if dpd <= 90:
        return ["CALL", "PLAN", "SMS"]
    return ["PLAN", "CALL", "SMS"]


def greedy(portfolio: pd.DataFrame, candidates: pd.DataFrame, limits: Limits, sort_by: str, minutes_of: dict,
           strategy: str, capacities: dict[str, float] | None = None) -> pd.DataFrame:
    lookup = candidates.set_index(["account_token", "action"])
    shift_room = dict(capacities) if capacities is not None else None
    minutes = sms = hard = 0
    member_used: dict[str, int] = {}
    picks = []
    for _, r in portfolio.sort_values(sort_by, ascending=False).iterrows():
        tok, mem = r["account_token"], r["member_token"]
        for action in legacy_action(float(r["dpd"])):
            if (tok, action) not in lookup.index:
                continue
            cand = lookup.loc[(tok, action)]
            if not bool(cand["allowed"]):
                continue
            room = min(limits.member_daily_contacts, int(cand["member_contact_room"]))
            if member_used.get(mem, 0) >= room:
                break
            m = minutes_of[action]
            if minutes + m > limits.capacity_minutes:
                continue
            collector = "automated" if m <= 0 else None
            if shift_room is not None and m > 0:
                fits = [c for c in shift_room if shift_room[c] + 1e-9 >= m]
                if not fits:
                    continue
                collector = max(fits, key=lambda c: shift_room[c])
            if action == limits.sms_action and sms >= limits.sms_max:
                continue
            if action == limits.hardship_action and hard >= limits.hardship_slots:
                continue
            minutes += m
            if shift_room is not None and m > 0:
                shift_room[collector] -= m
            sms += action == limits.sms_action
            hard += action == limits.hardship_action
            member_used[mem] = member_used.get(mem, 0) + 1
            picks.append({"account_token": tok, "member_token": mem, "action": action,
                          "value": float(cand["value"]), "minutes": m, "sort_key": float(r[sort_by]),
                          "collector_id": collector if shift_room is not None else None})
            break
    out = pd.DataFrame(picks)
    if out.empty:
        out = pd.DataFrame(columns=["account_token", "member_token", "action", "value", "minutes", "sort_key", "collector_id"])
    out.insert(0, "rank", range(1, len(out) + 1))
    out["strategy"] = strategy
    return out
