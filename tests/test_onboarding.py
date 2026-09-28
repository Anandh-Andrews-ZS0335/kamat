"""Claims about connecting a bank Last Mile has never seen: the brain decides, the checks hold, the engine adapts."""

from __future__ import annotations

import copy
import json
import os

import pandas as pd
import pytest

from tests.harbor_mapping import HARBOR_PROPOSAL


@pytest.fixture(scope="module")
def harbor(tmp_path_factory):
    """The second demo bank, served in-process: other paths, other column names, 180-day probabilities."""
    os.environ["HARBOR_DATA_DIR"] = str(tmp_path_factory.mktemp("harbor"))
    import importlib

    from fastapi.testclient import TestClient

    import bank_api.harbor as hb
    from bank_api.generator import generate
    hb = importlib.reload(hb)
    generate(hb.DATA_DIR, hb.PARAMS)          # 3,000 customers: enough treatment history for uplift to be feasible
    from lastmile.ingest.bank_client import BankClient
    with TestClient(hb.app) as tc:
        yield BankClient("http://testserver", 5000, client=tc)


@pytest.fixture(scope="module")
def profiles(harbor):
    from lastmile.ingest.onboarding import profile_frame
    return {role: profile_frame(pd.DataFrame(harbor.get(path, {"page": 1, "page_size": 300})["items"]))
            for role, path in HARBOR_PROPOSAL["feeds"].items()}


def test_probability_scores_become_the_same_pds_as_any_other_bank(cfg):
    """A 40.64% probability over 180 days is ~65% over 12 months: every bank ends in the same two numbers."""
    from lastmile.features.calibration import calibrate

    pc = cfg.institution.pd_calibration.model_copy(update={"input_type": "probability", "source_horizon_days": 180})
    out = calibrate(pd.Series([0.4064]), pc, 30)
    assert out["pd_12m"].iloc[0] == pytest.approx(1 - (1 - 0.4064) ** (365 / 180))
    assert out["pd_h"].iloc[0] == pytest.approx(1 - (1 - 0.4064) ** (30 / 180))
    assert out["label"].iloc[0] == "40.6% in 180d"


def test_profiles_never_carry_a_customers_values(profiles):
    """Names, phones and emails reach the LLM only as shapes; codes like product types are shown."""
    members = profiles["members"]["columns"]
    for col in ("given_name", "family_name", "mobile_no", "email_address", "cust_ref"):
        assert "values" not in members[col] and "shapes" in members[col] or members[col]["kind"] != "text"
    text = json.dumps(profiles)
    assert "Morgan" not in text and "@example.org" not in text
    assert set(profiles["accounts"]["columns"]["product_type"]["values"]) == {"AUTO", "CARD", "PERS"}


def test_the_true_mapping_passes_every_check(profiles):
    from lastmile.ingest.onboarding import check_proposal
    assert check_proposal(HARBOR_PROPOSAL, profiles) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda p: p["score"].update(source_horizon_days=365), "horizon"),
    (lambda p: p["field_map"]["accounts"].update(dpd="days_late"), "does not exist"),
    (lambda p: p["score"].update(input_type="band"), "grades"),
    (lambda p: p["field_map"]["accounts"].update(secured="hardship_count_12m"), "0/1"),
    (lambda p: p["field_map"]["accounts"].update(dpd="outstanding"), "expects integer"),
    (lambda p: p["field_map"]["members"].pop("first_name"), "first_name is required"),
    (lambda p: p.update(pii_fields=["last_name"]), "first_name"),
])
def test_checks_catch_what_a_wrong_brain_would_get_wrong(profiles, mutate, expect):
    from lastmile.ingest.onboarding import check_proposal

    wrong = copy.deepcopy(HARBOR_PROPOSAL)
    mutate(wrong)
    assert any(expect in p for p in check_proposal(wrong, profiles))


