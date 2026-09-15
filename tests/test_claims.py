"""Each test protects a claim made about the system. If one fails, that claim is false."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- Layer 0
def test_config_hash_is_stable_and_npa_pack_loads(cfg):
    """A new scenario needs no engine change; the same packs always hash the same."""
    from lastmile.config.resolve import load_scenario, resolve

    assert resolve().config_hash == cfg.config_hash
    npa = load_scenario("npa_early_warning")
    assert npa.scenario.entity == "loan" and npa.causal.outcome_column == "slipped_to_npa_90d"


def test_rules_fail_closed_on_missing_data(cfg):
    """A missing value never satisfies an eligibility rule."""
    df = pd.DataFrame({"dpd": [70, None], "hardship_active": [0, 0], "hardship_plans_12m": [0, 0],
                       "product": ["AUTO", "AUTO"]})
    assert cfg.institution.eligibility["hardship_eligible"].mask(df).tolist() == [True, False]


def test_excluded_feature_is_refused():
    """Leakage guards are enforced, not advisory."""
    from lastmile.features.leakage import LeakageError, guard_features

    with pytest.raises(LeakageError):
        guard_features(["dpd_at_t", "curr_bal"], ["curr_bal"], pd.DataFrame({"dpd_at_t": [1], "curr_bal": [2]}))


# ------------------------------------------------------------------------- features
def test_contact_features_are_strictly_point_in_time():
    """An action on day t, or after it, never counts towards the features for day t."""
    from lastmile.features.builder import contact_history_features

    hist = pd.DataFrame({"account_token": ["a"] * 4, "queue_date": ["2026-01-01", "2026-01-20", "2026-02-01", "2026-02-05"],
                         "action": ["CALL", "SMS", "CALL", "CALL"], "rpc": [1, 0, 1, 1]})
    q = pd.DataFrame({"account_token": ["a"], "t": ["2026-02-01"]})
    f = contact_history_features(q, hist, "NONE").iloc[0]
    assert f["contacts_30d"] == 1            # only 2026-01-20; 02-01 is day t, 02-05 is the future
    assert f["days_since_last_contact"] == 12


def test_training_labels_have_matured(dirs, run):
    """No training row uses an outcome window that has not closed."""
    from lastmile.store import artifacts, db

    train = artifacts.load_frame(run, "training_set", dirs["engine"])
    with db.connect(dirs["engine"]) as con:
        as_of = con.execute("SELECT as_of_date FROM runs WHERE run_id = ?", (run,)).fetchone()[0]
    assert pd.to_datetime(train["t"]).max() <= pd.Timestamp(as_of) - pd.Timedelta(days=30)


# --------------------------------------------------------------------- decision
def test_optimised_plan_has_zero_policy_violations(dirs, run, cfg):
    """Every recommendation respects policy, capacity and per-member contact limits."""
    from lastmile.governance import policy
    from lastmile.store import artifacts

    plan = artifacts.load_frame(run, "plan_optimised", dirs["engine"])
    elig = artifacts.load_frame(run, "eligibility", dirs["engine"])
    assert len(plan) > 0
    assert policy.verify(plan, elig, cfg) == []


def test_per_member_contact_cap_is_per_person_not_per_account(cfg):
    """A member with three accounts is still one person: at most one contact today."""
    from lastmile.agents.tools import limits_from
    from lastmile.engine.optimise import assign

    cand = pd.DataFrame({"account_token": ["a1", "a2", "a3"], "member_token": ["m1"] * 3, "action": ["CALL"] * 3,
                         "value": [500.0, 400.0, 300.0], "minutes": [12.0] * 3, "is_contact": [True] * 3,
                         "member_contact_room": [7] * 3, "allowed": [True] * 3})
    plan = assign(cand, limits_from(cfg)).plan
    assert len(plan) == 1 and plan.iloc[0]["account_token"] == "a1"


def test_solver_is_not_a_sort():
    """Under a binding minutes budget the solver skips the biggest single prize for two smaller ones."""
    from lastmile.engine.optimise import Limits, assign

    cand = pd.DataFrame({"account_token": ["whitfield", "osei", "reyes"], "member_token": ["m1", "m2", "m3"],
                         "action": ["HARDSHIP", "PLAN", "PLAN"], "value": [2400.0, 1584.0, 1300.0],
                         "minutes": [45.0, 25.0, 25.0], "is_contact": [True] * 3, "member_contact_room": [7] * 3,
                         "allowed": [True] * 3})
    plan = assign(cand, Limits(50, 0, 5, 1)).plan
    assert set(plan["account_token"]) == {"osei", "reyes"}


def test_optimised_beats_risk_sort_on_its_own_estimates(dirs, run):
    """Same capacity and policy: the optimiser's plan is worth more than working the list by risk."""
    from lastmile.store import artifacts

    opt = artifacts.load_frame(run, "plan_optimised", dirs["engine"])["value"].sum()
    risk = artifacts.load_frame(run, "plan_sort_by_risk", dirs["engine"])["value"].sum()
    assert opt >= risk


