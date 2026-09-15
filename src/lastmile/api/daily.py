"""The manager's business day: today's team, re-planning when it changes, closing the day, daily reports.

A day moves through five steps, all started by the collections manager:
  1. confirm today's team (roster)   2. start today's run   3. review and approve
  4. release approved actions        5. close the day - the report is frozen and the bank opens the next day
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lastmile.api import services
from lastmile.config.resolve import resolve
from lastmile.config.schema import Collector, Roster
from lastmile.governance import audit
from lastmile.ingest.bank_client import BankApiError
from lastmile.pipeline import run as pipeline
from lastmile.store import db, rosters

router = APIRouter()


def _bank_date() -> str:
    try:
        return services.bank().health()["as_of"]
    except BankApiError as e:
        raise HTTPException(502, f"cannot read today's date from the bank: {e}") from e


# ---------------------------------------------------------------------------------------------- day
@router.get("/api/day")
def day_status():
    cfg = resolve()
    try:
        today, bank_error = services.bank().health()["as_of"], None
    except BankApiError as e:
        today, bank_error = None, str(e)
    with db.connect() as con:
        row = con.execute("SELECT business_date, closed_by, closed_at FROM day_closures ORDER BY business_date DESC LIMIT 1").fetchone()
        last = dict(row) if row else None
    if not today:
        return {"bank_ok": False, "bank_error": bank_error, "last_closed": last}

    team = rosters.for_date(today, cfg.institution)
    run = services.live_run_for(today, include_running=True)
    closed = services.closure(today)
    items = services.worklist(run["run_id"]) if run and run["status"] in services.LIVE else []
    pending = sum(1 for i in items if i["decision"] == "pending")
    ready = sum(1 for i in items if i["decision"] in ("approved", "edited") and not i["released"])
    released = sum(1 for i in items if i["released"])
    run_team = pipeline.run_roster(run["run_id"]) if run else None
    team_changed = bool(run_team and run["status"] == "awaiting_approval"
                        and services.team_signature(run_team) != services.team_signature(team))

    def step(sid, title, status, detail):
        return {"id": sid, "title": title, "status": status, "detail": detail}

    run_status = run["status"] if run else None
    steps = [
        step("roster", "Confirm today's team", "done" if team.source == "saved" else "todo",
             f"{len(team.working)} collectors, {team.total_minutes:,} minutes"
             + ("" if team.source == "saved" else f" ({team.source.replace('_', ' ')} - check and save)")),
        step("run", "Start today's run", {"awaiting_approval": "done", "released": "done", "running": "running"}.get(run_status, "todo"),
             run["run_id"] if run else "not started"),
        step("review", "Review and approve", "done" if items and not pending else ("todo" if items else "waiting"),
             f"{len(items) - pending} of {len(items)} decided" if items else "after the run"),
        step("release", "Release approved actions", "done" if released and not ready else ("todo" if ready else "waiting"),
             f"{released} released, {ready} ready" if items else "after approval"),
        step("close", "Close the day", "done" if closed else ("todo" if released else "waiting"),
             "report frozen, bank moves to the next day" if not closed else f"closed by {closed['closed_by']}"),
    ]
    return {"bank_ok": True, "business_date": today, "closed": closed is not None, "last_closed": last,
            "simulation": cfg.institution.source.simulation_endpoint is not None,
            "roster": team.model_dump(), "run": {k: run[k] for k in ("run_id", "status", "started_at", "parent_run_id")} if run else None,
            "team_changed_since_run": team_changed, "counts": {"items": len(items), "pending": pending, "ready": ready,
                                                               "released": released},
            "planning_buffer": cfg.institution.capacity.planning_buffer, "steps": steps}


# ------------------------------------------------------------------------------------------ roster
class RosterIn(BaseModel):
    collectors: list[Collector] = Field(min_length=1)
    saved_by: str = "manager.demo"
    note: str | None = None


@router.get("/api/roster/{roster_date}")
def get_roster(roster_date: str):
    cfg = resolve()
    team = rosters.for_date(roster_date, cfg.institution)
    return {"roster": team.model_dump(), "plannable_minutes": team.total_minutes * (1 - cfg.institution.capacity.planning_buffer),
            "planning_buffer": cfg.institution.capacity.planning_buffer, "history": rosters.history(roster_date)}


@router.put("/api/roster/{roster_date}")
def save_roster(roster_date: str, body: RosterIn):
    cfg = resolve()
    try:
        Roster(roster_date=roster_date, collectors=body.collectors)   # same validation the engine applies
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    before = rosters.for_date(roster_date, cfg.institution)
    team = rosters.save(roster_date, body.collectors, body.saved_by, body.note)
    audit.append(None, "roster.saved", body.saved_by, {
        "roster_date": roster_date, "note": body.note,
        "before": {"source": before.source, "working": [c.id for c in before.working], "total_minutes": before.total_minutes},
        "after": {"working": [c.id for c in team.working], "total_minutes": team.total_minutes,
                  "collectors": [c.model_dump() for c in team.collectors]}})
    run = services.live_run_for(roster_date)
    run_team = pipeline.run_roster(run["run_id"]) if run else None
    return {"roster": team.model_dump(), "plannable_minutes": team.total_minutes * (1 - cfg.institution.capacity.planning_buffer),
            "replan_suggested": bool(run and run["status"] == "awaiting_approval"
                                     and services.team_signature(run_team) != services.team_signature(team)),
            "run_id": run["run_id"] if run else None}


# ------------------------------------------------------------------------------------------ re-plan
class ReplanIn(BaseModel):
    requested_by: str = "manager.demo"


@router.post("/api/runs/{run_id}/replan")
def replan(run_id: str, body: ReplanIn | None = None):
    body = body or ReplanIn()
    parent = services.get_run(run_id)
    if parent["status"] != "awaiting_approval":
        raise HTTPException(409, f"only a worklist awaiting approval can be re-planned (this one is {parent['status']})")
    if services.running_run():
        raise HTTPException(409, "a run is in progress; wait for it to finish")
    if services.closure(parent["as_of_date"]):
        raise HTTPException(409, f"business day {parent['as_of_date']} is closed")
    with db.connect() as con:
        if con.execute("SELECT 1 FROM releases WHERE run_id = ? LIMIT 1", (run_id,)).fetchone():
            raise HTTPException(409, "some actions were already sent to the bank; re-planning could contact members twice")
        decided = con.execute("SELECT COUNT(DISTINCT account_token) FROM approvals WHERE run_id = ?", (run_id,)).fetchone()[0]
    cfg = resolve()
    team = rosters.for_date(parent["as_of_date"], cfg.institution)
    if services.team_signature(team) == services.team_signature(pipeline.run_roster(run_id)):
        raise HTTPException(409, "the saved team is the same one this worklist was planned for; nothing to re-plan")
    audit.append(run_id, "run.replan_requested", body.requested_by, {
        "decisions_discarded": decided, "working": [c.id for c in team.working], "total_minutes": team.total_minutes})
    new_id = pipeline.start_replan_background(run_id, team)
    return {"run_id": new_id, "parent_run_id": run_id, "decisions_discarded": decided}


# --------------------------------------------------------------------------------------- close day
class CloseIn(BaseModel):
    closed_by: str = "manager.demo"
    confirm_unreleased: bool = False
    note: str | None = None


@router.post("/api/day/close")
def close_day(body: CloseIn | None = None):
    body = body or CloseIn()
    cfg = resolve()
    today = _bank_date()
    if services.closure(today):
        raise HTTPException(409, f"business day {today} is already closed")
    if services.running_run():
        raise HTTPException(409, "a run is in progress; wait for it to finish before closing the day")
    run = services.live_run_for(today)
    items = services.worklist(run["run_id"]) if run else []
    ready = sum(1 for i in items if i["decision"] in ("approved", "edited") and not i["released"])
    undecided = sum(1 for i in items if i["decision"] == "pending")
    if not body.confirm_unreleased:
        if not run:
            raise HTTPException(409, f"no worklist was produced for {today}. Close anyway?")
        if ready or undecided:
            parts = ([f"{ready} approved action(s) have not been released"] if ready else []) + (
                [f"{undecided} recommendation(s) were never decided"] if undecided else [])
            raise HTTPException(409, " and ".join(parts) + "; they will not happen. Close anyway?")
    report = services.build_report(today)
    report["closed_note"] = body.note
    endpoint = cfg.institution.source.simulation_endpoint
    bank_response = None
    if endpoint:
        try:
            bank_response = services.bank().post(endpoint, {})
        except BankApiError as e:
            raise HTTPException(502, f"the bank did not close the day: {e}") from e
    closed_at = db.now()
    with db.connect() as con:
        con.execute("INSERT INTO day_closures (business_date, run_id, closed_by, closed_at, report_json, bank_response_json)"
                    " VALUES (?,?,?,?,?,?)", (today, run["run_id"] if run else None, body.closed_by, closed_at,
                                              db.dumps(report), db.dumps(bank_response)))
    audit.append(run["run_id"] if run else None, "day.closed", body.closed_by, {
        "business_date": today, "note": body.note, "unreleased_approved": ready, "undecided": undecided,
        "released": (report.get("totals") or {}).get("released", 0), "bank": bank_response})
    return {"closed": today, "next_business_date": (bank_response or {}).get("as_of"), "bank_response": bank_response,
            "report_url": f"/report?date={today}"}


# ----------------------------------------------------------------------------------------- reports
@router.get("/api/reports")
def list_reports():
    with db.connect() as con:
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT as_of_date FROM runs WHERE as_of_date IS NOT NULL UNION SELECT business_date FROM day_closures"
            " ORDER BY 1 DESC")]
        closures = {r["business_date"]: dict(r) for r in con.execute("SELECT business_date, closed_by, closed_at, report_json FROM day_closures")}
    out = []
    for d in dates:
        c = closures.get(d)
        rep = db.loads(c["report_json"]) if c else None
        run = services.live_run_for(d)
        totals = (rep or {}).get("totals")
        if totals is None and run:
            items = services.worklist(run["run_id"])
            totals = {"recommended": len(items), "est_value": sum(i["action_value"] for i in items),
                      "approved": sum(1 for i in items if i["decision"] == "approved"),
                      "edited": sum(1 for i in items if i["decision"] == "edited"),
                      "rejected": sum(1 for i in items if i["decision"] == "rejected"),
                      "released": sum(1 for i in items if i["released"])}
        out.append({"business_date": d, "closed": c is not None, "closed_by": c["closed_by"] if c else None,
                    "closed_at": c["closed_at"] if c else None,
                    "run_id": (rep["run"] or {}).get("run_id") if rep else (run["run_id"] if run else None),
                    "collectors_working": ((rep or {}).get("team") or {}).get("working"), "totals": totals})
    return out


@router.get("/api/reports/{business_date}")
def get_report(business_date: str):
    c = services.closure(business_date)
    report = db.loads(c["report_json"]) if c else services.build_report(business_date)
    report["status"] = "final" if c else "live"
    if c:
        report.update({"closed_by": c["closed_by"], "closed_at": c["closed_at"], "bank_close": db.loads(c["bank_response_json"])})
    with db.connect() as con:
        released_runs = [r[0] for r in con.execute(
            "SELECT DISTINCT r.run_id FROM releases r JOIN runs u ON u.run_id = r.run_id WHERE u.as_of_date = ?", (business_date,))]
    report["follow_up"] = services.follow_up(released_runs)   # always live: execution and cures arrive after the day
    return report
