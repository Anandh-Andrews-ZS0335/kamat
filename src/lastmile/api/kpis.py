"""Business KPIs: seven questions a business analyst asks about collections, answered from what Last Mile stores.

Each KPI states how it is measured, where the numbers come from, and how sure we can be. Three facts shape them:
  * outcomes exist only once a 30-day window has closed, so recent days show "waiting", never a guess
  * there is no random holdout yet, so "because of Last Mile" cannot be claimed - results are "observed", and
    where a fair reference exists (accounts Last Mile did not contact, over the same window) it is shown beside them
  * the bank does not send call durations or dated complaint / opt-out events, so those parts are marked as gaps
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Query

from lastmile.api import services
from lastmile.config.resolve import resolve
from lastmile.governance import policy
from lastmile.ingest.bank_client import BankApiError
from lastmile.pipeline import run as pipeline
from lastmile.store import artifacts, db

router = APIRouter()
OUTCOME_DAYS = 30


def _days(window: int) -> list[dict]:
    """One row per business day of the primary institution: its live run and what was released."""
    inst = resolve().institution.institution.id
    with db.connect() as con:
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT as_of_date FROM runs WHERE institution = ? AND status IN ('awaiting_approval','released')"
            " AND as_of_date IS NOT NULL ORDER BY as_of_date DESC LIMIT ?", (inst, window))]
    days = []
    for d in sorted(dates):
        run = services.live_run_for(d)
        if not run:
            continue
        with db.connect() as con:
            released = db.rows(con, "SELECT r.account_token, r.action, c.member_token FROM releases r"
                                    " LEFT JOIN recommendations c ON c.run_id = r.run_id AND c.account_token = r.account_token"
                                    " WHERE r.run_id = ?", (run["run_id"],))
        days.append({"date": d, "run": run, "released": released, "closed": services.closure(d) is not None})
    return days


def _bank_actions(run_id: str) -> pd.DataFrame:
    """How each released action went, from the bank, keyed by our account token."""
    try:
        items = services.bank().get("/api/v1/collections/actions", {"source_run_id": run_id, "limit": 5000})["items"]
    except BankApiError:
        return pd.DataFrame()
    if not items:
        return pd.DataFrame()
    df = pd.DataFrame(items)
    ident = pd.read_parquet(artifacts.identity_path(run_id))[["account_id", "account_token"]].drop_duplicates()
    return df.merge(ident, left_on="ACCT_NBR", right_on="account_id", how="left")


def _frame(run_id: str, name: str) -> pd.DataFrame | None:
    return artifacts.load_frame(run_id, name) if artifacts.has_frame(run_id, name) else None


def _run_near(days: list[dict], target: date) -> dict | None:
    """The first day on or after target (within a week) - where '30 days later' is observed."""
    for d in days:
        if target <= date.fromisoformat(d["date"]) <= target + timedelta(days=7):
            return d
    return None


@router.get("/api/kpis")
def business_kpis(window: int = Query(60, ge=7, le=180)):
    cfg = resolve()
    days = _days(window)
    today = date.fromisoformat(days[-1]["date"]) if days else None
    per_day = []
    queue_latest, over_cap = None, None

    for i, d in enumerate(days):
        rid = d["run"]["run_id"]
        team = pipeline.run_roster(rid)
        team_minutes = team.total_minutes if team else cfg.institution.capacity.total_minutes
        plannable = team_minutes * (1 - cfg.institution.capacity.planning_buffer)
        items = services.worklist(rid)
        released_tokens = {r["account_token"] for r in d["released"]}
        minutes_of = {a.id: a.cost_minutes for a in cfg.scenario.actions}
        voice_released = [r for r in d["released"] if minutes_of.get(r["action"], 0) > 0]
        planned_minutes = sum(i_["minutes"] for i_ in items)
        released_minutes = sum(minutes_of.get(r["action"], 0) for r in d["released"])
        dec = artifacts.load_json(rid, "decision") if (artifacts.run_dir(rid) / "decision.json").exists() else {}
        acts = _bank_actions(rid) if d["released"] else pd.DataFrame()
        executed = acts[acts.get("EXECUTION_STATUS", pd.Series(dtype=str)) == "executed"] if len(acts) else acts
        voice_exec = executed[executed["ACTION_CD"] != "SMS"] if len(executed) else executed
        known = executed[executed["CURED_30D_IND"].notna()] if len(executed) else executed
        matures = date.fromisoformat(d["date"]) + timedelta(days=OUTCOME_DAYS)

        # KPI 3: 30 days later, did the account charge off or miss another payment? Compared with accounts not contacted.
        worse = {"status": "waiting", "contacted": None, "not_contacted": None}
        later = _run_near(days[i + 1:], matures)
        port = _frame(rid, "portfolio")
        if later is not None and port is not None and d["released"]:
            acc_later = _frame(later["run"]["run_id"], "canonical_accounts")
            if acc_later is not None:
                j = port[["account_token", "pmts_missed_12m"]].merge(
                    acc_later[["account_token", "status", "pmts_missed_12m"]], on="account_token", suffixes=("", "_later"))
                j["worse"] = (j["status"] == "CHARGED_OFF") | (j["pmts_missed_12m_later"] > j["pmts_missed_12m"])
                c = j[j["account_token"].isin(released_tokens)]
                n = j[~j["account_token"].isin(released_tokens)]
                worse = {"status": "measured", "contacted_n": int(len(c)), "contacted": float(c["worse"].mean()) if len(c) else None,
                         "not_contacted_n": int(len(n)), "not_contacted": float(n["worse"].mean()) if len(n) else None,
                         "observed_on": later["date"]}

        # KPI 6: segments left alone, hardship referrals, contacts per customer
        cand = _frame(rid, "candidates")
        sleeping = set(cand.loc[cand["segment"] == "sleeping_dog", "account_token"]) if cand is not None and "segment" in cand else set()
        members = pd.Series([r["member_token"] for r in d["released"] if r["member_token"]])

        # KPI 5: planned breaches, recomputed independently on the published plan
        plan, elig = _frame(rid, "plan_optimised"), _frame(rid, "eligibility")
        planned_breaches = None
        if plan is not None and elig is not None:
            rcfg = pipeline.run_config(rid)
            planned_breaches = sum(1 for v in policy.verify(plan, elig, rcfg)
                                   if v["rule"] in ("member_daily_contacts", "action_not_allowed"))

        per_day.append({
            "date": d["date"], "run_id": rid, "closed": d["closed"], "outcomes_from": matures.isoformat(),
            "collectors": len(team.working) if team else cfg.institution.capacity.agents,
            "team_minutes": team_minutes, "plannable_minutes": plannable, "planned_minutes": planned_minutes,
            "released_minutes": released_minutes, "recommended": len(items), "released": len(d["released"]),
            "voice_released": len(voice_released), "est_value_released": float(sum(i_["action_value"] for i_ in items if i_["released"])),
            "fits_shift": ((dec.get("des") or {}).get("optimised") or {}).get("completion_rate_mean"),
            "executed": int(len(executed)), "voice_executed": int(len(voice_exec)),
            "reached": int(voice_exec["RPC_IND"].fillna(0).sum()) if len(voice_exec) else 0,
            "outcomes_known": int(len(known)), "cured": int(known["CURED_30D_IND"].sum()) if len(known) else 0,
            "paid": float(known["AMT_PAID_30D"].fillna(0).sum()) if len(known) else 0.0,
            "worse": worse, "planned_breaches": planned_breaches,
            "hardship_released": sum(1 for r in d["released"] if r["action"] == "HARDSHIP"),
            "sleeping_dogs": len(sleeping), "sleeping_dogs_contacted": len(sleeping & released_tokens),
            "contacts_per_customer": float(members.value_counts().mean()) if len(members) else None,
        })

    # KPI 5 (actual) and KPI 6 (snapshots) come from the most recent run's view of the bank
    if days:
        last = days[-1]["run"]["run_id"]
        queue_latest = _frame(last, "canonical_queue_history")
        cons = _frame(last, "canonical_consents")
        cap = cfg.institution.contact_policy
        if queue_latest is not None:
            q = queue_latest[queue_latest["action"] != cfg.scenario.causal.control_value]
            since = (today - timedelta(days=30)).isoformat()
            q = q[q["queue_date"] >= since]
            per_member_day = q.groupby(["member_token", "queue_date"]).size()
            over = per_member_day[per_member_day > cap.max_contacts_per_member_per_day].reset_index()
            ours = {(r["member_token"], d_["date"]) for d_ in days for r in d_["released"] if r["member_token"]}
            over_cap = {"member_days_over_daily_cap": int(len(over)),
                        "involving_last_mile_release": int(sum((m, dt) in ours for m, dt in zip(over["member_token"], over["queue_date"], strict=True))),
                        "members_over_weekly_cap_now": int((cons["contacts_7d"] > cap.max_attempts_per_7d).sum()) if cons is not None else None,
                        "daily_cap": cap.max_contacts_per_member_per_day, "weekly_cap": cap.max_attempts_per_7d}

    port_first = _frame(days[0]["run"]["run_id"], "portfolio") if days else None
    port_last = _frame(days[-1]["run"]["run_id"], "portfolio") if days else None

    def snapshot(p):
        if p is None:
            return None
        return {"overdue_accounts": int(len(p)), "complaints_12m": int(p["complaints_12m"].sum()),
                "opted_out": int(((p.get("dnc", 0) == 1) | (p.get("cease_desist", 0) == 1)).sum())}

    # KPI 7: every released action can be explained from stored facts
    with db.connect() as con:
        recs = db.rows(con, "SELECT c.run_id, c.account_token, c.facts_json, c.rationale_tpl, r.released_at, r.released_by, r.action"
                            " FROM releases r JOIN recommendations c ON c.run_id = r.run_id AND c.account_token = r.account_token"
                            " ORDER BY r.released_at DESC LIMIT 400")
    explained = sum(1 for r in recs if r["rationale_tpl"] and all(v.get("source") for v in (db.loads(r["facts_json"]) or {}).values()))

    def total(key):
        return sum(p[key] for p in per_day)

    measured = [p for p in per_day if p["outcomes_known"]]
    hours_measured = sum(p["team_minutes"] for p in measured) / 60
    worse_days = [p["worse"] for p in per_day if p["worse"]["status"] == "measured"]
    wc = sum(w["contacted_n"] for w in worse_days)
    wn = sum(w["not_contacted_n"] for w in worse_days)

    kpis = [
        {"id": 1, "question": "Did we recover more money with the same collector effort?",
         "status": "partial" if measured else "waiting",
         "headline": {"paid_per_collector_hour": (total_paid := sum(p["paid"] for p in measured)) / hours_measured if hours_measured else None,
                      "paid_30d": total_paid, "collector_hours": hours_measured, "days_measured": len(measured)},
         "definition": "Amount paid within 30 days on released actions ÷ collector hours on those days.",
         "source": "Bank action outcomes (paid within 30 days) · team roster",
         "caveat": "Observed, not proven: comparing with 'the old way' needs a random holdout group, which is not set up yet. "
                   "The simulated benchmark shows +27% vs riskiest-first.",
         "gap": "Add a 5–10% random holdout handled the old way."},
        {"id": 2, "question": "How many overdue accounts became regular again?",
         "status": "measured" if measured else "waiting",
         "headline": {"cured": sum(p["cured"] for p in measured), "outcomes_known": sum(p["outcomes_known"] for p in measured),
                      "cure_rate": (sum(p["cured"] for p in measured) / sum(p["outcomes_known"] for p in measured)) if measured else None},
         "definition": "Released actions whose account caught up within 30 days ÷ released actions with a known outcome.",
         "source": "Bank action outcomes (recovered within 30 days)",
         "caveat": "An account can catch up without contact; the holdout would show how many did so because of it.", "gap": None},
        {"id": 3, "question": "How many contacts were followed by a worsening delinquency outcome?",
         "status": "measured" if worse_days else "waiting",
         "headline": {"contacted_worse_rate": (sum(w["contacted"] * w["contacted_n"] for w in worse_days) / wc) if wc else None,
                      "not_contacted_worse_rate": (sum(w["not_contacted"] * w["not_contacted_n"] for w in worse_days) / wn) if wn else None,
                      "contacted_n": wc, "not_contacted_n": wn},
         "definition": "Within 30 days of contact the account was charged off or missed another payment ÷ contacted accounts. "
                       "Shown beside the same rate for overdue accounts Last Mile did not contact.",
         "source": "This run's portfolio vs the bank's accounts ~30 days later (stored per run)",
         "caveat": "'Followed by', not 'caused by'. Accounts not contacted were chosen differently, so the comparison is context, not proof.",
         "gap": "Dated complaints after contact would complete this; the bank does not send them."},
        {"id": 4, "question": "How efficiently are we using collector time?",
         "status": "partial",
         "headline": {"planned_share": (total("planned_minutes") / sum(p["plannable_minutes"] for p in per_day)) if per_day and sum(p["plannable_minutes"] for p in per_day) else None,
                      "released_share": (total("released_minutes") / sum(p["plannable_minutes"] for p in per_day)) if per_day and sum(p["plannable_minutes"] for p in per_day) else None,
                      "reach_rate": (total("reached") / total("voice_executed")) if per_day and total("voice_executed") else None,
                      "fits_shift": (sum(p["fits_shift"] for p in per_day if p["fits_shift"] is not None) /
                                     max(1, sum(1 for p in per_day if p["fits_shift"] is not None))) if per_day else None,
                      "est_value_per_hour": (total("est_value_released") / (total("team_minutes") / 60)) if per_day and total("team_minutes") else None},
         "definition": "Planned and released minutes ÷ plannable minutes; share of each queue finishing in shift (simulated); "
                       "calls that reached the customer ÷ calls made.",
         "source": "Collector queues · shift simulation · bank reached-customer flag",
         "caveat": "Minutes are standard handling times; the bank does not send real call durations.",
         "gap": "Actual call durations from the bank's dialler."},
        {"id": 5, "question": "Did any customer receive more contacts than allowed?",
         "status": "measured" if over_cap is not None else "waiting",
         "headline": {"planned_breaches": sum(p["planned_breaches"] or 0 for p in per_day), **(over_cap or {})},
         "definition": "Planned: Last Mile's published plans re-checked against contact rules. Actual: customers with more "
                       "contacts per day than allowed in the bank's own contact log (last 30 days), and whether a Last Mile release was involved.",
         "source": "Policy verification on every plan · bank collections history · contact counts",
         "caveat": "Actual breaches can come from the bank's own process, outside Last Mile.", "gap": None},
        {"id": 6, "question": "What customer impact are we seeing?",
         "status": "partial",
         "headline": {"hardship_referrals": total("hardship_released"), "sleeping_dogs": total("sleeping_dogs"),
                      "sleeping_dogs_contacted": total("sleeping_dogs_contacted"),
                      "contacts_per_customer": (sum(p["contacts_per_customer"] for p in per_day if p["contacts_per_customer"]) /
                                                max(1, sum(1 for p in per_day if p["contacts_per_customer"]))) if per_day else None,
                      "portfolio_then": snapshot(port_first), "portfolio_now": snapshot(port_last)},
         "definition": "Hardship referrals made; members predicted to react badly to contact ('sleeping dogs') and how many were "
                       "still contacted; contacts per customer; complaints and opt-outs across the overdue book, first vs latest day.",
         "source": "Released actions · model segments · bank accounts and consents",
         "caveat": "Complaints and opt-outs are totals at two points in time, not events linked to a contact.",
         "gap": "Dated complaint and opt-out events from the bank."},
        {"id": 7, "question": "Can we quickly explain why a particular customer was contacted?",
         "status": "measured",
         "headline": {"released_checked": len(recs), "fully_explained": explained},
         "definition": "Released actions that have a stored reason and a source for every figure in it. Any one opens in seconds.",
         "source": "Recommendations · provenance stamps · approvals and audit trail", "caveat": None, "gap": None,
         "recent": [{"run_id": r["run_id"], "account_token": r["account_token"], "action": r["action"],
                     "released_at": r["released_at"], "released_by": r["released_by"]} for r in recs[:8]]},
    ]
    return {"institution": cfg.institution.institution.name, "window_days": len(per_day),
            "from": per_day[0]["date"] if per_day else None, "to": per_day[-1]["date"] if per_day else None,
            "outcome_days": OUTCOME_DAYS, "holdout": False, "kpis": kpis, "days": per_day}
