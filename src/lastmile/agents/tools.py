"""The tool catalogue. Each tool wraps a deterministic module and names its accountable agent.

Every tool returns (value, summary). The summary is what the Admin Console shows for the call.
All tools are set-based: they take whole frames, never a single account in a loop.
"""

from __future__ import annotations

import time
import uuid

import numpy as np
import pandas as pd

from lastmile.agents import provenance, templates
from lastmile.agents.llm.base import LLMError
from lastmile.agents.registry import Registry
from lastmile.config.resolve import resolve
from lastmile.engine import baseline, collectors, optimise, segments, simulate_des, simulate_mc
from lastmile.engine.uplift import TLearner
from lastmile.engine.value import evaluate
from lastmile.features import builder, leakage, profile
from lastmile.governance import audit, escalation, policy
from lastmile.ingest.gates import run_gates
from lastmile.ingest.mapping import map_feed
from lastmile.ingest.tokenise import tokenise
from lastmile.kpi.metrics import qini_coefficient, uplift_by_decile
from lastmile.store import artifacts, db, llm_calls, rosters

INGESTION, CONFIG, STEWARD = "Ingestion Agent", "Configuration Agent", "Data Steward Agent"
FEATURE, DECISION, POLICY = "Feature Agent", "Decision Agent", "Policy Agent"
EXPLAIN, SUPERVISOR = "Explanation Agent", "Supervisor Agent"


def _save(ctx, stage: str, name: str, df: pd.DataFrame, description: str, mask: list[str] | None = None) -> dict:
    meta = artifacts.save_frame(ctx.run_id, name, df, ctx.data_dir)
    meta.update({"stage": stage, "description": description, "mask_columns": mask or []})
    ctx.artifacts.setdefault(stage, []).append(meta)
    return meta


def limits_from(cfg) -> optimise.Limits:
    c = cfg.institution
    return optimise.Limits(cfg.plannable_minutes, c.capacity.sms_per_day_max, c.capacity.hardship_slots_per_day,
                           c.contact_policy.max_contacts_per_member_per_day)


def capacities_from(cfg) -> dict[str, float]:
    """Plannable minutes per working collector, in roster order."""
    return {c.id: cfg.plannable_for(c) for c in cfg.team.working}


def shifts_from(cfg) -> dict[str, int]:
    return {c.id: c.shift_minutes for c in cfg.team.working}


