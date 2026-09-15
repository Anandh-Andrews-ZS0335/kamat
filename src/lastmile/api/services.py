"""Helpers shared by the API routers: bank client, run lookup, the worklist view and the daily report."""

from __future__ import annotations

import json

import pandas as pd
from fastapi import HTTPException

from lastmile.config.resolve import resolve
from lastmile.config.settings import bank_base_url
from lastmile.engine.collectors import AUTOMATED
from lastmile.governance import approvals, audit
from lastmile.ingest.bank_client import BankApiError, BankClient
from lastmile.pipeline.run import run_roster
from lastmile.store import artifacts, db, rosters

LIVE = ("awaiting_approval", "released")


def bank() -> BankClient:
    src = resolve().institution.source
    return BankClient(bank_base_url(src.base_url_env, src.default_base_url), src.page_size, timeout=5)


def get_run(run_id: str) -> dict:
    with db.connect() as con:
        r = con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, f"run {run_id} not found")
    return dict(r)


def clean(v):
    if isinstance(v, float) and v != v:
        return None
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: clean(v) for k, v in row.items()} for row in json.loads(df.to_json(orient="records", date_format="iso"))]


def worklist(run_id: str) -> list[dict]:
    with db.connect() as con:
        recs = db.rows(con, "SELECT * FROM recommendations WHERE run_id = ? ORDER BY rank", (run_id,))
        released = {r["account_token"]: r for r in db.rows(con, "SELECT * FROM releases WHERE run_id = ?", (run_id,))}
    latest = approvals.latest(run_id)
    team = run_roster(run_id)
    names = {c.id: c.name for c in team.collectors} if team else {}
    names[AUTOMATED] = "Automated SMS"
    out = []
    for r in recs:
        f = db.loads(r["facts_json"])
        a = latest.get(r["account_token"])
        esc = db.loads(r["escalations_json"]) or []
        cid = r.get("collector_id")
        out.append({"rank": r["rank"], "account_token": r["account_token"], "member_token": r["member_token"],
                    "action": r["action"], "action_label": f["action_label"]["value"], "segment": r["segment"],
                    "segment_label": f["segment_label"]["value"], "product": f["product_label"]["value"],
                    "exposure": f["exposure"]["value"], "dpd": f["dpd"]["value"], "risk_grade": f["risk_grade"]["value"],
                    "expected_loss": f["expected_loss"]["value"], "uplift": f["uplift"]["value"],
                    "uplift_low": f["uplift_low"]["value"], "uplift_high": f["uplift_high"]["value"],
                    "base_cure": f["base_cure"]["value"], "action_value": f["action_value"]["value"],
                    "minutes": f["minutes"]["value"], "escalations": esc, "template_source": r["template_source"],
                    "collector_id": cid, "collector_name": names.get(cid, cid) if cid else None,
                    "queue_position": r.get("queue_position"),
                    "decision": a["decision"] if a else "pending", "final_action": a["final_action"] if a else None,
                    "approver": a["approver_id"] if a else None, "released": r["account_token"] in released})
    return out


def queues(run_id: str, items: list[dict]) -> list[dict]:
    """Per collector: what was planned, what the manager has decided so far, and whether it still fits the shift."""
    cfg = resolve()
    cfg.roster = run_roster(run_id)
    minutes_of = {a.id: a.cost_minutes for a in cfg.scenario.actions}
    try:
        simulated = {q["collector_id"]: q.get("completion_simulated") for q in artifacts.load_json(run_id, "decision")["queues"]}
    except (FileNotFoundError, KeyError):
        simulated = {}
    rows = []
    for c in [*cfg.team.collectors, None]:
        cid = c.id if c else AUTOMATED
        mine = [i for i in items if i["collector_id"] == cid]
        if c is None and not mine:
            continue
        dec = pd.Series([i["decision"] for i in mine], dtype=object).value_counts().to_dict()
        committed = sum(minutes_of.get(i["final_action"], 0) for i in mine if i["decision"] in ("approved", "edited"))
        pending = sum(i["minutes"] for i in mine if i["decision"] == "pending")
        plannable = cfg.plannable_for(c) if c and c.present else (0.0 if c else None)
        rows.append({
            "collector_id": cid, "name": c.name if c else "Automated SMS", "present": c.present if c else True,
            "shift_minutes": c.shift_minutes if c else None, "plannable_minutes": plannable,
            "tasks": len(mine), "planned_minutes": float(sum(i["minutes"] for i in mine)),
            "est_value": float(sum(i["action_value"] for i in mine)),
            "approved": dec.get("approved", 0), "edited": dec.get("edited", 0), "rejected": dec.get("rejected", 0),
            "pending": dec.get("pending", 0), "released": sum(1 for i in mine if i["released"]),
            "committed_minutes": float(committed), "pending_minutes": float(pending),
            # an edit can swap a 12-minute call for a 45-minute referral: flag queues the manager's changes overfill
            "over_shift": bool(c and plannable is not None and committed + pending > plannable + 1e-6),
            "completion_simulated": simulated.get(cid),
            "actions": pd.Series([i["action"] for i in mine], dtype=object).value_counts().to_dict(),
        })
    return rows


# ------------------------------------------------------------------------------------ business day
def live_run_for(business_date: str, include_running: bool = False) -> dict | None:
    with db.connect() as con:
        if include_running:
            # a run learns its business date from the bank a few seconds in; until then it has none
            r = con.execute("SELECT * FROM runs WHERE status = 'running' AND (as_of_date = ? OR as_of_date IS NULL)"
                            " ORDER BY started_at DESC LIMIT 1", (business_date,)).fetchone()
            if r:
                return dict(r)
        r = con.execute(f"SELECT * FROM runs WHERE as_of_date = ? AND status IN ({','.join('?' * len(LIVE))})"
                        " ORDER BY started_at DESC LIMIT 1", (business_date, *LIVE)).fetchone()
    return dict(r) if r else None


