"""One small synthetic bank and one full agent run, shared by the tests. Everything lives in temp dirs."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)


@pytest.fixture(scope="session")
def dirs(tmp_path_factory) -> dict[str, Path]:
    bank = tmp_path_factory.mktemp("bank")
    engine = tmp_path_factory.mktemp("engine")
    os.environ["BANK_DATA_DIR"] = str(bank)  # must be set before bank_api.main is imported
    return {"bank": bank, "engine": engine}


@pytest.fixture(scope="session")
def bank_client(dirs):
    from bank_api.generator import GenParams, generate

    generate(dirs["bank"], GenParams(members=2500, history_days=120, seed=11))
    from fastapi.testclient import TestClient

    import bank_api.main as bank_main
    from lastmile.ingest.bank_client import BankClient

    with TestClient(bank_main.app) as tc:
        yield BankClient("http://testserver", 5000, client=tc)


@pytest.fixture(scope="session")
def run(dirs, bank_client) -> str:
    from lastmile.pipeline.run import run_blocking
    from lastmile.store import db

    run_id = run_blocking("default", data_dir=dirs["engine"], bank=bank_client, llm=None, llm_mode="template (tests)")
    with db.connect(dirs["engine"]) as con:
        row = dict(con.execute("SELECT status, error FROM runs WHERE run_id = ?", (run_id,)).fetchone())
    assert row["status"] == "awaiting_approval", row["error"]
    return run_id


@pytest.fixture(scope="session")
def cfg():
    from lastmile.config.resolve import resolve

    return resolve()
