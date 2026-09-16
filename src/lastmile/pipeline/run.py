"""Entry points: start a run (background or blocking) and interactive, traced agent actions."""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime

from lastmile.agents import crew
from lastmile.agents.base import RunContext
from lastmile.agents.llm.factory import get_llm
from lastmile.agents.supervisor import Supervisor
from lastmile.agents.tools import build_registry
from lastmile.agents.trace import Tracer
from lastmile.config.resolve import resolve
from lastmile.config.schema import ResolvedConfig, Roster
from lastmile.ingest.bank_client import BankClient
from lastmile.store import artifacts, db


def new_run(run_name: str = "default", data_dir=None) -> str:
    db.init(data_dir)
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(2)}"
    with db.connect(data_dir) as con:
        con.execute("INSERT INTO runs (run_id, status, started_at) VALUES (?, 'running', ?)", (run_id, db.now()))
    return run_id


def execute(run_id: str, run_name: str = "default", data_dir=None, bank: BankClient | None = None,
            llm=None, llm_mode: str | None = None) -> str:
    if llm is None and llm_mode is None:
        llm, llm_mode = get_llm()
    ctx = RunContext(run_id=run_id, run_name=run_name, tracer=Tracer(run_id, data_dir), bank=bank, llm=llm,
                     llm_mode=llm_mode or "template", data_dir=data_dir)
    return Supervisor(ctx).execute()


def run_blocking(run_name: str = "default", data_dir=None, bank: BankClient | None = None, llm=None,
                 llm_mode: str | None = None) -> str:
    return execute(new_run(run_name, data_dir), run_name, data_dir, bank, llm, llm_mode)


def start_background(run_name: str = "default", data_dir=None, default_roster: bool = False) -> str:
    """default_roster: plan with the institution pack's default team (a newly onboarded bank has no saved roster)."""
    run_id = new_run(run_name, data_dir)

    def work():
        llm, mode = get_llm()
        ctx = RunContext(run_id=run_id, run_name=run_name, tracer=Tracer(run_id, data_dir), llm=llm, llm_mode=mode,
                         data_dir=data_dir)
        ctx.state["default_roster"] = default_roster
        Supervisor(ctx).execute()

    threading.Thread(target=work, daemon=True, name=f"run-{run_id}").start()
    return run_id


def run_config(run_id: str, data_dir=None) -> ResolvedConfig:
    """The exact configuration a run was produced with, including its team - not whatever is on disk today."""
    try:
        cfg = ResolvedConfig.model_validate(artifacts.load_json(run_id, "resolved_config", data_dir))
    except FileNotFoundError:
        cfg = resolve()
    cfg.roster = run_roster(run_id, data_dir) or cfg.roster
    return cfg


def replan(parent_run_id: str, roster: Roster, run_name: str = "default", data_dir=None, llm=None,
           llm_mode: str | None = None) -> str:
    """Same day, same data and model, new team: a new run that plans again and supersedes its parent."""
    run_id = new_run(run_name, data_dir)
    if llm is None and llm_mode is None:
        llm, llm_mode = get_llm()
    ctx = RunContext(run_id=run_id, run_name=run_name, tracer=Tracer(run_id, data_dir), llm=llm,
                     llm_mode=llm_mode or "template", data_dir=data_dir)
    return Supervisor(ctx).replan(parent_run_id, roster)


def start_replan_background(parent_run_id: str, roster: Roster, run_name: str = "default", data_dir=None) -> str:
    run_id = new_run(run_name, data_dir)

    def work():
        llm, mode = get_llm()
        ctx = RunContext(run_id=run_id, run_name=run_name, tracer=Tracer(run_id, data_dir), llm=llm, llm_mode=mode,
                         data_dir=data_dir)
        Supervisor(ctx).replan(parent_run_id, roster)

    threading.Thread(target=work, daemon=True, name=f"replan-{run_id}").start()
    return run_id


def run_roster(run_id: str, data_dir=None) -> Roster | None:
    with db.connect(data_dir) as con:
        row = con.execute("SELECT roster_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return Roster(**db.loads(row["roster_json"])) if row and row["roster_json"] else None


def explain_selection(run_id: str, account_token: str, data_dir=None) -> dict:
    """Manager asked 'why this account?' - the Decision Agent answers with a solver counterfactual, traced."""
    cfg = run_config(run_id, data_dir)  # the counterfactual is solved with that run's rules and team
    ctx = RunContext(run_id=run_id, run_name="default", tracer=Tracer(run_id, data_dir), cfg=cfg, data_dir=data_dir)
    ctx.tracer.stage = "interactive"
    reg = build_registry()
    agent = crew.DecisionAgent(ctx, reg)
    ctx.tracer.event("handoff", "Collections manager", target=agent.name, message=f"Why was {account_token} (not) selected?")
    return agent.use("engine.explain_selection", input_summary={"account_token": account_token},
                     cand=artifacts.load_frame(run_id, "candidates", data_dir),
                     base_plan=artifacts.load_frame(run_id, "plan_optimised", data_dir), account_token=account_token)


# ------------------------------------------------------------------------------------------- onboarding
def onboard(session_id: str, base_url: str, data_dir=None, bank: BankClient | None = None, llm=None,
            llm_mode: str | None = None) -> str:
    """Let the Onboarding Agent work out a new bank; store what it proposes for an admin to review."""
    import yaml

    from lastmile.agents.onboarding import OnboardingAgent
    from lastmile.agents.tools import build_registry
    from lastmile.store import onboarding as sessions

    if llm is None and llm_mode is None:
        llm, llm_mode = get_llm()
    ctx = RunContext(run_id=session_id, run_name="onboarding", tracer=Tracer(session_id, data_dir), llm=llm,
                     llm_mode=llm_mode or "template", data_dir=data_dir,
                     bank=bank or BankClient(base_url.rstrip("/"), 5000, timeout=30))
    ctx.tracer.stage = "onboarding"
    agent = OnboardingAgent(ctx, build_registry())
    ctx.tracer.event("handoff", "Admin", target=agent.name, message=f"Connect the bank at {base_url}")
    try:
        out = agent.run(base_url)
        pack = out["pack"]
        sessions.update(session_id, data_dir, status="proposed" if not out["problems"] else "needs_review",
                        institution_name=out["institution_name"],
                        institution_id=pack["institution"]["id"] if pack else None, plan=out["plan"],
                        proposal=out["proposal"], profiles=out["profiles"], problems=out["problems"],
                        attempts=out["attempts"], finished_at=db.now(),
                        pack_yaml=yaml.safe_dump(pack, sort_keys=False, allow_unicode=True) if pack else None)
    except Exception as e:
        agent.note(f"Onboarding stopped: {e}")
        sessions.update(session_id, data_dir, status="failed", error=str(e)[:2000], finished_at=db.now())
    return session_id


def start_onboarding(base_url: str, created_by: str, data_dir=None) -> str:
    from lastmile.store import onboarding as sessions

    session_id = sessions.new_session(base_url, created_by, data_dir)
    threading.Thread(target=onboard, args=(session_id, base_url, data_dir), daemon=True, name=f"onboard-{session_id}").start()
    return session_id