def test_onboarding_agent_decides_with_the_llm_and_corrects_itself_once(harbor, tmp_path):
    """First answer wrong, checks send the exact problems back, the rewrite fixes them; every call is recorded."""
    from lastmile.pipeline.run import onboard
    from lastmile.store import db, llm_calls
    from lastmile.store import onboarding as sessions

    class Brain:
        provider, model = "fake", "fake-brain"

        def generate_json(self, system, prompt):
            p = json.loads(prompt)
            if "api_paths" in p:
                assert any(x["path"] == "/v2/risk/pd-scores" for x in p["api_paths"])
                return {"endpoints": HARBOR_PROPOSAL["endpoints"], "feeds": HARBOR_PROPOSAL["feeds"], "reasons": {}}
            assert "Morgan" not in prompt                   # no customer values in what the brain sees
            answer = {"field_map": copy.deepcopy(HARBOR_PROPOSAL["field_map"]), "pii_fields": HARBOR_PROPOSAL["pii_fields"],
                      "score": {"input_type": "probability", "source_horizon_days": 180, "reasoning": "0-1 values"}}
            if "problems_found_by_checks" not in p:
                answer["score"]["source_horizon_days"] = 365
            return answer

    sid = sessions.new_session("http://testserver", "tester", tmp_path)
    onboard(sid, "http://testserver", data_dir=tmp_path, bank=harbor, llm=Brain(), llm_mode="fake")
    s = sessions.get(sid, tmp_path)
    assert s["status"] == "proposed", (s["error"], s["problems"])
    assert s["attempts"] == 2 and s["institution_id"] == "harbor_community_bank"
    assert "input_type: probability" in s["pack_yaml"] and "source_horizon_days: 180" in s["pack_yaml"]
    assert [c["purpose"] for c in llm_calls.for_run(sid, tmp_path)] == ["choose endpoints", "map fields and read the score",
                                                                      "fix the mapping"]
    with db.connect(tmp_path) as con:
        owners = {r[0] for r in con.execute("SELECT DISTINCT agent FROM trace_events WHERE run_id = ? AND tool IS NOT NULL", (sid,))}
    assert owners == {"Onboarding Agent"}


def test_onboarding_refuses_without_a_brain(harbor, tmp_path):
    from lastmile.pipeline.run import onboard
    from lastmile.store import onboarding as sessions

    sid = sessions.new_session("http://testserver", "tester", tmp_path)
    onboard(sid, "http://testserver", data_dir=tmp_path, bank=harbor, llm=None, llm_mode="template")
    s = sessions.get(sid, tmp_path)
    assert s["status"] == "failed" and "needs a language model" in s["error"]


def test_engine_runs_a_full_day_for_the_onboarded_bank(harbor, tmp_path, dirs, run, monkeypatch):
    """The pack built from the mapping drives a complete run on probability scores, and leaves other banks' runs alone."""
    import shutil

    import yaml

    from lastmile.config import resolve as resolve_mod
    from lastmile.config.resolve import load_institution
    from lastmile.ingest.onboarding import build_institution
    from lastmile.pipeline.run import run_blocking
    from lastmile.store import artifacts, db

    config = tmp_path / "config"
    shutil.copytree(resolve_mod.CONFIG_DIR, config, ignore=shutil.ignore_patterns(".history"))
    pack = build_institution(HARBOR_PROPOSAL, load_institution("riverbend_cu"), "Harbor Community Bank", "http://testserver")
    (config / "institutions" / "harbor_community_bank.yaml").write_text(yaml.safe_dump(pack, sort_keys=False))
    (config / "runs" / "harbor_community_bank.yaml").write_text(yaml.safe_dump(
        {"run": {"scenario": "collections_delinquency", "institution": "harbor_community_bank", "as_of": None}}))

    engine = tmp_path / "engine"
    shutil.copytree(dirs["engine"], engine)          # the Riverbend run is already waiting in this database
    monkeypatch.setattr(resolve_mod.resolve, "__defaults__", ("default", config, None))
    rid = run_blocking("harbor_community_bank", data_dir=engine, bank=harbor, llm=None, llm_mode="template (tests)")
    with db.connect(engine) as con:
        rows = {x["run_id"]: dict(x) for x in con.execute("SELECT run_id, status, error, institution FROM runs")}
    assert rows[rid]["status"] == "awaiting_approval", rows[rid]["error"]
    assert rows[rid]["institution"] == "harbor_community_bank"
    assert rows[run]["status"] == "awaiting_approval"      # another bank's worklist for the same day is not superseded
    port = artifacts.load_frame(rid, "portfolio", engine)
    assert port["risk_grade"].str.endswith("in 180d").all() and port["pd_12m"].between(0, 1).all()
