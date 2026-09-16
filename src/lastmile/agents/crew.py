"""The agents. Each owns one module, follows a fixed plan, and calls only the tools it is accountable for.

Sequencing is deterministic so a run is reproducible and auditable. The LLM is used in exactly one place -
the Explanation Agent's template drafting - and never produces a number.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd

from lastmile.agents.base import Agent
from lastmile.agents.tools import limits_from


class IngestionAgent(Agent):
    name = "Ingestion Agent"
    title = "Pulls today's feeds from the bank"
    responsibility = "Connect to the bank API, discover feeds, pull every page, store raw data untouched."
    modules = ("lastmile.ingest.bank_client", "lastmile.store.artifacts")

    def run(self, feeds: dict[str, str]) -> dict:
        health = self.use("bank.health")
        self.ctx.as_of = date.fromisoformat(self.ctx.cfg.run.run.as_of or health["as_of"])
        self.note(f"Decision date set to {self.ctx.as_of} "
                  f"({'run config override' if self.ctx.cfg.run.run.as_of else 'bank reported as-of'})")
        catalogue = self.use("bank.list_feeds")
        published = {f["path"] for f in catalogue["feeds"]}
        missing = [p for p in feeds.values() if p not in published]
        if missing:
            raise RuntimeError(f"bank does not publish required feeds: {missing}")
        raw, pii_src = {}, {}
        fm, pii = self.ctx.cfg.institution.field_map, set(self.ctx.cfg.institution.pii_fields)
        for feed, path in feeds.items():
            res = self.use("bank.fetch_feed", input_summary={"feed": feed, "path": path}, feed=feed, path=path)
            pii_src[feed] = [fm[feed][k] for k in fm[feed] if k in pii]
            self.use("landing.store", input_summary={"feed": feed}, feed=feed, frame=res.frame,
                     pii_source_cols=pii_src[feed])
            raw[feed] = res
        return raw


class ConfigurationAgent(Agent):
    name = "Configuration Agent"
    title = "Reads and enforces Layer 0"
    responsibility = ("Validate the three config packs, fix the config hash, read today's roster, "
                      "map bank columns to canonical names.")
    modules = ("lastmile.config.resolve", "lastmile.config.schema", "lastmile.store.rosters", "lastmile.ingest.mapping")

    def run(self, raw: dict, roster_override=None) -> dict[str, pd.DataFrame]:
        cfg = self.use("config.load_packs", input_summary={"run": self.ctx.run_name}, run_name=self.ctx.run_name)
        if cfg.config_hash != self.ctx.cfg.config_hash:
            raise RuntimeError("config changed on disk during the run; refusing to continue with mixed configuration")
        self.note("Configuration hash matches the one pinned at run start", config_hash=cfg.config_hash)
        self.load_roster(roster_override)
        canon = {}
        for feed, res in raw.items():
            canon[feed] = self.use("config.map_fields", input_summary={"feed": feed, "rows": res.rows}, feed=feed, raw=res.frame)
        return canon

    def load_roster(self, override=None):
        day = self.ctx.as_of.isoformat()
        if override is None and self.ctx.state.get("default_roster"):
            from lastmile.config.schema import Roster
            override = Roster.default(self.ctx.cfg.institution.capacity, day)
        team = self.use("roster.load", input_summary={"roster_date": day, "override": override is not None},
                        roster_date=day, override=override)
        if not team.working:
            self.note("No collector is working today: only actions that need no staff time can be planned")
        elif team.source != "saved":
            self.note(f"No roster saved for {day}; using the {team.source.replace('_', ' ')} team "
                      f"({len(team.working)} collectors). The manager can change it and re-plan.")
        else:
            self.note(f"Using the manager's roster for {day}: {len(team.working)} collectors, {team.total_minutes:,} minutes")
        return team


class DataStewardAgent(Agent):
    name = "Data Steward Agent"
    title = "Guards data quality and privacy"
    responsibility = "Run quality gates, quarantine bad rows, abort on blocking failures, tokenise identities."
    modules = ("lastmile.ingest.gates", "lastmile.ingest.tokenise")

    def run(self, canon: dict) -> tuple[dict, list, pd.DataFrame]:
        clean, results, quarantine = self.use("quality.run_gates", input_summary={f: len(d) for f, d in canon.items()},
                                              frames=canon)
        self.note("All blocking gates passed; proceeding to tokenisation",
                  warnings=[g.gate for g in results if not g.passed])
        tok = self.use("privacy.tokenise", input_summary={"pii_fields": self.ctx.cfg.institution.pii_fields}, frames=clean)
        return tok, results, quarantine


class FeatureAgent(Agent):
    name = "Feature Agent"
    title = "Builds model-level data"
    responsibility = "Portfolio join, PD calibration, LGD, point-in-time training set, online features, leakage and feasibility checks."
    modules = ("lastmile.features.builder", "lastmile.features.calibration", "lastmile.features.lgd",
               "lastmile.features.leakage", "lastmile.features.profile")

    def run(self, tok: dict) -> dict:
        port = self.use("features.join_portfolio", canon=tok)
        port = self.use("features.calibrate_pd", input_summary={"rows": len(port)}, portfolio=port)
        port = self.use("features.lookup_lgd", input_summary={"rows": len(port)}, portfolio=port)
        train = self.use("features.build_training_set", canon=tok)
        X = self.use("features.build_online_features", input_summary={"rows": len(port)}, portfolio=port, canon=tok)
        self.use("features.leakage_guard", input_summary={"frame": "online_features"}, frame=X)
        self.use("features.leakage_guard", input_summary={"frame": "training_set"}, frame=train)
        feas = self.use("features.feasibility_profile", input_summary={"rows": len(train)}, train=train)
        if not feas["feasible"]:
            self.note("Feasibility checks failed - uplift estimates would not be trustworthy", checks=feas["checks"])
            raise RuntimeError("causal feasibility checks failed; worklist not produced")
        self.note("Data can support causal estimation; handing matrices to the Decision Agent")
        return {"portfolio": port, "train": train, "X": X, "feasibility": feas}


class PolicyAgent(Agent):
    name = "Policy Agent"
    title = "Applies policy and governance"
    responsibility = "Eligibility before solving, independent verification after, escalations, audit trail."
    modules = ("lastmile.governance.policy", "lastmile.governance.escalation", "lastmile.governance.audit")

    def eligibility(self, portfolio: pd.DataFrame) -> pd.DataFrame:
        return self.use("policy.eligibility", input_summary={"accounts": len(portfolio)}, portfolio=portfolio)

    def govern(self, plans: dict[str, pd.DataFrame], elig: pd.DataFrame, cand: pd.DataFrame, summary: dict) -> pd.Series:
        violations = {}
        for label, p in plans.items():
            violations[label] = self.use("policy.verify_plan", input_summary={"plan": label, "rows": len(p)},
                                         plan=p, elig=elig, label=label)
        if violations["optimised"]:
            self.use("audit.append", event_type="plan.rejected", actor=self.name, payload={"violations": violations["optimised"]})
            raise RuntimeError(f"optimised plan failed verification: {violations['optimised'][:3]}")
        self.note("Optimised plan independently verified: zero violations")
        plan = plans["optimised"]  # the solver's rows already carry exposure and the uplift interval
        flags = self.use("governance.escalate", input_summary={"rows": len(plan)}, plan=plan)
        self.use("audit.append", event_type="plan.verified", actor=self.name,
                 payload={**summary, "violations": {k: len(v) for k, v in violations.items()},
                          "escalated": int((flags.map(len) > 0).sum())})
        return flags


class DecisionAgent(Agent):
    name = "Decision Agent"
    title = "Runs the maths through models and solvers"
    responsibility = "Validate and train the uplift model, score actions, value them, segment, optimise, compare, simulate."
    modules = ("lastmile.engine.uplift", "lastmile.engine.value", "lastmile.engine.segments", "lastmile.engine.optimise",
               "lastmile.engine.baseline", "lastmile.engine.simulate_mc", "lastmile.engine.simulate_des", "lastmile.kpi.metrics")

    def run(self, feats: dict, policy_agent: PolicyAgent) -> dict:
        port, train, X = feats["portfolio"], feats["train"], feats["X"]
        validation = self.use("kpi.validate_uplift", input_summary={"rows": len(train)}, train=train)
        if not validation["passed"]:
            self.note("Holdout Qini below threshold - refusing to publish a worklist from this model")
            raise RuntimeError("uplift model failed holdout validation")
        model, model_run_id = self.use("engine.train_uplift", input_summary={"rows": len(train)}, train=train)
        scored = self.use("engine.score_actions", input_summary={"accounts": len(X)}, model=model, X=X, portfolio=port)

        self.handoff(policy_agent, "Which actions is each account allowed to receive today?")
        elig = policy_agent.eligibility(port)
        policy_agent.handoff(self, "Eligibility matrix ready; ineligible pairs will not become decision variables")

        port, cand = self.use("engine.compute_value", input_summary={"pairs": len(scored)}, scored=scored, portfolio=port, elig=elig)
        segs = self.use("engine.assign_segments", input_summary={"pairs": len(cand)}, cand=cand)
        return {"portfolio": port, "cand": cand, "elig": elig, "segments": segs, "validation": validation,
                "model_run_id": model_run_id, **self.plan(port, cand)}

    def plan(self, port: pd.DataFrame, cand: pd.DataFrame, attempts: int = 3) -> dict:
        """Choose today's actions for today's team: solve, split into collector queues, compare, simulate."""
        limits = limits_from(self.ctx.cfg)
        n_cand = int((cand["allowed"] & (cand["value"] > 0)).sum())
        res = self.use("engine.optimise", input_summary={"candidates": n_cand, "minutes_limit": limits.capacity_minutes},
                       cand=cand, limits=limits)
        for attempt in range(1, attempts + 1):
            packing = self.use("engine.assign_collectors", input_summary={"plan": "optimised", "rows": len(res.plan)},
                               plan=res.plan, label="optimised")
            if packing.unassigned.empty:
                break
            lost = float(packing.unassigned["minutes"].sum())
            if attempt == attempts:
                self.note(f"{len(packing.unassigned)} task(s) still do not fit any single shift after {attempts} solves; "
                          "leaving them out of today's plan", dropped=packing.unassigned["account_token"].tolist())
                break
            # The team total had room but no one person did: tighten the total by what did not fit and solve again.
            limits = replace(limits, capacity_minutes=max(0.0, res.minutes_used - lost))
            self.note(f"{len(packing.unassigned)} task(s) ({lost:,.0f} min) fit the team total but no single collector's "
                      f"shift; re-solving with {limits.capacity_minutes:,.0f} minutes", attempt=attempt)
            res = self.use("engine.optimise", input_summary={"candidates": n_cand, "minutes_limit": limits.capacity_minutes,
                                                             "retry": attempt}, cand=cand, limits=limits)
        plan = packing.plan.sort_values("value", ascending=False).reset_index(drop=True)
        plan["rank"] = range(1, len(plan) + 1)
        res = replace(res, plan=plan, objective=float(plan["value"].sum()), minutes_used=float(plan["minutes"].sum()),
                      sms_used=int((plan["action"] == limits.sms_action).sum()),
                      hardship_used=int((plan["action"] == limits.hardship_action).sum()))

        risk, val = self.use("engine.baselines", input_summary={"accounts": len(port)}, portfolio=port, cand=cand)
        plans = {"optimised": res.plan, "sort_by_risk": risk, "sort_by_value": val}
        mc = self.use("engine.simulate_policies", input_summary={k: len(v) for k, v in plans.items()}, plans=plans, cand=cand)
        des = {k: self.use("engine.simulate_floor", input_summary={"plan": k, "rows": len(plans[k])}, plan=plans[k])
               for k in ("optimised", "sort_by_risk")}
        lift = (res.objective - float(risk["value"].sum()))
        self.note(f"Optimiser estimate beats sort-by-risk by ${lift:,.0f} on the same team, shifts and policy",
                  optimised=res.objective, sort_by_risk=float(risk["value"].sum()), sort_by_value=float(val["value"].sum()))
        return {"solver": res, "plans": plans, "mc": mc, "des": des}


class ExplanationAgent(Agent):
    name = "Explanation Agent"
    title = "Explains every recommendation"
    responsibility = "Stamp facts with provenance, draft templates with the LLM, reject any template containing a number."
    modules = ("lastmile.agents.provenance", "lastmile.agents.llm", "lastmile.agents.templates")

    def run(self, dec: dict) -> tuple[dict, dict]:
        plan = dec["plans"]["optimised"]
        facts = self.use("provenance.build_facts", input_summary={"accounts": len(plan)}, plan=plan, cand=dec["cand"],
                         portfolio=dec["portfolio"], segs=dec["segments"], model_run_id=dec["model_run_id"])
        combos = sorted({(f["_segment"], a) for (_, f), a in zip(facts.items(), plan["action"], strict=True)})
        self.note(f"{len(combos)} distinct segment x action combinations need templates; "
                  "only combinations are sent to the LLM, never account data")
        drafted = self.use("llm.draft_templates", input_summary={"combinations": len(combos), "mode": self.ctx.llm_mode},
                           combos=combos)
        checked = self.use("provenance.validate_templates", input_summary={"drafted": len(drafted)}, combos=combos, drafted=drafted)
        rejected = {(r["segment"], r["action"]): {**r["rejected"], "problems": r["problems"]}
                    for r in checked["report"] if r["problems"] and r["rejected"]}
        if rejected and self.ctx.llm is not None:
            # One bounded self-correction: send the checker's exact reasons back, then check again. Never a loop.
            self.note(f"{len(rejected)} AI draft(s) failed the checks; sending the reasons back for one rewrite",
                      problems={f"{s} x {a}": v["problems"] for (s, a), v in rejected.items()})
            rewritten = self.use("llm.draft_templates", input_summary={"combinations": len(rejected), "retry": True},
                                 combos=list(rejected), feedback=rejected)
            checked = self.use("provenance.validate_templates", input_summary={"drafted": len(rewritten), "retry": True},
                               combos=combos, drafted={**drafted, **rewritten})
        return facts, checked["final"]


class SupervisorAgent(Agent):
    name = "Supervisor Agent"
    title = "Orchestrates the run"
    responsibility = "Sequence the stages, hand work to the accountable agent, stop on failure, publish for approval."
    modules = ("lastmile.agents.supervisor", "lastmile.store.db")


AGENT_CLASSES = [SupervisorAgent, IngestionAgent, ConfigurationAgent, DataStewardAgent, FeatureAgent,
                 DecisionAgent, PolicyAgent, ExplanationAgent]
