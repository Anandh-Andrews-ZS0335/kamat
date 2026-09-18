"""Last Mile engine API and consoles.

  /demo     - Demo & Showcase Console: self-explanatory presentation & interactive workbench
  /admin    - Admin Console: every stage, every agent, every tool call, every intermediate table
  /manager  - Manager Console: today's team, validate rankings and outputs, approve / edit / reject, release, close the day
  /report   - Daily report: one page per business day, frozen when the manager closes the day
  /admin/config - Configuration editor: the three YAML packs, explained, checked before saving
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lastmile.agents import crew, provenance
from lastmile.agents.llm.factory import get_llm
from lastmile.agents.tools import build_registry
from lastmile.agents.trace import Tracer
from lastmile.api import auth, config_editor, daily, kpis, onboarding, services
from lastmile.config.resolve import resolve
from lastmile.config.settings import ROOT
from lastmile.governance import approvals, audit, policy, release
from lastmile.ingest.bank_client import BankApiError
from lastmile.pipeline import run as pipeline
from lastmile.store import artifacts, db, llm_calls

STATIC = Path(__file__).parent / "static"


_ASSET = re.compile(r'(/static/[\w.-]+\.(?:css|js))"')


def page(name: str) -> HTMLResponse:
    """Serve a console page with its stylesheets and scripts versioned by file time.

    A browser that cached an older build cannot serve it back: the URL itself changes when the file does.
    """
    def version(match: re.Match) -> str:
        asset = STATIC / Path(match.group(1)).name
        stamp = int(asset.stat().st_mtime) if asset.exists() else 0
        return f'{match.group(1)}?v={stamp}"'

    html = _ASSET.sub(version, (STATIC / name).read_text())
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
app = FastAPI(title="Last Mile - Agentic Prescriptive Analytics", version="1.0.0")
app.add_middleware(auth.AuthMiddleware)
class RevalidatingStatic(StaticFiles):
    """Browsers must re-check the consoles' CSS and JS. ETags keep that cheap, and a deploy is never half-stale."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStatic(directory=STATIC), name="static")
app.include_router(auth.router)
app.include_router(daily.router)
app.include_router(config_editor.router)
app.include_router(onboarding.router)
app.include_router(kpis.router)
db.init()


# ------------------------------------------------------------------------------ pages
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/demo")


@app.get("/demo", include_in_schema=False)
def demo_page():
    return page("demo.html")


@app.get("/guide", include_in_schema=False)
def guide_page():
    """Guided tour: plain-language, client-facing walkthrough of a run. Technical detail on demand."""
    return page("guide.html")