# ------------------------------------------------------------------ division of labour
def test_no_template_contains_a_number(dirs, run):
    """Every figure a manager sees came from a tool: no digit appears in any template outside a placeholder."""
    from lastmile.agents import provenance
    from lastmile.store import db

    with db.connect(dirs["engine"]) as con:
        recs = db.rows(con, "SELECT rationale_tpl, script_tpl, facts_json FROM recommendations WHERE run_id = ?", (run,))
    assert recs
    for r in recs:
        for tpl in (r["rationale_tpl"], r["script_tpl"]):
            assert not provenance.DIGIT.search(provenance.PLACEHOLDER.sub("", tpl))
        assert all(v["source"] for v in db.loads(r["facts_json"]).values())


def test_llm_template_with_a_number_is_rejected():
    from lastmile.agents import provenance

    bad = "Worth about 4,000 dollars: {{action_value}} {{expected_loss}} {{uplift}} {{runner_up_action}}"
    assert provenance.template_violations(bad, provenance.RATIONALE_KEYS)


def test_every_builtin_template_is_valid():
    from lastmile.agents import provenance, templates

    required = {"expected_loss", "uplift", "action_value", "runner_up_action"}
    for seg in templates.SEGMENT_SENTENCE:
        for action in templates.SCRIPTS:
            t = templates.fallback(seg, action)
            assert not provenance.template_violations(t["rationale"], provenance.RATIONALE_KEYS, required, max_repeats=1)
            assert not provenance.template_violations(t["script"], provenance.SCRIPT_KEYS, max_repeats=2)


def test_placeholder_used_as_its_own_label_is_rejected():
    """Real Gemini output that passed the old checks: 'has an {{expected_loss}} of {{expected_loss}}'."""
    from lastmile.agents import provenance

    bad = ("This {{segment_label}} {{member_noun}} has an {{expected_loss}} of {{expected_loss}}. The {{action_label}} is "
           "recommended due to an {{uplift}} of {{uplift}} and an {{action_value}} of {{action_value}}. "
           "The next best option was {{runner_up_action}}.")
    assert provenance.template_violations(bad, provenance.RATIONALE_KEYS, max_repeats=1)


def test_article_before_a_value_is_rejected():
    """Real Gemini output: 'has an {{expected_loss}} if they do not catch up' renders as 'has an $9,894'."""
    from lastmile.agents import provenance

    assert provenance.template_violations("It has an {{expected_loss}} at stake.", provenance.RATIONALE_KEYS)
    assert not provenance.template_violations("A payment of {{min_payment}} is due.", provenance.SCRIPT_KEYS)


