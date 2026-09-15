"""Claims about the manager's business day: the team, per-collector queues, re-planning, closing the day, config edits."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date

import pandas as pd
import pytest


def _team(*shifts, absent=()):
    from lastmile.config.schema import Collector

    return [Collector(id=f"C{i:02d}", name=f"Collector {i}", shift_minutes=m, present=i not in absent)
            for i, m in enumerate(shifts, start=1)]


# ------------------------------------------------------------------------------------ queues
def test_every_queue_fits_its_collectors_shift():
    """A 45-minute referral cannot be split: packing never overfills one person, even when the team total has room."""
    from lastmile.engine.collectors import AUTOMATED, pack

    plan = pd.DataFrame({"account_token": list("abcdef"), "action": ["HARDSHIP", "HARDSHIP", "CALL", "CALL", "SMS", "PLAN"],
                         "minutes": [45.0, 45.0, 12.0, 12.0, 0.0, 25.0], "value": [900.0, 800.0, 500.0, 400.0, 50.0, 300.0]})
    res = pack(plan, {"C01": 50.0, "C02": 50.0})           # 100 minutes in total, 139 asked for
    assert all(v <= 50.0 for v in res.load.values())
    assert set(res.plan.loc[res.plan["action"] == "SMS", "collector_id"]) == {AUTOMATED}
    assert len(res.unassigned) > 0 and res.unassigned["minutes"].sum() + sum(res.load.values()) == 139.0
    for _, q in res.plan[res.plan["collector_id"] != AUTOMATED].groupby("collector_id"):
        assert list(q.sort_values("queue_position")["value"]) == sorted(q["value"], reverse=True)   # worked by value


def test_published_plan_respects_each_collectors_shift(dirs, run, cfg):
    from lastmile.agents.tools import capacities_from
    from lastmile.pipeline.run import run_roster
    from lastmile.store import artifacts

    cfg.roster = run_roster(run, dirs["engine"])
    plan = artifacts.load_frame(run, "plan_optimised", dirs["engine"])
    caps = capacities_from(cfg)
    voice = plan[plan["minutes"] > 0]
    assert set(voice["collector_id"]) <= set(caps)
    assert all(m <= caps[c] + 1e-6 for c, m in voice.groupby("collector_id")["minutes"].sum().items())


def test_verification_catches_an_overfilled_or_absent_collector(dirs, run, cfg):
    """Independent of the packer: a queue over one shift, or work given to someone not in today, is a violation."""
    from lastmile.config.schema import Roster
    from lastmile.governance import policy
    from lastmile.store import artifacts

    plan = artifacts.load_frame(run, "plan_optimised", dirs["engine"])
    elig = artifacts.load_frame(run, "eligibility", dirs["engine"])
    ids = sorted(plan.loc[plan["minutes"] > 0, "collector_id"].unique())
    cfg = cfg.model_copy()
    cfg.roster = Roster(roster_date="x", collectors=_team(*([360] * len(ids))))
    bad = plan.copy()
    bad.loc[bad["minutes"] > 0, "collector_id"] = "C01"          # everything to one person
    assert any(v["rule"] == "collector_shift_minutes" for v in policy.verify(bad, elig, cfg))
    cfg.roster = Roster(roster_date="x", collectors=_team(*([360] * len(ids)), absent=(1,)))
    assert any(v["rule"] == "voice_task_without_working_collector" for v in policy.verify(plan, elig, cfg))


def test_roster_carries_forward_then_defaults(tmp_path, cfg):
    from lastmile.store import db, rosters

    db.init(tmp_path)
    assert rosters.for_date("2026-01-01", cfg.institution, tmp_path).source == "default"
    rosters.save("2026-01-01", _team(360, 240), "tester", data_dir=tmp_path)
    later = rosters.for_date("2026-01-05", cfg.institution, tmp_path)
    assert later.source == "carried_forward" and later.total_minutes == 600
    assert rosters.for_date("2025-12-31", cfg.institution, tmp_path).source == "default"


# ----------------------------------------------------------------------------------- re-plan
def test_replan_for_a_smaller_team_supersedes_and_fits(dirs, run, tmp_path, cfg):
    """Fewer people: a new worklist from the same data and model, every queue within its shift, the old one retired."""
    from lastmile.config.schema import Roster
    from lastmile.pipeline.run import replan, run_roster
    from lastmile.store import artifacts, db

    eng = tmp_path / "engine"
    shutil.copytree(dirs["engine"], eng)     # keep the shared run untouched for the other tests
    team = Roster(roster_date="x", source="saved", collectors=_team(360, 120, 360, absent=(3,)))
    new = replan(run, team, data_dir=eng, llm=None, llm_mode="template (tests)")
    with db.connect(eng) as con:
        rows = {r["run_id"]: dict(r) for r in con.execute("SELECT * FROM runs WHERE run_id IN (?, ?)", (run, new))}
    assert rows[new]["status"] == "awaiting_approval", rows[new]["error"]
    assert rows[run]["status"] == "superseded" and rows[run]["superseded_by"] == new
    assert rows[new]["model_run_id"] == rows[run]["model_run_id"]              # the model was reused, not retrained
    plan = artifacts.load_frame(new, "plan_optimised", eng)
    voice = plan[plan["minutes"] > 0]
    assert set(voice["collector_id"]) <= {"C01", "C02"}
    assert voice.groupby("collector_id")["minutes"].sum().max() <= 360 * 0.9 + 1e-6
    assert voice["minutes"].sum() <= 480 * 0.9 + 1e-6
    assert run_roster(new, eng).total_minutes == 480


# ------------------------------------------------------------------------------- bank day
def test_bank_day_executes_released_actions_and_moves_on(tmp_path):
    from bank_api.generator import GenParams, advance_day, generate

    generate(tmp_path, GenParams(members=600, history_days=90, seed=3, as_of=date(2026, 3, 2)))
    with sqlite3.connect(tmp_path / "bank.db") as con:
        acct = con.execute("SELECT ACCT_NBR FROM accounts WHERE ACCT_STATUS = 'DELINQUENT' LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO collection_actions (ACCT_NBR, ACTION_CD, CHANNEL, SCRIPT_TXT, APPROVED_BY, SOURCE_RUN_ID, RECEIVED_AT)"
                    " VALUES (?, 'PLAN', 'voice', 'hi', 'tester', 'run_x', 'now')", (acct,))
    res = advance_day(tmp_path)
    assert res["as_of"] == "2026-03-03" and res["engine_actions_executed"] == 1
    with sqlite3.connect(tmp_path / "bank.db") as con:
        assert con.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()[0] == "2026-03-03"
        assert con.execute("SELECT ACTION_CD FROM collections_queue_history WHERE ACCT_NBR = ? AND QUEUE_DT = '2026-03-02'",
                           (acct,)).fetchone()[0] == "PLAN"
        assert con.execute("SELECT EXECUTED_DT FROM collection_actions").fetchone()[0] == "2026-03-02"
        # label maturity still holds after the day moves: no outcome younger than 30 days is published
        assert con.execute("SELECT MAX(QUEUE_DT) FROM outcomes").fetchone()[0] <= "2026-02-01"


# --------------------------------------------------------------------------- config editor
@pytest.fixture
def editor(monkeypatch):
    from lastmile.api import config_editor

    monkeypatch.setattr(config_editor, "_warnings", lambda kind, data: [])   # warnings read the run database
    return config_editor


def test_config_check_names_the_line_and_writes_nothing(editor):
    from lastmile.config.settings import CONFIG_DIR

    path = CONFIG_DIR / "institutions" / "riverbend_cu.yaml"
    text = path.read_text()
    res = editor.validate("institutions", "riverbend_cu", text.replace("planning_buffer: 0.10", "planning_buffer: 0.7"))
    assert not res["valid"]
    err = res["errors"][0]
    assert err["path"] == "capacity.planning_buffer" and "planning_buffer" in text.splitlines()[err["line"] - 1]
    assert [c["path"] for c in res["changes"]] == ["capacity.planning_buffer"]
    assert path.read_text() == text


def test_config_check_catches_what_only_breaks_across_packs(editor):
    from lastmile.config.settings import CONFIG_DIR

    inst = (CONFIG_DIR / "institutions" / "riverbend_cu.yaml").read_text()
    renamed = inst.replace("  sms_consent:\n", "  sms_ok:\n", 1)      # the scenario's SMS action still requires sms_consent
    assert any(e["stage"] == "Across packs" for e in editor.validate("institutions", "riverbend_cu", renamed)["errors"])
    scen = (CONFIG_DIR / "scenarios" / "collections_delinquency.yaml").read_text()
    assert not editor.validate("scenarios", "collections_delinquency", scen.replace("id: CALL", "id: VISIT"))["valid"]
    assert editor.validate("institutions", "riverbend_cu", inst)["valid"]


def test_every_config_section_is_documented():
    from lastmile.config import docs
    from lastmile.config.resolve import resolve

    cfg = resolve()
    for kind, model in (("scenarios", cfg.scenario), ("institutions", cfg.institution), ("runs", cfg.run)):
        assert set(model.model_dump()) <= set(docs.SECTIONS[kind]), kind


def test_bank_seeded_before_day_simulation_recovers_without_losing_actions(tmp_path):
    """Old banks had no sealed state. It is rebuilt from the seed only if the book matches exactly; received actions survive."""
    from bank_api.generator import GenParams, advance_day, generate

    generate(tmp_path, GenParams(members=500, history_days=90, seed=8, as_of=date(2026, 4, 1)))
    for name in ("state.npz", "state.json", "pending_outcomes.parquet", "truth_2026-04-01.csv"):
        (tmp_path / "sealed" / name).unlink()
    with sqlite3.connect(tmp_path / "bank.db") as con:   # the old schema: no execution columns, no day log
        con.execute("DROP TABLE day_log")
        con.execute("ALTER TABLE collection_actions RENAME TO old")
        con.execute("CREATE TABLE collection_actions (ACTION_ID INTEGER PRIMARY KEY AUTOINCREMENT, ACCT_NBR TEXT, ACTION_CD TEXT,"
                    " CHANNEL TEXT, SCRIPT_TXT TEXT, APPROVED_BY TEXT, SOURCE_RUN_ID TEXT, RECEIVED_AT TEXT)")
        con.execute("DROP TABLE old")
        acct = con.execute("SELECT ACCT_NBR FROM accounts WHERE ACCT_STATUS = 'DELINQUENT' LIMIT 1").fetchone()[0]
        con.execute("INSERT INTO collection_actions (ACCT_NBR, ACTION_CD, CHANNEL, SCRIPT_TXT, APPROVED_BY, SOURCE_RUN_ID, RECEIVED_AT)"
                    " VALUES (?, 'CALL', 'voice', 'hi', 'tester', 'run_x', 'now')", (acct,))
    res = advance_day(tmp_path)
    assert res["as_of"] == "2026-04-02" and res["engine_actions_executed"] == 1


def test_bank_that_matches_no_seed_refuses_to_advance(tmp_path):
    from bank_api.generator import GenParams, NotAdvanceable, advance_day, generate

    generate(tmp_path, GenParams(members=300, history_days=60, seed=9, as_of=date(2026, 4, 1)))
    (tmp_path / "sealed" / "state.npz").unlink()
    with sqlite3.connect(tmp_path / "bank.db") as con:
        con.execute("UPDATE accounts SET CURR_BAL = CURR_BAL + 1 WHERE rowid = 1")   # no longer what the seed produces
    with pytest.raises(NotAdvanceable):
        advance_day(tmp_path)
