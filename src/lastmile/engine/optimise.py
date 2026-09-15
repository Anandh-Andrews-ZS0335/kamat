"""Daily assignment as a mixed-integer program, solved by CBC. Not a sort.

Decision variables exist only for (account, action) pairs policy already allowed and whose
estimated value is positive - so ineligible or harmful choices are impossible by construction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import pulp


@dataclass
class Limits:
    capacity_minutes: float
    sms_max: int
    hardship_slots: int
    member_daily_contacts: int
    hardship_action: str = "HARDSHIP"
    sms_action: str = "SMS"


@dataclass
class PlanResult:
    plan: pd.DataFrame
    status: str
    objective: float
    solve_ms: int
    variables: int
    constraints: int
    minutes_used: float
    sms_used: int
    hardship_used: int


CANDIDATE_COLUMNS = ["account_token", "member_token", "action", "value", "minutes", "is_contact",
                     "member_contact_room"]


def assign(candidates: pd.DataFrame, limits: Limits, force: tuple[str, str] | None = None,
           exclude: set[str] | None = None, time_limit_s: int = 20) -> PlanResult:
    exclude = exclude or set()
    c = candidates[(candidates["allowed"]) & (candidates["value"] > 0) & (~candidates["account_token"].isin(exclude))]
    if force is not None:
        forced_row = candidates[(candidates["account_token"] == force[0]) & (candidates["action"] == force[1])]
        c = pd.concat([c[c["account_token"] != force[0]], forced_row], ignore_index=True)
    c = c.reset_index(drop=True)

    prob = pulp.LpProblem("daily_assignment", pulp.LpMaximize)
    x = {i: pulp.LpVariable(f"x_{i}", cat="Binary") for i in c.index}
    prob += pulp.lpSum(float(c.at[i, "value"]) * x[i] for i in c.index)

    for _, idx in c.groupby("account_token").groups.items():                     # one action per account
        prob += pulp.lpSum(x[i] for i in idx) <= 1
    prob += pulp.lpSum(float(c.at[i, "minutes"]) * x[i] for i in c.index) <= limits.capacity_minutes
    sms = c.index[c["action"] == limits.sms_action]
    prob += pulp.lpSum(x[i] for i in sms) <= limits.sms_max
    hard = c.index[c["action"] == limits.hardship_action]
    prob += pulp.lpSum(x[i] for i in hard) <= limits.hardship_slots
    contacts = c[c["is_contact"]]
    for _, g in contacts.groupby("member_token"):                                  # per PERSON, not per account
        room = int(min(limits.member_daily_contacts, g["member_contact_room"].min()))
        prob += pulp.lpSum(x[i] for i in g.index) <= room
    if force is not None:
        fi = c.index[(c["account_token"] == force[0]) & (c["action"] == force[1])]
        prob += pulp.lpSum(x[i] for i in fi) == 1

    t0 = time.perf_counter()
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s))
    ms = int((time.perf_counter() - t0) * 1000)
    status = pulp.LpStatus[prob.status]

    chosen = [i for i in c.index if (x[i].value() or 0) > 0.5]
    plan = c.loc[chosen].copy().sort_values("value", ascending=False).reset_index(drop=True)
    plan.insert(0, "rank", range(1, len(plan) + 1))
    return PlanResult(
        plan=plan, status=status, objective=float(plan["value"].sum()), solve_ms=ms,
        variables=len(x), constraints=len(prob.constraints), minutes_used=float(plan["minutes"].sum()),
        sms_used=int((plan["action"] == limits.sms_action).sum()),
        hardship_used=int((plan["action"] == limits.hardship_action).sum()),
    )


def explain_selection(candidates: pd.DataFrame, limits: Limits, base: PlanResult, account_token: str) -> dict:
    """Solver-produced counterfactual. Selected: what if we drop it? Not selected: what if we force it in?"""
    selected = base.plan[base.plan["account_token"] == account_token]
    base_set = set(base.plan["account_token"])
    if len(selected):
        alt = assign(candidates, limits, exclude={account_token})
        added = alt.plan[~alt.plan["account_token"].isin(base_set)]
        return {"account_token": account_token, "was_selected": True, "action": selected.iloc[0]["action"],
                "objective_base": base.objective, "objective_counterfactual": alt.objective,
                "cost_of_change": base.objective - alt.objective,
                "would_be_replaced_by": added[["account_token", "action", "value"]].to_dict("records"),
                "solver_status": alt.status}
    options = candidates[(candidates["account_token"] == account_token) & candidates["allowed"]]
    if options.empty:
        blocked = candidates[candidates["account_token"] == account_token]
        return {"account_token": account_token, "was_selected": False, "reason": "no_allowed_action",
                "blocked": blocked[["action", "blocked_reasons"]].to_dict("records")}
    best = options.sort_values("value", ascending=False).iloc[0]
    if best["value"] <= 0:
        return {"account_token": account_token, "was_selected": False, "reason": "no_positive_value",
                "best_action": best["action"], "best_value": float(best["value"])}
    alt = assign(candidates, limits, force=(account_token, best["action"]))
    alt_set = set(alt.plan["account_token"])
    displaced = base.plan[~base.plan["account_token"].isin(alt_set)]
    return {"account_token": account_token, "was_selected": False, "reason": "outcompeted",
            "forced_action": best["action"], "forced_value": float(best["value"]),
            "objective_base": base.objective, "objective_counterfactual": alt.objective,
            "cost_of_change": base.objective - alt.objective,
            "would_displace": displaced[["account_token", "action", "value"]].to_dict("records"),
            "solver_status": alt.status}