def test_articles_agree_with_rendered_values():
    from lastmile.agents import provenance

    facts = {"product_label": {"display": "auto loan", "source": "s", "run_id": None},
             "runner_up_action": {"display": "no action", "source": "s", "run_id": None},
             "action_label": {"display": "Collector call", "source": "s", "run_id": None}}
    tpl = "With a {{product_label}}. The next best option is a {{runner_up_action}}. An {{action_label}} is advised."
    assert provenance.render_text(tpl, facts) == "With an auto loan. The next best option is no action. A Collector call is advised."


def test_scripts_cannot_use_internal_action_names():
    from lastmile.agents import provenance

    script = "Hello {{first_name}}, we can explore options like a {{action_label}}."
    assert provenance.template_violations(script, provenance.SCRIPT_KEYS)


def test_agent_cannot_call_a_tool_it_does_not_own(dirs, run):
    """Responsibility is enforced: the Explanation Agent may not run the optimiser."""
    from lastmile.agents import crew
    from lastmile.agents.base import RunContext
    from lastmile.agents.registry import ToolDenied
    from lastmile.agents.tools import build_registry
    from lastmile.agents.trace import Tracer
    from lastmile.store import db

    ctx = RunContext(run_id=run, run_name="default", tracer=Tracer(run, dirs["engine"]), data_dir=dirs["engine"])
    agent = crew.ExplanationAgent(ctx, build_registry())
    with pytest.raises(ToolDenied):
        agent.use("engine.optimise", cand=pd.DataFrame())
    with db.connect(dirs["engine"]) as con:
        assert con.execute("SELECT COUNT(*) FROM trace_events WHERE run_id = ? AND status = 'denied'", (run,)).fetchone()[0] >= 1


def test_every_stage_is_traced_with_its_accountable_agent(dirs, run):
    from lastmile.store import db

    with db.connect(dirs["engine"]) as con:
        stages = db.rows(con, "SELECT stage, status FROM stages WHERE run_id = ? ORDER BY ord", (run,))
        agents = {r[0] for r in con.execute("SELECT DISTINCT agent FROM trace_events WHERE run_id = ? AND tool IS NOT NULL", (run,))}
    assert [s["status"] for s in stages][:-1] == ["done"] * 7 and stages[-1]["status"] == "waiting"
    assert {"Ingestion Agent", "Configuration Agent", "Data Steward Agent", "Feature Agent", "Decision Agent",
            "Policy Agent", "Explanation Agent", "Supervisor Agent"} <= agents


# ------------------------------------------------------------------------ governance
def test_audit_trail_is_append_only_and_chained(dirs, run):
    from lastmile.governance import audit
    from lastmile.store import db

    assert audit.verify_chain(dirs["engine"])["valid"]
    with db.connect(dirs["engine"]) as con, pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE audit_events SET actor = 'someone else'")
    with db.connect(dirs["engine"]) as con, pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM audit_events")


def test_rejection_requires_a_reason_even_at_the_database(dirs, run):
    from lastmile.governance.approvals import ApprovalError, record
    from lastmile.store import db

    with db.connect(dirs["engine"]) as con:
        tok, action = con.execute("SELECT account_token, action FROM recommendations WHERE run_id = ? LIMIT 1", (run,)).fetchone()
    with pytest.raises(ApprovalError):
        record(run, tok, "rejected", "tester", None, None, None, {action}, ["OTHER"], action, dirs["engine"])
    with db.connect(dirs["engine"]) as con, pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO approvals (run_id, account_token, decision, approver_id, decided_at) VALUES (?,?,?,?,?)",
                    (run, tok, "rejected", "tester", "now"))


def test_no_pii_downstream_of_tokenisation(dirs, run, cfg):
    from lastmile.store import artifacts

    for name in ("portfolio", "training_set", "online_features", "candidates", "plan_optimised"):
        cols = set(artifacts.load_frame(run, name, dirs["engine"]).columns)
        assert not cols & set(cfg.institution.pii_fields) and "account_id" not in cols and "member_id" not in cols