@app.get("/presentation", include_in_schema=False)
def presentation_page():
    """A concise, client-facing introduction to the collections decision."""
    return page("risk-score-presentation.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return page("admin.html")


@app.get("/manager", include_in_schema=False)
def manager_page():
    return page("manager.html")


@app.get("/report", include_in_schema=False)
def report_page():
    return page("report.html")


@app.get("/kpis", include_in_schema=False)
def kpis_page():
    return page("kpis.html")


@app.get("/admin/onboarding", include_in_schema=False)
def onboarding_page():
    return page("onboarding.html")


@app.get("/admin/config", include_in_schema=False)
def config_page():
    return page("config.html")


# ----------------------------------------------------------------------------- status
@app.get("/api/status")
def status():
    _, mode = get_llm()
    try:
        bank = {"ok": True, **services.bank().health()}
    except BankApiError as e:
        bank = {"ok": False, "error": str(e)}
    cfg = resolve()
    return {"bank": bank, "llm_mode": mode, "config_hash": cfg.config_hash, "institution_id": cfg.institution.institution.id,
            "scenario": cfg.scenario.scenario.title, "institution": cfg.institution.institution.name}


@app.get("/api/agents")
def agents():
    reg = build_registry()
    tools = reg.describe()
    from lastmile.agents.onboarding import OnboardingAgent  # works once per new bank, outside the daily run
    return {"agents": [c.describe() | {"tools": [t["name"] for t in tools if c.name in t["owners"]]}
                       for c in [*crew.AGENT_CLASSES, OnboardingAgent]],
            "tools": tools}


# ------------------------------------------------------------------------------- runs
@app.post("/api/runs")
def start_run():
    busy = services.running_run()
    if busy:
        raise HTTPException(409, f"run {busy['run_id']} is still running")
    try:
        today = services.bank().health()["as_of"]
    except BankApiError:
        today = None  # the run itself reports the bank as unreachable, traced
    if today:
        if services.closure(today):
            raise HTTPException(409, f"business day {today} is already closed; the bank has not opened the next day yet")
        live = services.live_run_for(today)
        if live and live["status"] == "released":
            raise HTTPException(409, f"today's actions ({today}) were already released from {live['run_id']}; "
                                     "close the day in the Manager Console to move to the next one")
    return {"run_id": pipeline.start_background()}


@app.get("/api/runs")
def list_runs(limit: int = 30, institution: str | None = None):
    where, params = ("WHERE institution = ?", (institution,)) if institution else ("", ())
    with db.connect() as con:
        return db.rows(con, "SELECT run_id, status, as_of_date, started_at, finished_at, config_hash, model_run_id, llm_mode,"
                            f" error, parent_run_id, superseded_by, institution FROM runs {where} ORDER BY started_at DESC LIMIT ?",
                       (*params, limit))


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = services.get_run(run_id)
    with db.connect() as con:
        stages = db.rows(con, "SELECT * FROM stages WHERE run_id = ? ORDER BY ord", (run_id,))
        calls = db.rows(con, "SELECT agent, COUNT(*) n, SUM(ms) ms FROM trace_events WHERE run_id = ? AND tool IS NOT NULL"
                             " GROUP BY agent", (run_id,))
    for s in stages:
        for k in ("agents_json", "metrics_json", "notes_json", "artifacts_json"):
            s[k.removesuffix("_json")] = db.loads(s.pop(k))
    run["summary"] = db.loads(run.pop("summary_json"))
    return {"run": run, "stages": stages, "agent_calls": calls}


@app.get("/api/runs/{run_id}/trace")
def trace(run_id: str, after: int = 0, limit: int = 500):
    with db.connect() as con:
        ev = db.rows(con, "SELECT * FROM trace_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?", (run_id, after, limit))
    for e in ev:
        e["input"], e["output"] = db.loads(e.pop("input_json")), db.loads(e.pop("output_json"))
    return ev


@app.get("/api/runs/{run_id}/artifacts/{name}")
def artifact(run_id: str, name: str, limit: int = Query(50, le=500), offset: int = 0):
    run = get_run(run_id)
    meta = next((a for s in run["stages"] for a in (s["artifacts"] or []) if a["name"] == name), None)
    if meta is None:
        raise HTTPException(404, f"artifact {name} not registered for this run")
    df = artifacts.load_frame(run_id, name)
    page = df.iloc[offset: offset + limit].copy()
    for col in meta.get("mask_columns", []):
        if col in page.columns:
            page[col] = "••• masked (PII)"
    return {"name": name, "rows": int(len(df)), "offset": offset, "columns": [str(c) for c in df.columns],
            "masked": meta.get("mask_columns", []), "items": services.records(page)}


@app.get("/api/runs/{run_id}/config")
def run_config(run_id: str):
    try:
        return artifacts.load_json(run_id, "resolved_config")
    except FileNotFoundError as e:
        raise HTTPException(404, "configuration stage has not completed for this run") from e


# -------------------------------------------------------------------- manager: worklist
@app.get("/api/runs/{run_id}/worklist")
def worklist(run_id: str):
    run = services.get_run(run_id)
    items = services.worklist(run_id)
    dec = artifacts.load_json(run_id, "decision") if (artifacts.run_dir(run_id) / "decision.json").exists() else {}
    counts = pd.Series([i["decision"] for i in items]).value_counts().to_dict() if items else {}
    cfg = pipeline.run_config(run_id)
    return {"run": {k: run[k] for k in ("run_id", "status", "as_of_date", "llm_mode", "model_run_id", "config_hash",
                                        "parent_run_id", "superseded_by")},
            "day_closed": services.closure(run["as_of_date"]) is not None if run["as_of_date"] else False,
            "roster": cfg.team.model_dump(), "queues": services.queues(run_id, items),
            "summary": {"selected": len(items), "est_value": sum(i["action_value"] for i in items),
                        "minutes": sum(i["minutes"] for i in items),
                        "plannable_minutes": cfg.plannable_minutes, "collectors_working": len(cfg.team.working),
                        "escalated": sum(1 for i in items if i["escalations"]), "decisions": counts,
                        "released": sum(1 for i in items if i["released"]),
                        "mc": dec.get("mc", {}).get("optimised")},
            "reason_codes": cfg.institution.approval.reason_codes, "items": items}


@app.get("/api/runs/{run_id}/accounts/{token}")
def account(run_id: str, token: str):
    cfg = pipeline.run_config(run_id)
    port = artifacts.load_frame(run_id, "portfolio")
    row = port[port["account_token"] == token]
    if row.empty:
        raise HTTPException(404, "account not in this run's portfolio")
    row = row.iloc[0]
    cand = artifacts.load_frame(run_id, "candidates")
    c = cand[cand["account_token"] == token].sort_values("value", ascending=False)
    with db.connect() as con:
        rec = con.execute("SELECT * FROM recommendations WHERE run_id = ? AND account_token = ?", (run_id, token)).fetchone()
    history = approvals.latest(run_id).get(token)
    out = {
        "account_token": token, "member_token": row["member_token"],
        "attributes": {k: services.clean(row[k].item() if hasattr(row[k], "item") else row[k]) for k in
                       ["product", "secured", "orig_amt", "curr_bal", "dpd", "risk_grade", "pd_12m", "pd_h", "lgd",
                        "pmts_missed_12m", "mos_since_last_pmt", "ptp_kept_rate_12m", "complaints_12m", "tenure_mos",
                        "direct_deposit", "member_accounts", "phone_consent", "sms_consent", "contacts_7d", "hardship_active"]},
        "alternatives": services.records(c[["action", "allowed", "blocked_reasons", "value", "uplift", "uplift_lower", "uplift_upper",
                                    "p0", "minutes", "segment"]]),
        "checks": policy.account_checks(row, cfg), "recommendation": None, "approval": history,
    }
    if rec:
        facts = db.loads(rec["facts_json"])
        ident = pd.read_parquet(artifacts.identity_path(run_id))
        first = ident.loc[ident["account_token"] == token, "first_name"]
        script_facts = {**facts, "first_name": provenance.stamp("first_name", first.iloc[0] if len(first) else "there",
                                                                  "identity:members (rejoined for script only)", run_id)}
        out["recommendation"] = {
            "rank": rec["rank"], "action": rec["action"], "segment": rec["segment"], "template_source": rec["template_source"],
            "escalations": db.loads(rec["escalations_json"]), "facts": facts,
            "rationale": provenance.render_segments(rec["rationale_tpl"], facts),
            "script": provenance.render_segments(rec["script_tpl"], script_facts),
            "rationale_template": rec["rationale_tpl"], "script_template": rec["script_tpl"],
            "allowed_actions": [a for a in c[c["allowed"]]["action"].tolist()],
        }
    return out


@app.post("/api/runs/{run_id}/accounts/{token}/explain")
def explain(run_id: str, token: str):
    return pipeline.explain_selection(run_id, token)


class Decision(BaseModel):
    decision: str
    final_action: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None


def _open_for_decisions(run_id: str) -> None:
    run = services.get_run(run_id)
    if run["status"] == "superseded":
        raise HTTPException(409, f"this worklist was replaced by {run['superseded_by']} after the team changed; "
                                 "decide on the current worklist")


@app.post("/api/runs/{run_id}/accounts/{token}/decision")
def decide(run_id: str, token: str, d: Decision, request: Request):
    cfg = pipeline.run_config(run_id)
    approver_id = auth.actor(request)
    _open_for_decisions(run_id)
    with db.connect() as con:
        rec = con.execute("SELECT action FROM recommendations WHERE run_id = ? AND account_token = ?", (run_id, token)).fetchone()
        if con.execute("SELECT 1 FROM releases WHERE run_id = ? AND account_token = ?", (run_id, token)).fetchone():
            raise HTTPException(409, "already released to the bank; decisions are locked")
    if not rec:
        raise HTTPException(404, "no recommendation for this account in this run")
    cand = artifacts.load_frame(run_id, "candidates")
    allowed = set(cand[(cand["account_token"] == token) & cand["allowed"]]["action"])
    try:
        res = approvals.record(run_id, token, d.decision, approver_id, d.final_action, d.reason_code, d.reason_text,
                               allowed, cfg.institution.approval.reason_codes, rec["action"])
    except approvals.ApprovalError as e:
        raise HTTPException(422, str(e)) from e
    Tracer(run_id).event("human", "Collections manager", tool=f"approval.{d.decision}", module="lastmile.governance.approvals",
                         stage="approval", input={"account_token": token, "reason_code": d.reason_code},
                         output=res, message=f"{approver_id} {d.decision} {token}" + (f" ({d.reason_code})" if d.reason_code else ""))
    return res


@app.post("/api/runs/{run_id}/approve-bulk")
def bulk(run_id: str, request: Request):
    cfg = pipeline.run_config(run_id)
    approver_id = auth.actor(request)
    _open_for_decisions(run_id)
    items = [i for i in services.worklist(run_id) if i["decision"] == "pending" and not i["escalations"] and not i["released"]]
    for i in items:
        approvals.record(run_id, i["account_token"], "approved", approver_id, None, None, None, set(),
                         cfg.institution.approval.reason_codes, i["action"])
    Tracer(run_id).event("human", "Collections manager", tool="approval.bulk", module="lastmile.governance.approvals",
                         stage="approval", output={"approved": len(items)},
                         message=f"{approver_id} bulk-approved {len(items)} non-escalated recommendations")
    return {"approved": len(items), "skipped_escalated": sum(1 for i in services.worklist(run_id) if i["escalations"])}


@app.post("/api/runs/{run_id}/release")
def release_actions(run_id: str, request: Request):
    cfg = pipeline.run_config(run_id)
    released_by = auth.actor(request)
    run = services.get_run(run_id)
    if run["status"] == "superseded":
        raise HTTPException(409, f"this worklist was replaced by {run['superseded_by']}; release from the current one")
    if run["as_of_date"] and services.closure(run["as_of_date"]):
        raise HTTPException(409, f"business day {run['as_of_date']} is closed; its actions can no longer be released")
    items = [i for i in services.worklist(run_id) if i["decision"] in ("approved", "edited") and not i["released"]]
    if not items:
        raise HTTPException(422, "nothing approved and unreleased")
    with db.connect() as con:
        recs = {r["account_token"]: r for r in db.rows(con, "SELECT * FROM recommendations WHERE run_id = ?", (run_id,))}
    ident = pd.read_parquet(artifacts.identity_path(run_id))
    first = ident.set_index("account_token")["first_name"]
    payload = []
    for i in items:
        rec = recs[i["account_token"]]
        facts = db.loads(rec["facts_json"])
        facts["first_name"] = provenance.stamp("first_name", first.get(i["account_token"], "there"), "identity:members", run_id)
        if i["final_action"] != rec["action"]:
            from lastmile.agents.templates import SCRIPTS
            script_tpl = SCRIPTS[i["final_action"]]
        else:
            script_tpl = rec["script_tpl"]
        payload.append({"account_token": i["account_token"], "final_action": i["final_action"], "approver_id": i["approver"],
                        "script_text": provenance.render_text(script_tpl, facts)})
    channels = {a.id: a.channel for a in cfg.scenario.actions}
    try:
        res = release.release(run_id, payload, ident, services.bank(cfg), cfg.institution.source.release_endpoint, channels, released_by)
    except BankApiError as e:
        raise HTTPException(502, str(e)) from e
    Tracer(run_id).event("api", "Collections manager", tool="bank.release_actions", module="bank_api  POST /api/v1/collections/actions",
                         stage="approval", output={"released": res["released"]},
                         message=f"{res['released']} approved actions released to the bank")
    return res


# ----------------------------------------------------------------- manager: comparison
@app.get("/api/runs/{run_id}/comparison")
def comparison(run_id: str):
    dec = artifacts.load_json(run_id, "decision")
    cand = artifacts.load_frame(run_id, "candidates")
    port = artifacts.load_frame(run_id, "portfolio").set_index("account_token")
    seg = cand.drop_duplicates("account_token").set_index("account_token")["segment"]
    plans = {k: artifacts.load_frame(run_id, f"plan_{k}") for k in ("optimised", "sort_by_risk", "sort_by_value")}
    summary = {}
    for k, p in plans.items():
        summary[k] = {"selected": int(len(p)), "est_value": float(p["value"].sum()), "minutes": float(p["minutes"].sum()),
                      "actions": p["action"].value_counts().to_dict(),
                      "segments": p["account_token"].map(seg).value_counts().to_dict(),
                      "negative_value_actions": int((p["value"] < 0).sum()), "mc": dec["mc"][k],
                      "des": dec["des"].get(k)}
    opt, risk = plans["optimised"], plans["sort_by_risk"]

    def rows(df):
        d = df.copy()
        d["segment"] = d["account_token"].map(seg)
        d["risk_grade"] = d["account_token"].map(port["risk_grade"])
        d["exposure"] = d["account_token"].map(port["exposure"])
        d["dpd"] = d["account_token"].map(port["dpd"])
        return services.records(d[["account_token", "action", "value", "segment", "risk_grade", "exposure", "dpd"]])

    only_opt = opt[~opt["account_token"].isin(set(risk["account_token"]))].sort_values("value", ascending=False).head(25)
    only_risk = risk[~risk["account_token"].isin(set(opt["account_token"]))].sort_values("value").head(25)
    evaluation = None
    if (artifacts.run_dir(run_id) / "evaluation.json").exists():
        evaluation = artifacts.load_json(run_id, "evaluation")
    return {"summary": summary, "only_in_optimised": rows(only_opt), "only_in_risk_sort": rows(only_risk),
            "overlap": int(len(set(opt["account_token"]) & set(risk["account_token"]))), "evaluation": evaluation}


@app.post("/api/runs/{run_id}/evaluate")
def evaluate(run_id: str):
    """Synthetic benchmark only: a separate process reads the bank's sealed truth. The engine never does."""
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate_against_truth.py"), "--run-id", run_id],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise HTTPException(500, proc.stderr[-800:])
    return artifacts.load_json(run_id, "evaluation")


# --------------------------------------------------------------------- model & audit
@app.get("/api/runs/{run_id}/model")
def model(run_id: str):
    dec = artifacts.load_json(run_id, "decision")
    with db.connect() as con:
        recs = db.rows(con, "SELECT rationale_tpl, script_tpl, facts_json FROM recommendations WHERE run_id = ?", (run_id,))
    unstamped = sum(bool(provenance.DIGIT.search(provenance.PLACEHOLDER.sub("", r["rationale_tpl"] + r["script_tpl"]))) for r in recs)
    complete = sum(all(v.get("source") for v in db.loads(r["facts_json"]).values()) for r in recs)
    return {**dec, "governance": {"recommendations": len(recs), "unstamped_numbers": unstamped,
                                  "provenance_complete": complete,
                                  "overrides": approvals.override_signals(run_id)}}


@app.get("/api/runs/{run_id}/llm-calls")
def run_llm_calls(run_id: str):
    services.get_run(run_id)
    return llm_calls.for_run(run_id)


@app.get("/api/llm-calls/{call_id}")
def llm_call(call_id: int):
    call = llm_calls.get(call_id)
    if not call:
        raise HTTPException(404, "LLM call not found")
    return call


@app.get("/api/runs/{run_id}/audit")
def audit_events(run_id: str):
    ev = audit.events(run_id)
    for e in ev:
        e["payload"] = db.loads(e.pop("payload_json"))
    return {"events": ev, "chain": audit.verify_chain()}
