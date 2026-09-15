"""Supervisor: runs the stages in order, records stage outputs for the Admin Console, stops cleanly on failure.

Two entry points:
  execute  - a full run: pull the bank's data, model it, plan for today's team, explain, wait for approval.
  replan   - the team changed after a run: reuse that run's data and model, plan again for the new roster.
"""

from __future__ import annotations

import shutil
import traceback
from datetime import date

import pandas as pd

from lastmile.agents import crew
from lastmile.agents.base import RunContext
from lastmile.agents.tools import build_registry, capacities_from
from lastmile.config.resolve import resolve
from lastmile.config.settings import bank_base_url
from lastmile.engine import collectors
from lastmile.ingest.bank_client import BankClient
from lastmile.ingest.gates import GateAbort
from lastmile.store import artifacts, db

STAGES = [
    ("ingestion", "Data ingestion", ["Ingestion Agent"]),
    ("configuration", "Configuration", ["Configuration Agent"]),
    ("quality", "Data quality & privacy", ["Data Steward Agent"]),
    ("features", "Feature layer", ["Feature Agent"]),
    ("decision", "Decision engine", ["Decision Agent", "Policy Agent"]),
    ("governance", "Policy & governance", ["Policy Agent"]),
    ("explanation", "Explanation", ["Explanation Agent"]),
    ("approval", "Human approval", ["Supervisor Agent", "Collections manager"]),
]
REUSED_ON_REPLAN = ("ingestion", "quality", "features")
# Tables a re-plan inherits unchanged from its parent run. Plans are always produced again.
REUSED_ARTIFACTS = ("landing_scores", "landing_accounts", "landing_members", "landing_consents", "landing_queue_history",
                    "landing_outcomes", "quarantine", "canonical_accounts", "canonical_consents", "canonical_queue_history",
                    "portfolio", "training_set", "online_features", "eligibility", "candidates")


def _table(title: str, rows: list[dict] | pd.DataFrame, note: str | None = None) -> dict:
    if isinstance(rows, pd.DataFrame):
        rows = rows.to_dict("records")
    return {"title": title, "type": "table", "rows": rows, "note": note}


def _kv(title: str, data: dict, note: str | None = None) -> dict:
    return {"title": title, "type": "kv", "data": data, "note": note}