def test_counterfactual_explanation_comes_from_the_solver(dirs, run):
    from lastmile.pipeline.run import explain_selection
    from lastmile.store import artifacts

    tok = artifacts.load_frame(run, "plan_optimised", dirs["engine"]).iloc[0]["account_token"]
    res = explain_selection(run, tok, dirs["engine"])
    assert res["was_selected"] and res["cost_of_change"] >= 0


# ---------------------------------------------------------------------- architecture
def test_import_contracts_hold():
    """The maths never imports the LLM layer; the engine never imports the bank's code."""
    # Call the real executable: `python -m importlinter.cli` exits 0 without checking anything.
    exe = Path(sys.executable).parent / "lint-imports"
    assert exe.exists(), "import-linter is not installed in this environment"
    proc = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-1000:]
    assert "Contracts: 3 kept, 0 broken" in proc.stdout


@pytest.mark.parametrize("reply", [
    [{"segment": "persuadable", "action": "CALL", "rationale": "x", "script": "y"}],   # bare list
    "not json at all",                                                                   # wrong type
    {"templates": [{"segment": "persuadable"}, 42, None]},                               # malformed items
    {},                                                                                  # empty
])
def test_malformed_llm_reply_never_fails_the_run(cfg, tmp_path, reply):
    """Real Gemini returned a bare list on a rewrite and crashed the run. Any shape must degrade to standard wording."""
    from lastmile.agents.base import RunContext
    from lastmile.agents.tools import build_registry
    from lastmile.agents.trace import Tracer
    from lastmile.store import db

    class FakeLLM:
        provider, model = "fake", "fake"
        def generate_json(self, system, prompt):
            return reply

    db.init(tmp_path)
    ctx = RunContext(run_id="t", run_name="default", tracer=Tracer("t", tmp_path), cfg=cfg, llm=FakeLLM(), llm_mode="fake", data_dir=tmp_path)
    reg = build_registry()
    combos = [("persuadable", "CALL")]
    drafted, _ = reg.tools["llm.draft_templates"].fn(ctx, combos=combos)
    checked, _ = reg.tools["provenance.validate_templates"].fn(ctx, combos=combos, drafted=drafted)
    assert checked["final"][("persuadable", "CALL")]["rationale"]   # always has usable wording


def test_every_llm_call_is_stored_in_full_without_account_data(cfg, tmp_path, dirs, run):
    """The trace summarises; llm_calls keeps the exact request and response - and proves no account data was sent."""
    from lastmile.agents.base import RunContext
    from lastmile.agents.tools import build_registry
    from lastmile.agents.trace import Tracer
    from lastmile.store import artifacts, db, llm_calls

    class FakeLLM:
        provider, model = "fake", "fake-1"

        def generate_json(self, system, prompt):
            self.last_exchange = {"endpoint": "fake://", "request": {"prompt": prompt}, "http_status": 200,
                                  "response_text": '{"templates": []}', "usage": {"totalTokenCount": 7}, "latency_ms": 3}
            return {"templates": []}

    db.init(tmp_path)
    ctx = RunContext(run_id="t", run_name="default", tracer=Tracer("t", tmp_path), cfg=cfg, llm=FakeLLM(), llm_mode="fake",
                     data_dir=tmp_path)
    ctx.tracer.stage = "explanation"
    _, summary = build_registry().tools["llm.draft_templates"].fn(ctx, combos=[("persuadable", "CALL")])
    call = llm_calls.get(summary["llm_call_id"], tmp_path)
    assert call["status"] == "ok" and call["model"] == "fake-1" and call["total_tokens"] == 7
    assert call["system_prompt"] and '"combinations"' in call["user_prompt"] and call["response_text"] == '{"templates": []}'
    tokens = artifacts.load_frame(run, "portfolio", dirs["engine"])["account_token"].head(200)
    assert not any(t in call["user_prompt"] for t in tokens)
    with db.connect(tmp_path) as con, pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE llm_calls SET response_text = 'edited'")
