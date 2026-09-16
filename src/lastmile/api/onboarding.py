"""Onboarding a new bank from the Admin Console: start the agent, review its proposal, approve, and test it."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lastmile.api import services
from lastmile.api.auth import actor
from lastmile.config.resolve import resolve
from lastmile.config.settings import CONFIG_DIR
from lastmile.governance import audit
from lastmile.ingest import onboarding as ob
from lastmile.pipeline import run as pipeline
from lastmile.store import db, llm_calls
from lastmile.store import onboarding as sessions

router = APIRouter()


def _admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is not None and user.role != "admin":
        raise HTTPException(403, "only an admin can onboard a bank")


def _session(session_id: str) -> dict:
    s = sessions.get(session_id)
    if not s:
        raise HTTPException(404, "onboarding session not found")
    return s


class StartIn(BaseModel):
    base_url: str = Field(min_length=8, max_length=300, pattern=r"^https?://")


@router.post("/api/onboarding")
def start(body: StartIn, request: Request):
    _admin(request)
    from lastmile.agents.llm.factory import get_llm
    llm, mode = get_llm()
    if llm is None:
        raise HTTPException(409, f"onboarding needs an LLM - the mapping decisions are made by the model ({mode})")
    with db.connect() as con:
        busy = con.execute("SELECT session_id FROM onboarding_sessions WHERE status = 'running'").fetchone()
    if busy:
        raise HTTPException(409, f"onboarding {busy['session_id']} is still working")
    sid = pipeline.start_onboarding(body.base_url.rstrip("/"), actor(request))
    audit.append(sid, "onboarding.started", actor(request), {"base_url": body.base_url})
    return {"session_id": sid}


@router.get("/api/onboarding")
def list_sessions():
    return sessions.recent()


@router.get("/api/onboarding/{session_id}")
def detail(session_id: str):
    s = _session(session_id)
    canonical = {feed: {f: {"meaning": m, "kind": k, "required": req} for f, (m, k, req) in fields.items()}
                 for feed, fields in ob.CANONICAL.items()}
    return {**s, "canonical": canonical, "feed_roles": ob.FEED_ROLES, "reference_sections": ob.REFERENCE_SECTIONS}


@router.get("/api/onboarding/{session_id}/trace")
def trace(session_id: str, after: int = 0):
    _session(session_id)
    with db.connect() as con:
        ev = db.rows(con, "SELECT * FROM trace_events WHERE run_id = ? AND seq > ? ORDER BY seq", (session_id, after))
    for e in ev:
        e["input"], e["output"] = db.loads(e.pop("input_json")), db.loads(e.pop("output_json"))
    return ev


@router.get("/api/onboarding/{session_id}/llm-calls")
def calls(session_id: str):
    _session(session_id)
    return llm_calls.for_run(session_id)


class DecideIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/api/onboarding/{session_id}/approve")
def approve(session_id: str, request: Request, body: DecideIn | None = None):
    """Save the proposed Institution Pack and a Run Config for the new bank. Existing files are versioned first."""
    _admin(request)
    s = _session(session_id)
    if s["status"] != "proposed" or not s["pack_yaml"]:
        raise HTTPException(409, f"only a proposal that passed every check can be approved (this one is {s['status']})")
    inst_id = s["institution_id"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    header = (f"# Institution Pack for {s['institution_name']}.\n"
              f"# Proposed by the Onboarding Agent (session {session_id}), approved by {actor(request)} on {db.now()}.\n"
              f"# Data mapping and risk-score reading came from the bank's API. Business rules (LGD table, capacity,\n"
              f"# contact policy, eligibility, escalation, reason codes) were copied from the reference institution:\n"
              f"# confirm them with the bank before live use.\n")
    run_yaml = (f"# Run Config for {s['institution_name']} (onboarded {db.now()[:10]}).\n"
                f"run:\n  scenario: collections_delinquency\n  institution: {inst_id}\n  as_of: null\n")
    written = []
    for kind, text in (("institutions", header + s["pack_yaml"]), ("runs", run_yaml)):
        path = CONFIG_DIR / kind / f"{inst_id}.yaml"
        if path.exists():
            hist = CONFIG_DIR / ".history" / kind / inst_id
            hist.mkdir(parents=True, exist_ok=True)
            (hist / f"{stamp}.yaml").write_text(path.read_text())
            with (hist / "history.jsonl").open("a") as f:
                f.write(json.dumps({"version": stamp, "saved_at": db.now(), "changed_by": actor(request),
                                    "reason": f"replaced by onboarding {session_id}", "changes": []}) + "\n")
        path.write_text(text)
        written.append(f"config/{kind}/{inst_id}.yaml")
    try:
        resolve(inst_id)
    except Exception as e:  # never leave a configuration behind that a run cannot load
        for rel in written:
            (CONFIG_DIR.parent / rel).unlink(missing_ok=True)
        raise HTTPException(422, f"the saved configuration would not load, so nothing was kept: {e}") from e
    sessions.update(session_id, status="approved", decided_by=actor(request), decided_at=db.now(),
                    decision_note=(body.note if body else None))
    audit.append(session_id, "onboarding.approved", actor(request), {"institution_id": inst_id, "files": written,
                                                                     "note": body.note if body else None})
    return {"approved": True, "institution_id": inst_id, "files": written}


@router.post("/api/onboarding/{session_id}/reject")
def reject(session_id: str, request: Request, body: DecideIn | None = None):
    _admin(request)
    s = _session(session_id)
    if s["status"] in ("approved", "rejected", "running"):
        raise HTTPException(409, f"this session is {s['status']}")
    sessions.update(session_id, status="rejected", decided_by=actor(request), decided_at=db.now(),
                    decision_note=(body.note if body else None))
    audit.append(session_id, "onboarding.rejected", actor(request), {"note": body.note if body else None})
    return {"rejected": True}


@router.post("/api/onboarding/{session_id}/test-run")
def test_run(session_id: str, request: Request):
    """Run a full day for the new bank with its default team. The result opens in Runs & agents."""
    _admin(request)
    s = _session(session_id)
    if s["status"] != "approved":
        raise HTTPException(409, "approve the proposal first")
    busy = services.running_run()
    if busy:
        raise HTTPException(409, f"run {busy['run_id']} is still running")
    run_id = pipeline.start_background(run_name=s["institution_id"], default_roster=True)
    sessions.update(session_id, test_run_id=run_id)
    audit.append(session_id, "onboarding.test_run", actor(request), {"run_id": run_id, "institution_id": s["institution_id"]})
    return {"run_id": run_id}