class Supervisor:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.registry = build_registry()
        self.agent = crew.SupervisorAgent(ctx, self.registry)
        self.agents = {c.name: c(ctx, self.registry) for c in crew.AGENT_CLASSES}

    def _set_run(self, run_id: str | None = None, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.connect(self.ctx.data_dir) as con:
            con.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", (*fields.values(), run_id or self.ctx.run_id))

    def _stage(self, stage: str, agent_name: str, task: str):
        self.ctx.tracer.stage_begin(stage)
        self.agent.handoff(agent_name, task)

    def _done(self, stage: str, metrics: dict, notes: list):
        self.ctx.tracer.stage_end(stage, "done", metrics, notes, self.ctx.artifacts.get(stage, []))

    def _team_notes(self) -> list[dict]:
        cfg = self.ctx.cfg
        team = cfg.team
        return [_table("Today's team (Manager Console roster)", [
            {"collector": c.name, "id": c.id, "working": c.present and c.shift_minutes > 0, "shift_minutes": c.shift_minutes,
             "plannable_minutes": round(cfg.plannable_for(c), 1) if c.present else 0} for c in team.collectors],
            f"Roster {team.source.replace('_', ' ')}" + (f" by {team.saved_by}" if team.saved_by else "")
            + f". {cfg.institution.capacity.planning_buffer:.0%} of each shift is kept spare because calls run long."),
            _kv("Capacity & contact policy", {"collectors_working": len(team.working), "total_minutes": team.total_minutes,
                                              "plannable_minutes": cfg.plannable_minutes,
                                              "sms_per_day_max": cfg.institution.capacity.sms_per_day_max,
                                              "hardship_slots_per_day": cfg.institution.capacity.hardship_slots_per_day,
                                              **cfg.institution.contact_policy.model_dump()})]

    def _supersede_earlier(self, as_of: str) -> None:
        """One live worklist per business day: older unreleased worklists for the same day are retired."""
        with db.connect(self.ctx.data_dir) as con:
            old = [r["run_id"] for r in con.execute(
                "SELECT run_id FROM runs WHERE as_of_date = ? AND status = 'awaiting_approval' AND run_id != ?",
                (as_of, self.ctx.run_id))]
        for rid in old:
            self._set_run(rid, status="superseded", superseded_by=self.ctx.run_id)
            self.registry.call(self.agent, "audit.append", event_type="run.superseded", actor=self.agent.name,
                               payload={"run_id": rid, "superseded_by": self.ctx.run_id, "business_date": as_of})

    # ------------------------------------------------------------------------------------ full run
    def execute(self) -> str:
        ctx, t = self.ctx, self.ctx.tracer
        t.stage_plan(STAGES)
        current = None
        try:
            ctx.cfg = resolve(ctx.run_name)
            self._set_run(scenario=ctx.cfg.scenario.scenario.id, institution=ctx.cfg.institution.institution.id,
                          config_hash=ctx.cfg.config_hash, llm_mode=ctx.llm_mode)
            src = ctx.cfg.institution.source
            ctx.bank = ctx.bank or BankClient(bank_base_url(src.base_url_env, src.default_base_url), src.page_size)
            self.agent.note(f"Run pinned to configuration {ctx.cfg.config_hash[:12]}; "
                            f"explanations will use {ctx.llm_mode}", files=ctx.cfg.files)
            with db.connect(ctx.data_dir) as con:
                prev = con.execute("SELECT summary_json FROM runs WHERE status IN ('awaiting_approval','released','superseded') "
                                   "AND run_id != ? ORDER BY started_at DESC LIMIT 1", (ctx.run_id,)).fetchone()
            if prev and prev["summary_json"]:
                ctx.state["previous_score_rows"] = db.loads(prev["summary_json"]).get("scores_rows")

            # 1 ---------------------------------------------------------------- ingestion
            current = "ingestion"
            self._stage(current, "Ingestion Agent", "Pull today's feeds from the bank API and land them untouched")
            raw = self.agents["Ingestion Agent"].run(src.feeds)
            self._set_run(as_of_date=ctx.as_of.isoformat())
            self._done(current, {
                "Bank as-of": ctx.as_of.isoformat(), "Feeds": len(raw), "Rows pulled": sum(r.rows for r in raw.values()),
                "API pages": sum(r.pages for r in raw.values()), "API time (ms)": sum(r.ms for r in raw.values())},
                [_table("Feeds pulled", [{"feed": f, "endpoint": r.path, "rows": r.rows, "pages": r.pages, "ms": r.ms,
                                          "bank columns": len(r.frame.columns)} for f, r in raw.items()]),
                 _table("HTTP calls", [c | {"feed": f} for f, r in raw.items() for c in r.calls])])

            # 2 ------------------------------------------------------------ configuration
            current = "configuration"
            self._stage(current, "Configuration Agent", "Validate Layer 0, read today's roster, map bank columns")
            canon = self.agents["Configuration Agent"].run(raw)
            s, i = ctx.cfg.scenario, ctx.cfg.institution
            artifacts.save_json(ctx.run_id, "resolved_config", ctx.cfg.model_dump(mode="json"), ctx.data_dir)
            self._set_run(roster_json=db.dumps(ctx.cfg.team.model_dump()))
            self._done(current, {
                "Config hash": ctx.cfg.config_hash[:16], "Scenario": s.scenario.title, "Institution": i.institution.name,
                "Collectors working": len(ctx.cfg.team.working), "Plannable minutes": round(ctx.cfg.plannable_minutes),
                "Actions": len(s.actions), "Features": len(s.causal.features), "Eligibility rules":
                    sum(len(v.all) + len(v.any) for v in i.eligibility.values())},
                [_kv("Packs", ctx.cfg.files),
                 *self._team_notes(),
                 _table("Actions (Scenario Pack)", [a.model_dump() for a in s.actions]),
                 _kv("Objective", s.objective.model_dump()),
                 _table("Eligibility rules", [{"rule_set": k, "rule": r.describe()} for k, v in i.eligibility.items()
                                              for r in [*v.all, *v.any]]),
                 _table("Field mapping", [{"feed": f, "canonical": c, "bank column": b} for f, m in i.field_map.items()
                                          for c, b in m.items()]),
                 _kv("Leakage guard (Scenario Pack)", {"features": s.causal.features, "exclude_features": s.causal.exclude_features})])

            # 3 --------------------------------------------------------------- quality
            current = "quality"
            self._stage(current, "Data Steward Agent", "Gate the data and remove identities before any modelling")
            tok, gates, quarantine = self.agents["Data Steward Agent"].run(canon)
            q_meta = artifacts.save_frame(ctx.run_id, "quarantine", quarantine, ctx.data_dir) | {
                "stage": current, "description": "Rows removed by quality gates, with reason",
                "mask_columns": ctx.cfg.institution.pii_fields}
            ctx.artifacts.setdefault(current, []).append(q_meta)
            for feed in ("accounts", "consents", "queue_history"):
                meta = artifacts.save_frame(ctx.run_id, f"canonical_{feed}", tok[feed], ctx.data_dir) | {
                    "stage": current, "description": f"Canonical, tokenised '{feed}' - no PII", "mask_columns": []}
                ctx.artifacts[current].append(meta)
            self._done(current, {"Gates passed": f"{sum(g.passed for g in gates)}/{len(gates)}",
                                 "Quarantined rows": len(quarantine), "Identities vaulted": int(len(tok["accounts"])),
                                 "PII columns removed": len(ctx.cfg.institution.pii_fields)},
                       [_table("Quality gates", [g.dict() for g in gates])])

            # 4 -------------------------------------------------------------- features
            current = "features"
            self._stage(current, "Feature Agent", "Build point-in-time training data and today's feature matrix")
            feats = self.agents["Feature Agent"].run(tok)
            port, train, X = feats["portfolio"], feats["train"], feats["X"]
            for name, df, desc in [("portfolio", port, "Today's delinquent, scored accounts with PD and LGD"),
                                   ("training_set", train, "One row per matured past decision: X, T, Y"),
                                   ("online_features", X, "Today's feature matrix, same definitions as training")]:
                ctx.artifacts.setdefault(current, []).append(
                    artifacts.save_frame(ctx.run_id, name, df, ctx.data_dir) | {"stage": current, "description": desc, "mask_columns": []})
            self._done(current, {"Accounts today": len(port), "Training decisions": len(train),
                                 "Features": len(ctx.cfg.scenario.causal.features),
                                 "Feasible": "yes" if feats["feasibility"]["feasible"] else "no",
                                 "Control rows": int((train["T"] == ctx.cfg.scenario.causal.control_value).sum())},
                       [_table("Causal feasibility", feats["feasibility"]["checks"]),
                        _kv("Treatment arms in training data", feats["feasibility"]["arms"]),
                        _table("PD calibration by grade", port.groupby("risk_grade").agg(
                            accounts=("account_token", "size"), pd_12m=("pd_12m", "first"), pd_30d=("pd_h", "first")).reset_index().round(4))])

            # 5 -------------------------------------------------------------- decision
            current = "decision"
            self._stage(current, "Decision Agent", "Estimate effects, value them, plan for today's team, simulate")
            dec = self.agents["Decision Agent"].run(feats, self.agents["Policy Agent"])
            dec["cand"] = dec["cand"].merge(dec["segments"], on="account_token", how="left")
            for name, df, desc in [("eligibility", dec["elig"], "Allowed / blocked for every account x action, with reasons"),
                                   ("candidates", dec["cand"], "Uplift, interval, value and eligibility per account x action")]:
                ctx.artifacts.setdefault(current, []).append(
                    artifacts.save_frame(ctx.run_id, name, df, ctx.data_dir) | {"stage": current, "description": desc, "mask_columns": []})
            self._set_run(model_run_id=dec["model_run_id"])
            self._plan_and_publish(dec, feats["feasibility"], int(len(raw["scores"].frame)))
        except Exception as e:
            self._fail(current, e)
        return ctx.run_id

    # ------------------------------------------------------------------------------ re-plan
    def replan(self, parent_run_id: str, roster) -> str:
        """Plan again for a changed team, reusing the parent run's data, features, model and scores."""
        ctx, t = self.ctx, self.ctx.tracer
        t.stage_plan(STAGES)
        current = None
        try:
            with db.connect(ctx.data_dir) as con:
                parent = dict(con.execute("SELECT * FROM runs WHERE run_id = ?", (parent_run_id,)).fetchone())
            ctx.cfg = resolve(ctx.run_name)
            if ctx.cfg.config_hash != parent["config_hash"]:
                raise RuntimeError("the configuration packs changed after the original run; its scores no longer match "
                                   "the rules - start a full run instead of re-planning")
            ctx.as_of = date.fromisoformat(parent["as_of_date"])
            self._set_run(scenario=parent["scenario"], institution=parent["institution"], config_hash=parent["config_hash"],
                          llm_mode=ctx.llm_mode, as_of_date=parent["as_of_date"], model_run_id=parent["model_run_id"],
                          parent_run_id=parent_run_id)
            self.agent.note(f"Re-planning {parent_run_id} for a changed team: bank data, features, model and scores are "
                            "reused; only the plan, checks and explanations are produced again", parent=parent_run_id)
            self._copy_from_parent(parent_run_id)

            # 2 --------------------------------------------------- configuration (roster only)
            current = "configuration"
            self._stage(current, "Configuration Agent", "Read the manager's updated roster")
            self.agents["Configuration Agent"].load_roster(roster)
            artifacts.save_json(ctx.run_id, "resolved_config", ctx.cfg.model_dump(mode="json"), ctx.data_dir)
            self._set_run(roster_json=db.dumps(ctx.cfg.team.model_dump()))
            self._done(current, {"Config hash": ctx.cfg.config_hash[:16], "Collectors working": len(ctx.cfg.team.working),
                                 "Plannable minutes": round(ctx.cfg.plannable_minutes), "Re-plan of": parent_run_id},
                       [_kv("Packs (unchanged since the original run)", ctx.cfg.files), *self._team_notes()])

            # 5 ------------------------------------------------------------------- decision
            current = "decision"
            self._stage(current, "Decision Agent", "Plan again for the new team, using the scores already produced")
            cand = artifacts.load_frame(ctx.run_id, "candidates", ctx.data_dir)
            # expected loss is computed in the decision stage, after the portfolio table was saved
            port = artifacts.load_frame(ctx.run_id, "portfolio", ctx.data_dir).merge(
                cand.drop_duplicates("account_token")[["account_token", "expected_loss"]], on="account_token", how="left")
            elig = artifacts.load_frame(ctx.run_id, "eligibility", ctx.data_dir)
            old = artifacts.load_json(parent_run_id, "decision", ctx.data_dir)
            segs = cand.drop_duplicates("account_token")[["account_token", "segment"]].reset_index(drop=True)
            dec = {"portfolio": port, "cand": cand, "elig": elig, "segments": segs, "validation": old["validation"],
                   "model_run_id": parent["model_run_id"],
                   **self.agents["Decision Agent"].plan(port, cand.drop(columns=["segment"]))}
            scores_rows = (db.loads(parent["summary_json"]) or {}).get("scores_rows")
            self._plan_and_publish(dec, old["feasibility"], scores_rows, parent_run_id=parent_run_id)
        except Exception as e:
            self._fail(current, e)
        return ctx.run_id

    def _copy_from_parent(self, parent_run_id: str) -> None:
        ctx = self.ctx
        src, dst = artifacts.run_dir(parent_run_id, ctx.data_dir), artifacts.run_dir(ctx.run_id, ctx.data_dir)
        for name in REUSED_ARTIFACTS:
            if (src / f"{name}.parquet").exists():
                shutil.copy2(src / f"{name}.parquet", dst / f"{name}.parquet")
        shutil.copy2(artifacts.identity_path(parent_run_id, ctx.data_dir), artifacts.identity_path(ctx.run_id, ctx.data_dir))
        with db.connect(ctx.data_dir) as con:
            stages = {r["stage"]: dict(r) for r in con.execute("SELECT * FROM stages WHERE run_id = ?", (parent_run_id,))}
            for sid in REUSED_ON_REPLAN:
                s = stages[sid]
                notes = [_kv("Reused", {"from_run": parent_run_id,
                                        "why": "the team changed, not the bank's data - nothing here depends on the roster"}),
                         *(db.loads(s["notes_json"]) or [])]
                con.execute("UPDATE stages SET status='done', started_at=?, finished_at=?, ms=0, metrics_json=?, notes_json=?,"
                            " artifacts_json=? WHERE run_id=? AND stage=?",
                            (s["started_at"], s["finished_at"], s["metrics_json"], db.dumps(notes), s["artifacts_json"],
                             ctx.run_id, sid))
            # the reused stages' trace is copied so the Admin Console still shows who did what, and when
            con.execute("INSERT INTO trace_events (run_id, stage, ts, kind, agent, target, tool, module, status, ms, input_json,"
                        " output_json, message) SELECT ?, stage, ts, kind, agent, target, tool, module, status, ms, input_json,"
                        " output_json, message FROM trace_events WHERE run_id = ? AND stage IN (?,?,?) ORDER BY seq",
                        (ctx.run_id, parent_run_id, *REUSED_ON_REPLAN))
        dec_art = next((db.loads(s["artifacts_json"]) for sid, s in stages.items() if sid == "decision"), []) or []
        ctx.artifacts["decision"] = [a for a in dec_art if a["name"] in ("eligibility", "candidates")]

    # --------------------------------------------------------- shared tail: plan, govern, explain, publish
    def _plan_and_publish(self, dec: dict, feasibility: dict, scores_rows: int | None, parent_run_id: str | None = None):
        ctx = self.ctx
        current = "decision"
        res, plans, mc = dec["solver"], dec["plans"], dec["mc"]
        cand = dec["cand"]
        for name, df, desc in [("plan_optimised", plans["optimised"], "CBC assignment, split into collector queues"),
                               ("plan_sort_by_risk", plans["sort_by_risk"], "Baseline: highest risk first"),
                               ("plan_sort_by_value", plans["sort_by_value"], "Baseline: highest expected loss first")]:
            ctx.artifacts.setdefault(current, []).append(
                artifacts.save_frame(ctx.run_id, name, df, ctx.data_dir) | {"stage": current, "description": desc, "mask_columns": []})
        names = {c.id: c.name for c in ctx.cfg.team.collectors}
        queues = collectors.queue_summary(plans["optimised"], capacities_from(ctx.cfg), names)
        for q in queues:
            q["completion_simulated"] = dec["des"]["optimised"].get("per_collector", {}).get(q["collector_id"])
        artifacts.save_json(ctx.run_id, "decision", {"mc": mc, "des": dec["des"], "validation": dec["validation"],
                                                     "feasibility": feasibility, "model_run_id": dec["model_run_id"],
                                                     "solver": {k: v for k, v in res.__dict__.items() if k != "plan"},
                                                     "segments": dec["segments"]["segment"].value_counts().to_dict(),
                                                     "queues": queues, "roster": ctx.cfg.team.model_dump(),
                                                     "parent_run_id": parent_run_id},
                            ctx.data_dir)
        risk_v = float(plans["sort_by_risk"]["value"].sum())
        self._done(current, {"Selected": len(res.plan), "Est. value (optimised)": round(res.objective),
                             "Est. value (risk sort)": round(risk_v), "Collector queues": len(capacities_from(ctx.cfg)),
                             "Solver": f"CBC {res.status}", "Solve (ms)": res.solve_ms, "Model": dec["model_run_id"]},
                   [_table("Collector queues", [{"collector": q["name"], "tasks": q["tasks"], "minutes": q["minutes"],
                                                 "plannable": q["plannable_minutes"], "est_value": round(q["est_value"]),
                                                 "fits_shift_simulated": q["completion_simulated"]} for q in queues],
                           "Each queue is packed to fit that collector's shift; SimPy shows the share of tasks finished in time."),
                    _kv("Holdout validation (Qini)", {a: v["qini"] for a, v in dec["validation"]["per_action"].items()}),
                    _kv("Solver", {"status": res.status, "variables": res.variables, "constraints": res.constraints,
                                   "minutes_used": res.minutes_used, "sms_used": res.sms_used,
                                   "hardship_used": res.hardship_used, "solve_ms": res.solve_ms}),
                    _table("Policies compared (estimates)", [
                        {"policy": k, "selected": len(plans[k]), "est_value": round(float(plans[k]["value"].sum())),
                         "mc_p10": round(mc[k]["value_p10"]), "mc_p50": round(mc[k]["value_p50"]),
                         "mc_p90": round(mc[k]["value_p90"]), "p_over_shift": round(mc[k]["prob_over_capacity"], 3)}
                        for k in plans]),
                    _kv("Floor simulation (SimPy, optimised)", {k: v for k, v in dec["des"]["optimised"].items() if k != "per_collector"}),
                    _kv("Segments", dec["segments"]["segment"].value_counts().to_dict())])

        # 6 ------------------------------------------------------------ governance
        current = "governance"
        self._stage(current, "Policy Agent", "Independently verify every plan and flag what needs a senior reviewer")
        flags = self.agents["Policy Agent"].govern(plans, dec["elig"], cand, {
            "model_run_id": dec["model_run_id"], "config_hash": ctx.cfg.config_hash, "solver_status": res.status,
            "objective": res.objective, "selected": len(res.plan), "collectors_working": len(ctx.cfg.team.working),
            "plannable_minutes": ctx.cfg.plannable_minutes, "parent_run_id": parent_run_id})
        esc = [{"account_token": plans["optimised"].at[i, "account_token"], "action": plans["optimised"].at[i, "action"],
                "codes": ", ".join(f["code"] for f in fl)} for i, fl in flags.items() if fl]
        self._done(current, {"Violations (optimised)": 0, "Escalated": len(esc), "Audit chain": "appended"},
                   [_table("Escalations", esc[:200], "Escalated accounts are excluded from bulk approval")])

        # 7 ----------------------------------------------------------- explanation
        current = "explanation"
        self._stage(current, "Explanation Agent", "Explain each recommendation using stamped facts only")
        facts, tpl = self.agents["Explanation Agent"].run(dec)
        self._done(current, {"LLM": ctx.llm_mode, "Templates": len(tpl),
                             "LLM drafts accepted": sum(1 for v in tpl.values() if v["source"] == "llm"),
                             "Fact sheets": len(facts)},
                   [_table("Templates", [{"segment": s_, "action": a_, "source": v["source"], "rationale": v["rationale"],
                                          "script": v["script"]} for (s_, a_), v in tpl.items()])])

        # 8 ---------------------------------------------------------- publish & wait
        current = "approval"
        ctx.tracer.stage_begin(current)
        published = self.registry.call(self.agent, "worklist.publish", input_summary={"rows": len(res.plan)},
                                       plan=plans["optimised"], facts=facts, flags=flags, tpl=tpl, cand=cand)
        self.registry.call(self.agent, "audit.append", event_type="worklist.published", actor=self.agent.name,
                           payload={"recommendations": published, "llm_mode": ctx.llm_mode, "parent_run_id": parent_run_id,
                                    "roster": {"date": ctx.cfg.team.roster_date, "source": ctx.cfg.team.source,
                                               "working": [c.id for c in ctx.cfg.team.working],
                                               "total_minutes": ctx.cfg.team.total_minutes}})
        self.agent.handoff("Collections manager", f"{published} recommendations in {len(capacities_from(ctx.cfg))} "
                                                  "collector queue(s) await approval in the Manager Console")
        ctx.tracer.stage_end(current, "waiting", {"Awaiting approval": published}, [], [])
        self._set_run(status="awaiting_approval", finished_at=db.now(),
                      summary_json=db.dumps({"scores_rows": scores_rows, "selected": published, "est_value": res.objective}))
        self._supersede_earlier(ctx.as_of.isoformat())

    def _fail(self, current: str | None, e: Exception) -> None:
        ctx, t = self.ctx, self.ctx.tracer
        aborted = isinstance(e, GateAbort)
        notes = [_table("Quality gates", [g.dict() for g in e.results])] if aborted else [
            _kv("Error", {"type": type(e).__name__, "message": str(e)})]
        if current:
            t.stage_end(current, "failed", {"Error": type(e).__name__}, notes, ctx.artifacts.get(current, []))
        self.agent.note(f"Run stopped at '{current}': {e}", traceback=traceback.format_exc()[-1500:])
        self._set_run(status="aborted" if aborted else "failed", error=str(e)[:2000], finished_at=db.now())