def running_run() -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def closure(business_date: str) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM day_closures WHERE business_date = ?", (business_date,)).fetchone()
    return dict(r) if r else None


def team_signature(team) -> list:
    return sorted((c.id, c.present, c.shift_minutes) for c in team.collectors) if team else []


def follow_up(run_ids: list[str]) -> dict:
    """How released actions went, from the bank. Execution is known once the day closes; a cure only after 30 days."""
    if not run_ids:
        return {"available": True, "released": 0}
    try:
        items = [it for rid in run_ids for it in bank().get("/api/v1/collections/actions",
                                                             {"source_run_id": rid, "limit": 5000})["items"]]
    except BankApiError as e:
        return {"available": False, "error": str(e)}
    executed = [i for i in items if i.get("EXECUTION_STATUS") == "executed"]
    voice = [i for i in executed if i["ACTION_CD"] != "SMS"]
    known = [i for i in executed if i.get("CURED_30D_IND") is not None]
    return {"available": True, "released": len(items), "awaiting_execution": sum(1 for i in items if not i.get("EXECUTED_DT")),
            "executed": len(executed), "not_executed": sum(1 for i in items if i.get("EXECUTED_DT") and i.get("EXECUTION_STATUS") != "executed"),
            "voice_executed": len(voice), "right_party_contacts": sum(int(i.get("RPC_IND") or 0) for i in voice),
            "outcomes_known": len(known), "cured_30d": sum(int(i["CURED_30D_IND"]) for i in known),
            "amount_paid_30d": float(sum(i.get("AMT_PAID_30D") or 0 for i in known))}


def build_report(business_date: str) -> dict:
    cfg = resolve()
    run = live_run_for(business_date)
    with db.connect() as con:
        today = db.rows(con, "SELECT run_id, status, started_at, finished_at, parent_run_id, superseded_by, roster_json, error"
                             " FROM runs WHERE as_of_date = ? ORDER BY started_at", (business_date,))
    runs_today = []
    for r in today:
        team = db.loads(r.pop("roster_json"))
        r["collectors_working"] = sum(1 for c in (team or {}).get("collectors", []) if c["present"] and c["shift_minutes"] > 0)
        r["team_minutes"] = sum(c["shift_minutes"] for c in (team or {}).get("collectors", []) if c["present"])
        runs_today.append(r)
    report = {"business_date": business_date, "institution": cfg.institution.institution.name,
              "scenario": cfg.scenario.scenario.title, "generated_at": db.now(), "runs_today": runs_today, "run": None}
    if not run:
        team = rosters.for_date(business_date, cfg.institution)
        report["roster"] = team.model_dump()
        return report

    items = worklist(run["run_id"])
    team = run_roster(run["run_id"])
    cfg.roster = team
    labels = {a.id: a.label for a in cfg.scenario.actions}
    dec = artifacts.load_json(run["run_id"], "decision")
    mc = (dec.get("mc") or {}).get("optimised") or {}
    counts = pd.Series([i["decision"] for i in items], dtype=object).value_counts().to_dict()
    final = [i for i in items if i["decision"] in ("approved", "edited")]
    with db.connect() as con:
        rel = con.execute("SELECT MIN(released_at) first, MAX(released_at) last, COUNT(*) n FROM releases WHERE run_id = ?",
                          (run["run_id"],)).fetchone()
    plans = {k: artifacts.load_frame(run["run_id"], f"plan_{k}") for k in ("optimised", "sort_by_risk", "sort_by_value")}
    report.update({
        "run": {k: run[k] for k in ("run_id", "status", "llm_mode", "config_hash", "model_run_id", "parent_run_id",
                                    "started_at", "finished_at")},
        "roster": team.model_dump() if team else None,
        "team": {"working": len(cfg.team.working), "total_minutes": cfg.total_minutes,
                 "plannable_minutes": cfg.plannable_minutes, "planning_buffer": cfg.institution.capacity.planning_buffer},
        "totals": {
            "recommended": len(items), "est_value": float(sum(i["action_value"] for i in items)),
            "mc_p10": mc.get("value_p10"), "mc_p50": mc.get("value_p50"), "mc_p90": mc.get("value_p90"),
            "planned_minutes": float(sum(i["minutes"] for i in items)),
            "voice_tasks": sum(1 for i in items if i["minutes"] > 0), "sms": sum(1 for i in items if i["action"] == "SMS"),
            "escalated": sum(1 for i in items if i["escalations"]),
            "approved": counts.get("approved", 0), "edited": counts.get("edited", 0), "rejected": counts.get("rejected", 0),
            "pending": counts.get("pending", 0), "released": int(rel["n"] or 0),
            "approved_est_value": float(sum(i["action_value"] for i in final if i["decision"] == "approved")),
            "first_release_at": rel["first"], "last_release_at": rel["last"],
        },
        "queues": queues(run["run_id"], items),
        "actions": [{"action": a, "label": labels.get(a, a),
                     "recommended": sum(1 for i in items if i["action"] == a),
                     "final": sum(1 for i in final if i["final_action"] == a),
                     "released": sum(1 for i in items if i["released"] and (i["final_action"] or i["action"]) == a)}
                    for a in labels],
        "overrides": approvals.override_signals(run["run_id"]),
        "comparison": {k: {"selected": int(len(p)), "est_value": float(p["value"].sum())} for k, p in plans.items()},
        "simulation": {"fits_shift": (dec.get("des") or {}).get("optimised", {}).get("completion_rate_mean")},
        "audit": {"events_for_run": len(audit.events(run["run_id"])), "chain": audit.verify_chain()},
    })
    return report