def build_registry() -> Registry:
    r = Registry()

    # ------------------------------------------------------------------ Ingestion Agent
    @r.register("bank.health", "bank_api  GET /api/v1/health", INGESTION, "Check the bank API is up and read its as-of date", "api")
    def bank_health(ctx):
        h = ctx.bank.health(ctx.cfg.institution.source.health_endpoint)
        return h, {**h, "_message": f"{h['institution']} is up, data as of {h['as_of']}"}

    @r.register("bank.list_feeds", "bank_api  GET /api/v1/feeds", INGESTION, "Discover the feeds the bank publishes", "api")
    def bank_feeds(ctx):
        f = ctx.bank.feeds(ctx.cfg.institution.source.catalogue_endpoint)
        return f, {"feeds": [{"feed": x["feed"], "rows": x["row_count"], "pii": x["contains_pii"]} for x in f["feeds"]],
                   "_message": f"{len(f['feeds'])} feeds published"}

    @r.register("bank.fetch_feed", "lastmile.ingest.bank_client  GET /api/v1/<feed>", INGESTION,
                "Pull a feed page by page, exactly as the bank sends it", "api")
    def bank_fetch(ctx, feed: str, path: str):
        res = ctx.bank.fetch_feed(feed, path)
        return res, {"feed": feed, "path": path, "rows": res.rows, "pages": res.pages, "api_ms": res.ms,
                     "bank_columns": list(res.frame.columns), "calls": res.calls,
                     "_message": f"{path}: {res.rows:,} rows in {res.pages} page(s), {res.ms} ms"}

    @r.register("landing.store", "lastmile.store.artifacts", INGESTION, "Write the untouched feed to the landing store")
    def landing_store(ctx, feed: str, frame: pd.DataFrame, pii_source_cols: list[str]):
        meta = _save(ctx, "ingestion", f"landing_{feed}", frame,
                     f"Raw '{feed}' feed with the bank's own column names", mask=pii_source_cols)
        return meta, {"artifact": meta["name"], "rows": meta["rows"], "_message": f"landing_{feed} stored"}

    # -------------------------------------------------------------- Configuration Agent
    @r.register("config.load_packs", "lastmile.config.resolve", CONFIG,
                "Load, validate and merge the Scenario, Institution and Run packs; hash the result")
    def config_load(ctx, run_name: str):
        cfg = resolve(run_name)
        s, i = cfg.scenario, cfg.institution
        summary = {"config_hash": cfg.config_hash, "files": cfg.files, "scenario": s.scenario.id,
                   "institution": i.institution.name, "actions": [a.id for a in s.actions],
                   "features": len(s.causal.features), "excluded_features": s.causal.exclude_features,
                   "eligibility_rule_sets": {k: len(v.all) + len(v.any) for k, v in i.eligibility.items()},
                   "suppression_flags": i.suppression, "default_team": {"agents": i.capacity.agents,
                                                                         "minutes_per_agent": i.capacity.minutes_per_agent},
                   "planning_buffer": i.capacity.planning_buffer,
                   "_message": f"3 packs valid, hash {cfg.config_hash[:12]}"}
        return cfg, summary

    @r.register("roster.load", "lastmile.store.rosters", CONFIG,
                "Read today's team from the Manager Console: who is working and for how many minutes")
    def roster_load(ctx, roster_date: str, override=None):
        team = override or rosters.for_date(roster_date, ctx.cfg.institution, ctx.data_dir)
        ctx.cfg.roster = team
        working = team.working
        buffer = ctx.cfg.institution.capacity.planning_buffer
        return team, {"roster_date": roster_date, "source": team.source, "saved_by": team.saved_by,
                      "collectors": [{"id": c.id, "name": c.name, "present": c.present, "shift_minutes": c.shift_minutes}
                                     for c in team.collectors],
                      "working": len(working), "total_minutes": team.total_minutes,
                      "plannable_minutes": ctx.cfg.plannable_minutes, "planning_buffer": buffer,
                      "_message": f"{len(working)} of {len(team.collectors)} collectors working, {team.total_minutes:,} min "
                                  f"({ctx.cfg.plannable_minutes:,.0f} plannable) - roster {team.source.replace('_', ' ')}"}

    @r.register("config.map_fields", "lastmile.ingest.mapping", CONFIG,
                "Rename bank columns to canonical names using the institution field map")
    def config_map(ctx, feed: str, raw: pd.DataFrame):
        canon, report = map_feed(feed, raw, ctx.cfg.institution.field_map[feed])
        return canon, {**report, "_message": f"{feed}: {len(report['mapped'])} fields mapped, "
                                              f"{len(report['unmapped_source_columns'])} ignored"}

    # --------------------------------------------------------------- Data Steward Agent
    @r.register("quality.run_gates", "lastmile.ingest.gates", STEWARD,
                "Freshness, volume, completeness, domain, referential and suppression-integrity gates")
    def quality_gates(ctx, frames: dict):
        prev = ctx.state.get("previous_score_rows")
        clean, results, quarantine = run_gates(frames, ctx.cfg.institution, ctx.as_of, prev)
        return (clean, results, quarantine), {
            "gates": [g.dict() for g in results], "quarantined_rows": int(len(quarantine)),
            "_message": f"{sum(g.passed for g in results)}/{len(results)} gates passed, {len(quarantine)} row(s) quarantined"}

    @r.register("privacy.tokenise", "lastmile.ingest.tokenise", STEWARD,
                "Replace account and member identifiers with HMAC tokens; move PII to the identity vault")
    def privacy_tokenise(ctx, frames: dict):
        tok, identity, report = tokenise(frames, ctx.cfg.institution.pii_fields)
        identity.to_parquet(artifacts.identity_path(ctx.run_id, ctx.data_dir), index=False)
        return tok, {**report, "_message": f"{report['identity_rows']:,} identities vaulted; PII removed downstream"}

    # -------------------------------------------------------------------- Feature Agent
    @r.register("features.join_portfolio", "lastmile.features.builder", FEATURE,
                "Join delinquent, scored accounts with member, consent and suppression data")
    def feat_join(ctx, canon: dict):
        port, info = builder.join_portfolio(canon)
        return port, {**info, "_message": f"{info['portfolio_rows']:,} accounts in today's decision population"}

    @r.register("features.calibrate_pd", "lastmile.features.calibration", FEATURE,
                "Turn the bank's risk output (grade or probability) into 12-month and decision-horizon default probabilities")
    def feat_pd(ctx, portfolio: pd.DataFrame):
        port, info = builder.attach_pd(portfolio, ctx.cfg)
        return port, {**info, "_message": f"{info['input_type']} risk scores calibrated, {info['horizon']}"}

    @r.register("features.lookup_lgd", "lastmile.features.lgd", FEATURE, "Loss given default from the recovery table")
    def feat_lgd(ctx, portfolio: pd.DataFrame):
        port, info = builder.attach_lgd(portfolio, ctx.cfg)
        return port, {**info, "_message": f"LGD attached, mean {info['mean_lgd']}"}

    @r.register("features.build_training_set", "lastmile.features.builder", FEATURE,
                "One row per past decision: features strictly before t, matured 30-day outcome after t")
    def feat_train(ctx, canon: dict):
        train, info = builder.build_training_set(canon, ctx.cfg, ctx.as_of)
        return train, {**info, "_message": f"{info['training_rows']:,} matured decisions; "
                                           f"{info['excluded_immature_labels']:,} held back (label not matured)"}

    @r.register("features.build_online_features", "lastmile.features.builder", FEATURE,
                "Today's feature vector per account, same definitions as training")
    def feat_online(ctx, portfolio: pd.DataFrame, canon: dict):
        X = builder.build_online_features(portfolio, canon, ctx.cfg, ctx.as_of)
        return X, {"rows": int(len(X)), "features": ctx.cfg.scenario.causal.features,
                   "_message": f"{len(X):,} x {len(ctx.cfg.scenario.causal.features)} feature matrix"}

    @r.register("features.leakage_guard", "lastmile.features.leakage", FEATURE,
                "Refuse any excluded (post-outcome) feature; confirm every requested feature exists")
    def feat_guard(ctx, frame: pd.DataFrame):
        c = ctx.cfg.scenario.causal
        res = leakage.guard_features(c.features, c.exclude_features, frame)
        return res, {**res, "_message": "no excluded features requested"}

    @r.register("features.feasibility_profile", "lastmile.features.profile", FEATURE,
                "Can this data support causal estimation? Prevalence, overlap, arm sizes, leakage scan")
    def feat_profile(ctx, train: pd.DataFrame):
        p = profile.feasibility(train, ctx.cfg.scenario.causal.features, ctx.cfg.scenario.causal.control_value)
        return p, {**p, "_message": f"{sum(c['passed'] for c in p['checks'])}/{len(p['checks'])} feasibility checks passed"}

    # ------------------------------------------------------------------- Decision Agent
    @r.register("kpi.validate_uplift", "lastmile.engine.uplift + lastmile.kpi.metrics", DECISION,
                "Fit on a training split, score the holdout: Qini and uplift by decile per action")
    def validate(ctx, train: pd.DataFrame):
        c = ctx.cfg.scenario.causal
        actions = [a.id for a in ctx.cfg.scenario.actions]
        rng = np.random.default_rng(ctx.cfg.scenario.simulation.seed)
        mask = rng.random(len(train)) >= c.validation.holdout_fraction
        m = TLearner(actions, c.control_value, c.features, c.bootstrap_rounds, 11).fit(
            train[mask], train[mask]["T"], train[mask]["Y"])
        ho = train[~mask]
        out = {}
        for a in actions:
            sub = ho[ho["T"].isin([a, c.control_value])]
            e = m.effect(sub, a).point
            y, t = sub["Y"].to_numpy(), (sub["T"] == a).to_numpy()
            out[a] = {"qini": round(qini_coefficient(y, t, e), 4), "holdout_rows": int(len(sub)),
                      "deciles": uplift_by_decile(y, t, e)}
        ok = all(v["qini"] >= c.validation.min_qini for v in out.values())
        return {"per_action": out, "passed": ok}, {
            "holdout_fraction": c.validation.holdout_fraction, "qini": {a: v["qini"] for a, v in out.items()},
            "min_qini": c.validation.min_qini, "passed": ok,
            "_message": "holdout Qini " + ", ".join(f"{a} {v['qini']:+.3f}" for a, v in out.items())}

    @r.register("engine.train_uplift", "lastmile.engine.uplift", DECISION,
                "Fit the production T-learner (bagged boosted trees per arm) on all matured decisions")
    def train_uplift(ctx, train: pd.DataFrame):
        c = ctx.cfg.scenario.causal
        t0 = time.perf_counter()
        model = TLearner([a.id for a in ctx.cfg.scenario.actions], c.control_value, c.features,
                         c.bootstrap_rounds, ctx.cfg.scenario.simulation.seed).fit(train, train["T"], train["Y"])
        model_run_id = f"tlearner-{uuid.uuid4().hex[:8]}"
        model.save(artifacts.model_path(model_run_id, ctx.data_dir))
        return (model, model_run_id), {"model_run_id": model_run_id, "arm_rows": model.arm_rows,
                                       "bag_size_per_arm": max(2, c.bootstrap_rounds),
                                       "fit_seconds": round(time.perf_counter() - t0, 2),
                                       "_message": f"{model_run_id} trained on {len(train):,} decisions"}

    @r.register("engine.score_actions", "lastmile.engine.uplift", DECISION,
                "Estimate baseline cure and the uplift of every action for every account, with intervals")
    def score(ctx, model: TLearner, X: pd.DataFrame, portfolio: pd.DataFrame):
        p0 = model.p0(X)
        frames = []
        for a in ctx.cfg.scenario.actions:
            e = model.effect(X, a.id)
            frames.append(pd.DataFrame({"account_token": portfolio["account_token"], "action": a.id, "p0": p0,
                                        "uplift": e.point, "uplift_lower": e.lower, "uplift_upper": e.upper,
                                        "uplift_std": e.std}))
        s = pd.concat(frames, ignore_index=True)
        mean = s.groupby("action")["uplift"].mean().round(4).to_dict()
        return s, {"pairs_scored": int(len(s)), "mean_uplift_by_action": mean,
                   "_message": f"{len(s):,} account x action uplift estimates"}

    @r.register("engine.compute_value", "lastmile.engine.value", DECISION,
                "Expected loss and action value in dollars, from the scenario's formulas (numexpr)")
    def value(ctx, scored: pd.DataFrame, portfolio: pd.DataFrame, elig: pd.DataFrame):
        o = ctx.cfg.scenario.objective
        port = portfolio.copy()
        port["expected_loss"] = evaluate(o.expected_loss, port)
        cand = scored.merge(port[["account_token", "member_token", "exposure", "lgd", "pd_h", "pd_12m", "expected_loss"]],
                            on="account_token").merge(
            elig[["account_token", "action", "allowed", "blocked_reasons", "member_contact_room", "is_contact",
                  "minutes", "cost_cash"]], on=["account_token", "action"])
        cand["value"] = evaluate(o.action_value, cand)
        return (port, cand), {"expected_loss_formula": o.expected_loss, "action_value_formula": o.action_value,
                              "portfolio_expected_loss": round(float(port["expected_loss"].sum()), 2),
                              "positive_value_allowed_pairs": int(((cand["value"] > 0) & cand["allowed"]).sum()),
                              "_message": f"expected loss ${port['expected_loss'].sum():,.0f} across the book"}

    @r.register("engine.assign_segments", "lastmile.engine.segments", DECISION,
                "Persuadable / sure thing / lost cause / sleeping dog from estimated baseline and uplift")
    def segs(ctx, cand: pd.DataFrame):
        g = cand.groupby("account_token")
        best = cand.loc[g["uplift"].idxmax(), ["account_token", "uplift", "uplift_lower", "p0"]].set_index("account_token")
        worst = cand[cand["is_contact"]].groupby("account_token")["uplift"].min()
        seg = segments.assign(best["p0"].to_numpy(), best["uplift"].to_numpy(), best["uplift_lower"].to_numpy(),
                              worst.reindex(best.index).to_numpy(), ctx.cfg.scenario.segments)
        out = pd.DataFrame({"account_token": best.index, "segment": seg.to_numpy()})
        counts = out["segment"].value_counts().to_dict()
        return out, {"segments": counts, "_message": ", ".join(f"{k} {v}" for k, v in counts.items())}

    @r.register("engine.optimise", "lastmile.engine.optimise  (PuLP -> CBC)", DECISION,
                "Mixed-integer assignment under capacity, SMS, hardship and per-member contact constraints")
    def opt(ctx, cand: pd.DataFrame, limits: optimise.Limits | None = None):
        limits = limits or limits_from(ctx.cfg)
        res = optimise.assign(cand, limits)
        return res, {"status": res.status, "objective": round(res.objective, 2), "selected": int(len(res.plan)),
                     "variables": res.variables, "constraints": res.constraints, "solve_ms": res.solve_ms,
                     "minutes_used": res.minutes_used, "minutes_limit": limits.capacity_minutes,
                     "sms_used": res.sms_used, "hardship_used": res.hardship_used,
                     "actions": res.plan["action"].value_counts().to_dict(),
                     "_message": f"CBC {res.status}: {len(res.plan)} actions, est. ${res.objective:,.0f}, {res.solve_ms} ms"}

    @r.register("engine.assign_collectors", "lastmile.engine.collectors", DECISION,
                "Split the plan into one queue per collector so each queue fits that person's shift")
    def assign_collectors(ctx, plan: pd.DataFrame, label: str):
        caps = capacities_from(ctx.cfg)
        packing = collectors.pack(plan, caps)
        names = {c.id: c.name for c in ctx.cfg.team.collectors}
        queues = collectors.queue_summary(packing.plan, caps, names)
        return packing, {"plan": label, "collectors": len(caps), "queues": queues,
                         "unassigned": int(len(packing.unassigned)),
                         "unassigned_minutes": float(packing.unassigned["minutes"].sum()) if len(packing.unassigned) else 0.0,
                         "_message": f"{label}: {len(packing.plan):,} tasks across {len(caps)} collector queue(s)"
                                     + (f", {len(packing.unassigned)} did not fit any single shift" if len(packing.unassigned) else "")}

    @r.register("engine.baselines", "lastmile.engine.baseline", DECISION,
                "Same policy and capacity, but work the list top-down by risk or by dollars at stake")
    def base(ctx, portfolio: pd.DataFrame, cand: pd.DataFrame):
        lim = limits_from(ctx.cfg)
        mins = {a.id: a.cost_minutes for a in ctx.cfg.scenario.actions}
        caps = capacities_from(ctx.cfg)
        risk = baseline.greedy(portfolio, cand, lim, "pd_h", mins, "sort_by_risk", caps)
        val = baseline.greedy(portfolio, cand, lim, "expected_loss", mins, "sort_by_value", caps)
        return (risk, val), {"sort_by_risk": {"selected": len(risk), "estimated_value": round(float(risk["value"].sum()), 2)},
                             "sort_by_value": {"selected": len(val), "estimated_value": round(float(val["value"].sum()), 2)},
                             "_message": f"risk sort est. ${risk['value'].sum():,.0f}; value sort est. ${val['value'].sum():,.0f}"}

    @r.register("engine.simulate_policies", "lastmile.engine.simulate_mc", DECISION,
                "Monte Carlo: distribution of avoided loss and staff minutes for each candidate policy")
    def mc(ctx, plans: dict[str, pd.DataFrame], cand: pd.DataFrame):
        sim = ctx.cfg.scenario.simulation
        cols = ["account_token", "action", "p0", "uplift", "uplift_std", "expected_loss"]
        out = {}
        for name, p in plans.items():
            j = p[["account_token", "action", "minutes", "value"]].merge(cand[cols], on=["account_token", "action"], how="left")
            out[name] = simulate_mc.simulate(j, sim.monte_carlo_runs, ctx.cfg.total_minutes, sim.seed)
        return out, {k: {m: round(v[m]) for m in ("value_p10", "value_p50", "value_p90")} | {
            "prob_over_shift": round(v["prob_over_capacity"], 3)} for k, v in out.items()} | {
            "runs": sim.monte_carlo_runs,
            "_message": "; ".join(f"{k} P50 ${v['value_p50']:,.0f}" for k, v in out.items())}

    @r.register("engine.simulate_floor", "lastmile.engine.simulate_des  (SimPy)", DECISION,
                "Discrete-event model of the collections floor: does each collector's queue fit their shift?")
    def des(ctx, plan: pd.DataFrame):
        res = simulate_des.simulate_floor(plan, shifts_from(ctx.cfg), ctx.cfg.scenario.simulation.des_replications,
                                          ctx.cfg.scenario.simulation.seed)
        return res, {**res, "_message": f"{res['completion_rate_mean']:.0%} of {res['voice_tasks']} voice tasks complete in shift"}

    @r.register("engine.explain_selection", "lastmile.engine.optimise  (counterfactual re-solve)", DECISION,
                "Why this account and not another: re-solve with it forced in or out and report the difference")
    def explain(ctx, cand: pd.DataFrame, base_plan: pd.DataFrame, account_token: str):
        base_res = optimise.PlanResult(base_plan, "Optimal", float(base_plan["value"].sum()), 0, 0, 0, 0, 0, 0)
        res = optimise.explain_selection(cand, limits_from(ctx.cfg), base_res, account_token)
        return res, {k: v for k, v in res.items() if not isinstance(v, list)} | {
            "_message": f"counterfactual for {account_token}: {res.get('reason', 'selected')}"}

    # --------------------------------------------------------------------- Policy Agent
    @r.register("policy.eligibility", "lastmile.governance.policy", POLICY,
                "Evaluate suppression, consent, contact caps and eligibility rules for every account and action")
    def elig(ctx, portfolio: pd.DataFrame):
        e = policy.eligibility(portfolio, ctx.cfg)
        s = policy.summary(e)
        return e, {**s, "_message": f"{s['pairs_allowed']:,} actions allowed, {s['pairs_blocked']:,} blocked"}

    @r.register("policy.verify_plan", "lastmile.governance.policy", POLICY,
                "Independent post-solve re-check of every selected action against policy and capacity")
    def verify(ctx, plan: pd.DataFrame, elig: pd.DataFrame, label: str):
        v = policy.verify(plan, elig, ctx.cfg)
        return v, {"plan": label, "violations": v, "count": len(v),
                   "_message": f"{label}: {len(v)} violation(s)"}

    @r.register("governance.escalate", "lastmile.governance.escalation", POLICY,
                "Flag accounts that need a senior reviewer: high exposure, wide interval, hardship")
    def escalate(ctx, plan: pd.DataFrame):
        flags = escalation.flag(plan, ctx.cfg.institution.escalation)
        codes = pd.Series([f["code"] for fl in flags for f in fl]).value_counts().to_dict() if len(flags) else {}
        return flags, {"escalated_accounts": int((flags.map(len) > 0).sum()), "by_code": codes,
                       "_message": f"{int((flags.map(len) > 0).sum())} account(s) escalated"}

    @r.register("audit.append", "lastmile.governance.audit", (POLICY, SUPERVISOR),
                "Append a hash-chained, tamper-evident audit event")
    def audit_append(ctx, event_type: str, actor: str, payload: dict):
        h = audit.append(ctx.run_id, event_type, actor, payload, ctx.data_dir)
        return h, {"event_type": event_type, "hash": h[:16], "_message": f"audit {event_type} #{h[:10]}"}

    # --------------------------------------------------------------- Explanation Agent
    @r.register("provenance.build_facts", "lastmile.agents.provenance", EXPLAIN,
                "Stamp every figure a rationale may cite with the model, solver or config that produced it")
    def facts(ctx, plan: pd.DataFrame, cand: pd.DataFrame, portfolio: pd.DataFrame, segs: pd.DataFrame, model_run_id: str):
        cfg, rid = ctx.cfg, ctx.run_id
        labels = {a.id: a.label for a in cfg.scenario.actions}
        pidx = portfolio.set_index("account_token")
        sidx = segs.set_index("account_token")["segment"]
        by_acct = {k: g.sort_values("value", ascending=False) for k, g in cand[cand["allowed"]].groupby("account_token")}
        out = {}
        for _, r in plan.iterrows():
            tok, p = r["account_token"], pidx.loc[r["account_token"]]
            c = cand[(cand["account_token"] == tok) & (cand["action"] == r["action"])].iloc[0]
            alts = by_acct.get(tok, pd.DataFrame())
            alts = alts[alts["action"] != r["action"]]
            ru_action, ru_value = ("no action", 0.0) if alts.empty or alts.iloc[0]["value"] <= 0 else (
                labels[alts.iloc[0]["action"]], float(alts.iloc[0]["value"]))
            seg = sidx.get(tok, "uncertain")
            out[tok] = {
                "member_noun": provenance.stamp("member_noun", cfg.institution.institution.customer_noun, "config:institution", rid),
                "institution_name": provenance.stamp("institution_name", cfg.institution.institution.name, "config:institution", rid),
                "product_label": provenance.stamp("product_label", provenance.PRODUCT_LABELS.get(p["product"], p["product"]), "bank:accounts", rid),
                "exposure": provenance.stamp("exposure", float(p["exposure"]), "bank:accounts.CURR_BAL", rid),
                "dpd": provenance.stamp("dpd", int(p["dpd"]), "bank:accounts.DPD_CNT", rid),
                "min_payment": provenance.stamp("min_payment", float(p["min_payment"]), "bank:accounts.MIN_PMT_AMT", rid),
                "risk_grade": provenance.stamp("risk_grade", p["risk_grade"], "bank:risk-scores", rid),
                "pd_horizon": provenance.stamp("pd_horizon", float(p["pd_h"]), "calc:pd_calibration", rid),
                "lgd": provenance.stamp("lgd", float(p["lgd"]), "calc:lgd_table", rid),
                "expected_loss": provenance.stamp("expected_loss", float(c["expected_loss"]), "calc:expected_loss", rid),
                "base_cure": provenance.stamp("base_cure", float(c["p0"]), f"model:{model_run_id}", rid),
                "uplift": provenance.stamp("uplift", float(c["uplift"]), f"model:{model_run_id}", rid),
                "uplift_low": provenance.stamp("uplift_low", float(c["uplift_lower"]), f"model:{model_run_id}", rid),
                "uplift_high": provenance.stamp("uplift_high", float(c["uplift_upper"]), f"model:{model_run_id}", rid),
                "action_label": provenance.stamp("action_label", labels[r["action"]], "config:actions", rid),
                "action_value": provenance.stamp("action_value", float(c["value"]), "calc:action_value", rid),
                "minutes": provenance.stamp("minutes", float(c["minutes"]), "config:actions", rid),
                "rank": provenance.stamp("rank", int(r["rank"]), "solver:cbc", rid),
                "segment_label": provenance.stamp("segment_label", segments.LABELS[seg], "engine:segments", rid),
                "runner_up_action": provenance.stamp("runner_up_action", ru_action, "calc:alternatives", rid),
                "runner_up_value": provenance.stamp("runner_up_value", ru_value, "calc:alternatives", rid),
                "horizon_days": provenance.stamp("horizon_days", cfg.scenario.objective.horizon_days, "config:objective", rid),
                "_segment": seg,
            }
        return out, {"accounts": len(out), "facts_per_account": len(provenance.FORMATS) - 1,
                     "_message": f"{len(out)} fact sheets stamped"}

    @r.register("llm.draft_templates", "lastmile.agents.llm", EXPLAIN,
                "Ask the LLM for rationale and script templates per segment x action. No account data is sent", "llm")
    def draft(ctx, combos: list[tuple[str, str]], feedback: dict | None = None):
        cfg = ctx.cfg
        if ctx.llm is None:
            return {}, {"mode": ctx.llm_mode, "templates": 0, "_message": f"no LLM call ({ctx.llm_mode}); deterministic templates"}
        system = (
            "You write templates for a credit union's collections worklist. A {{placeholder}} is replaced by a VALUE "
            "(see 'placeholders' for each meaning and an example of how it renders), so write the words around it and "
            "never use a placeholder as a label or as a noun. Values are bare figures with no words attached, so never put "
            "'a' or 'an' directly before one. Bad: 'has an {{expected_loss}} of {{expected_loss}}', 'provides an "
            "{{uplift}}'. Good: 'carries an expected loss of {{expected_loss}} if it does not catch up', 'raises the "
            "chance of catching up by {{uplift}}'. HARD RULES: never write any digit or number "
            "yourself (the examples only show rendering - never copy them); use each placeholder at most once in a "
            "rationale; rationale templates must include {{expected_loss}}, {{uplift}}, {{action_value}} and "
            "{{runner_up_action}}; tailor each rationale to its segment - persuadable: contact is expected to change the "
            "outcome; sure_thing: likely to catch up anyway, so justify the low-cost action; lost_cause: recovery unlikely; "
            "sleeping_dog: contact may backfire, so urge careful review; uncertain: the estimate is not confident. "
            "Scripts are spoken or sent to the member: warm and respectful, no internal action names, no mention of "
            "models, uplift, risk grades or expected loss; allowed script placeholders only. Return JSON only.")
        prompt = {
            "institution_noun": cfg.institution.institution.customer_noun,
            "placeholders": {k: {"meaning": m, "renders_like": ex} for k, (m, ex) in provenance.MEANINGS.items()},
            "allowed_rationale_placeholders": sorted(provenance.RATIONALE_KEYS),
            "allowed_script_placeholders": sorted(provenance.SCRIPT_KEYS),
            "segments": {k: segments.LABELS[k] for k in segments.LABELS},
            "actions": {a.id: a.label for a in cfg.scenario.actions},
            "combinations": [{"segment": s, "action": a} for s, a in combos],
            "output_format": {"templates": [{"segment": "...", "action": "...", "rationale": "...", "script": "..."}]},
        }
        if feedback:  # one bounded rewrite: the exact reasons each earlier draft was rejected
            prompt["rewrite_requests"] = [{"segment": s, "action": a, "rejected_rationale": f["rationale"],
                                           "rejected_script": f["script"], "problems": f["problems"]}
                                          for (s, a), f in feedback.items()]
            system += " Some earlier drafts were rejected by an automatic checker; rewrite them fixing exactly the listed problems."
        import json
        user = json.dumps(prompt)
        ctx.llm.last_exchange = None

        def keep(parsed, error):   # the full request and response, whatever happened
            return llm_calls.record(ctx.run_id, ctx.tracer.stage, EXPLAIN, "llm.draft_templates",
                                    "rewrite rejected templates" if feedback else "draft templates",
                                    getattr(ctx.llm, "provider", "unknown"), getattr(ctx.llm, "model", "unknown"),
                                    system, user, getattr(ctx.llm, "last_exchange", None), parsed, error, ctx.data_dir)
        try:
            res = ctx.llm.generate_json(system, user)
        except LLMError as e:
            call_id = keep(None, str(e))
            return {}, {"mode": ctx.llm_mode, "rewrite": bool(feedback), "error": str(e), "llm_call_id": call_id,
                        "_message": f"LLM failed, falling back: {e}"}
        call_id = keep(res, None)
        # Models do not always honour the requested shape: accept {"templates": [...]} or a bare list, and treat
        # anything else as no drafts. A malformed reply must degrade to standard wording, never fail the run.
        items = res.get("templates", []) if isinstance(res, dict) else res if isinstance(res, list) else []
        got = {(t["segment"], t["action"]): t for t in items
               if isinstance(t, dict) and isinstance(t.get("rationale"), str) and isinstance(t.get("script"), str)
               and "segment" in t and "action" in t}
        return got, {"mode": ctx.llm_mode, "llm_call_id": call_id, "requested": len(combos), "returned": len(got), "rewrite": bool(feedback),
                     "response_shape": type(res).__name__, "sample": next(iter(got.values()), {}),
                     "_message": f"{ctx.llm_mode}: {len(got)}/{len(combos)} templates {'rewritten' if feedback else 'drafted'}"}

    @r.register("provenance.validate_templates", "lastmile.agents.provenance", EXPLAIN,
                "Reject any template with digits, unknown placeholders or missing required facts; fall back per combo")
    def validate_tpl(ctx, combos: list[tuple[str, str]], drafted: dict):
        required = set(ctx.cfg.scenario.explanation.must_state)

        def check(t: dict) -> list[str]:
            p = [f"rationale: {x}" for x in provenance.template_violations(
                t.get("rationale", ""), provenance.RATIONALE_KEYS, required, max_repeats=1)]
            return p + [f"script: {x}" for x in provenance.template_violations(
                t.get("script", ""), provenance.SCRIPT_KEYS, max_repeats=2)]

        final, report = {}, []
        for combo in combos:
            t, source = drafted.get(combo), "llm"
            problems = check(t) if t else []
            if not t or problems:
                t, source = templates.fallback(*combo), "fallback"
                # Built-in templates are held to the same rules. A failure here is a code defect: refuse to publish.
                own = check(t)
                if own:
                    raise ValueError(f"built-in template for {combo} failed validation: {own}")
            final[combo] = {"rationale": t["rationale"], "script": t["script"], "source": source}
            report.append({"segment": combo[0], "action": combo[1], "source": source, "problems": problems,
                           "rejected": drafted.get(combo) if problems else None})
        violations = sum(1 for x in report if x["problems"])
        return {"final": final, "report": report}, {"combinations": len(combos), "llm_templates_accepted": sum(1 for x in report if x["source"] == "llm"),
                       "rejected_for_violations": violations, "report": report,
                       "_message": f"{len(combos)} templates ready, {violations} LLM draft(s) rejected"}

    # ------------------------------------------------------------------ Supervisor Agent
    @r.register("worklist.publish", "lastmile.store.db", SUPERVISOR,
                "Persist the explained, verified worklist and open the human approval gate")
    def publish(ctx, plan: pd.DataFrame, facts: dict, flags: pd.Series, tpl: dict, cand: pd.DataFrame):
        labels = {a.id: a.label for a in ctx.cfg.scenario.actions}
        rows = []
        for i, r in plan.iterrows():
            tok = r["account_token"]
            f = {k: v for k, v in facts[tok].items() if not k.startswith("_")}
            seg = facts[tok]["_segment"]
            t = tpl[(seg, r["action"])]
            alts = cand[cand["account_token"] == tok][["action", "allowed", "blocked_reasons", "value", "uplift",
                                                       "uplift_lower", "uplift_upper"]]
            alts = alts.assign(label=alts["action"].map(labels)).to_dict("records")
            rows.append((ctx.run_id, tok, r["member_token"], int(r["rank"]), r["action"], seg, db.dumps(f), db.dumps(alts),
                         db.dumps(flags.loc[i]), t["rationale"], t["script"], t["source"], r.get("collector_id"),
                         int(r["queue_position"]) if "queue_position" in r and pd.notna(r["queue_position"]) else None))
        with db.connect(ctx.data_dir) as con:
            con.execute("DELETE FROM recommendations WHERE run_id = ?", (ctx.run_id,))
            con.executemany("INSERT INTO recommendations (run_id, account_token, member_token, rank, action, segment,"
                            " facts_json, alternatives_json, escalations_json, rationale_tpl, script_tpl, template_source,"
                            " collector_id, queue_position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        digits = sum(bool(provenance.DIGIT.search(provenance.PLACEHOLDER.sub("", x[9] + x[10]))) for x in rows)
        return len(rows), {"published": len(rows), "unstamped_numbers": digits,
                           "_message": f"{len(rows)} recommendations awaiting approval"}

    from lastmile.agents import (
        onboarding,  # the Onboarding Agent's tools share the registry and its ownership rules
    )
    onboarding.register(r)
    return r
