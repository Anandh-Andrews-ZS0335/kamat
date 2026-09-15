"""Policy runs twice: before solving it prunes what is allowed; after solving it independently re-verifies.

A bug in pruning therefore cannot silently reach a member - verification would catch it.
"""

from __future__ import annotations

import pandas as pd

from lastmile.config.schema import ResolvedConfig


def eligibility(portfolio: pd.DataFrame, cfg: ResolvedConfig) -> pd.DataFrame:
    inst, scen = cfg.institution, cfg.scenario
    suppressed_by = pd.Series([[] for _ in range(len(portfolio))], index=portfolio.index, dtype=object)
    for flag in inst.suppression:
        hit = portfolio[flag].fillna(0).astype(int) == 1
        for i in portfolio.index[hit]:
            suppressed_by.at[i] = [*suppressed_by.at[i], f"suppressed: {flag}"]

    room = (inst.contact_policy.max_attempts_per_7d - portfolio["contacts_7d"]).clip(lower=0)
    failing = {name: rs.failing(portfolio) for name, rs in inst.eligibility.items()}

    rows = []
    for a in scen.actions:
        for i in portfolio.index:
            reasons = list(suppressed_by.at[i])
            for req in a.requires:
                reasons += [f"{req}: {d}" for d in failing[req].at[i]]
            if a.is_contact and room.at[i] <= 0:
                reasons.append(f"contact cap: {int(portfolio.at[i, 'contacts_7d'])} contacts in last 7 days")
            rows.append({"account_token": portfolio.at[i, "account_token"], "member_token": portfolio.at[i, "member_token"],
                         "action": a.id, "allowed": not reasons, "blocked_reasons": "; ".join(reasons),
                         "member_contact_room": int(room.at[i]), "is_contact": a.is_contact,
                         "minutes": a.cost_minutes, "cost_cash": a.cost_cash})
    return pd.DataFrame(rows)


def summary(elig: pd.DataFrame) -> dict:
    per_account = elig.groupby("account_token")["allowed"].any()
    blocked = elig[~elig["allowed"]]
    reason_counts = (blocked["blocked_reasons"].str.split("; ").explode().str.split(":").str[0]
                     .value_counts().to_dict()) if len(blocked) else {}
    return {"accounts": int(per_account.size), "accounts_with_any_allowed_action": int(per_account.sum()),
            "pairs_allowed": int(elig["allowed"].sum()), "pairs_blocked": int((~elig["allowed"]).sum()),
            "block_reasons": reason_counts,
            "allowed_by_action": elig.groupby("action")["allowed"].sum().astype(int).to_dict()}


def verify(plan: pd.DataFrame, elig: pd.DataFrame, cfg: ResolvedConfig) -> list[dict]:
    """Independent post-solve check. Returns violations; the pipeline refuses to publish if any exist."""
    inst = cfg.institution
    v: list[dict] = []
    if plan.empty:
        return v
    key = elig.set_index(["account_token", "action"])["allowed"]
    for _, r in plan.iterrows():
        if not bool(key.get((r["account_token"], r["action"]), False)):
            v.append({"rule": "action_not_allowed", "account_token": r["account_token"], "action": r["action"]})
    dup = plan["account_token"].duplicated()
    if dup.any():
        v.append({"rule": "multiple_actions_per_account", "count": int(dup.sum())})
    minutes = plan["minutes"].sum()
    if minutes > cfg.plannable_minutes + 1e-6:
        v.append({"rule": "capacity_minutes", "used": float(minutes), "limit": cfg.plannable_minutes})
    if "collector_id" in plan:
        # Every voice task belongs to one collector who is working today, within that person's shift.
        working = {c.id: c for c in cfg.team.working}
        voice = plan[plan["minutes"] > 0]
        stray = voice[~voice["collector_id"].isin(list(working))]
        if len(stray):
            v.append({"rule": "voice_task_without_working_collector", "count": int(len(stray)),
                      "collectors": sorted({str(x) for x in stray["collector_id"]})})
        for cid, used in voice[voice["collector_id"].isin(list(working))].groupby("collector_id")["minutes"].sum().items():
            limit = cfg.plannable_for(working[cid])
            if used > limit + 1e-6:
                v.append({"rule": "collector_shift_minutes", "collector_id": cid, "used": float(used), "limit": limit})
    if (plan["action"] == "SMS").sum() > inst.capacity.sms_per_day_max:
        v.append({"rule": "sms_daily_max"})
    if (plan["action"] == "HARDSHIP").sum() > inst.capacity.hardship_slots_per_day:
        v.append({"rule": "hardship_slots"})
    contact_ids = {a.id for a in cfg.scenario.actions if a.is_contact}
    per_member = plan[plan["action"].isin(contact_ids)].groupby("member_token").size()
    over = per_member[per_member > inst.contact_policy.max_contacts_per_member_per_day]
    for mem, n in over.items():
        v.append({"rule": "member_daily_contacts", "member_token": mem, "contacts": int(n)})
    return v


def account_checks(row: pd.Series, cfg: ResolvedConfig) -> list[dict]:
    """Every rule evaluated for one account, passed or failed - for the manager's review panel."""
    inst = cfg.institution
    df = row.to_frame().T.infer_objects()
    checks = [{"group": "suppression", "check": flag, "passed": int(row.get(flag, 0) or 0) == 0,
               "detail": f"{flag} = {int(row.get(flag, 0) or 0)}"} for flag in inst.suppression]
    room = inst.contact_policy.max_attempts_per_7d - int(row["contacts_7d"])
    checks.append({"group": "contact policy", "check": "7-day contact cap", "passed": room > 0,
                   "detail": f"{int(row['contacts_7d'])} of {inst.contact_policy.max_attempts_per_7d} used"})
    for name, rs in inst.eligibility.items():
        for desc, m in rs.rule_masks(df).items():
            checks.append({"group": name, "check": desc, "passed": bool(m.iloc[0]),
                           "detail": f"{desc.split(' ')[0]} = {row.get(desc.split(' ')[0])}"})
    return checks
