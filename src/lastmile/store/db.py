"""SQLite: the transactional record. Runs, stage state, agent trace, recommendations, approvals, audit."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from lastmile.config.settings import DATA_DIR

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, as_of_date TEXT, scenario TEXT, institution TEXT, config_hash TEXT,
  model_run_id TEXT, llm_mode TEXT,
  status TEXT CHECK (status IN ('running','failed','aborted','awaiting_approval','released','superseded')),
  error TEXT, started_at TEXT NOT NULL, finished_at TEXT, summary_json TEXT,
  roster_json TEXT, parent_run_id TEXT, superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS stages (
  run_id TEXT, stage TEXT, ord INTEGER, title TEXT, agents_json TEXT, status TEXT,
  started_at TEXT, finished_at TEXT, ms INTEGER, metrics_json TEXT, artifacts_json TEXT, notes_json TEXT,
  PRIMARY KEY (run_id, stage)
);

CREATE TABLE IF NOT EXISTS trace_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, stage TEXT, ts TEXT, kind TEXT,
  agent TEXT, target TEXT, tool TEXT, module TEXT, status TEXT, ms INTEGER,
  input_json TEXT, output_json TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events(run_id, seq);

CREATE TABLE IF NOT EXISTS recommendations (
  run_id TEXT, account_token TEXT, member_token TEXT, rank INTEGER, action TEXT, segment TEXT,
  facts_json TEXT NOT NULL, alternatives_json TEXT, escalations_json TEXT,
  rationale_tpl TEXT, script_tpl TEXT, template_source TEXT, collector_id TEXT, queue_position INTEGER,
  PRIMARY KEY (run_id, account_token)
);

-- Every LLM call in full: what was asked, what came back, how long it took. Append-only.
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, stage TEXT, agent TEXT, tool TEXT, purpose TEXT,
  provider TEXT, model TEXT, endpoint TEXT, status TEXT CHECK (status IN ('ok','error')), http_status INTEGER,
  latency_ms INTEGER, system_prompt TEXT, user_prompt TEXT, request_json TEXT, response_text TEXT, parsed_json TEXT,
  error TEXT, prompt_tokens INTEGER, response_tokens INTEGER, thinking_tokens INTEGER, total_tokens INTEGER, finish_reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id, id);
CREATE TRIGGER IF NOT EXISTS llm_calls_no_update BEFORE UPDATE ON llm_calls
  BEGIN SELECT RAISE(ABORT, 'llm_calls is append-only'); END;

-- Today's team, as entered by the collections manager. Append-only history; the latest row per date wins.
CREATE TABLE IF NOT EXISTS rosters (
  id INTEGER PRIMARY KEY AUTOINCREMENT, roster_date TEXT NOT NULL, collectors_json TEXT NOT NULL,
  saved_by TEXT NOT NULL, saved_at TEXT NOT NULL, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_rosters_date ON rosters(roster_date, id);

-- A closed business day: the frozen daily report and what the bank said when the day moved on.
CREATE TABLE IF NOT EXISTS day_closures (
  business_date TEXT PRIMARY KEY, run_id TEXT, closed_by TEXT NOT NULL, closed_at TEXT NOT NULL,
  report_json TEXT NOT NULL, bank_response_json TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, account_token TEXT,
  decision TEXT CHECK (decision IN ('approved','edited','rejected')),
  final_action TEXT, reason_code TEXT, reason_text TEXT,
  approver_id TEXT NOT NULL, decided_at TEXT NOT NULL,
  CHECK (decision = 'approved' OR reason_code IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event_type TEXT, actor TEXT,
  payload_json TEXT, prev_hash TEXT, hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
  BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
  BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, account_token TEXT, action TEXT,
  bank_action_id INTEGER, released_by TEXT, released_at TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def db_path(data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / "lastmile.db"


def _default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def dumps(obj) -> str:
    return json.dumps(obj, default=_default, allow_nan=False) if obj is not None else None


def loads(s: str | None):
    return json.loads(s) if s else None


@contextmanager
def connect(data_dir: Path | None = None):
    path = db_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
    finally:
        con.close()


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _migrate(con: sqlite3.Connection) -> None:
    """Bring a database created by an earlier version up to date without losing its runs."""
    runs_sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'runs'").fetchone()
    if runs_sql and "superseded" not in runs_sql["sql"]:
        # SQLite cannot alter a CHECK constraint: rebuild the table with the new status list.
        con.executescript("""
            BEGIN;
            ALTER TABLE runs RENAME TO runs_old;
            CREATE TABLE runs (
              run_id TEXT PRIMARY KEY, as_of_date TEXT, scenario TEXT, institution TEXT, config_hash TEXT,
              model_run_id TEXT, llm_mode TEXT,
              status TEXT CHECK (status IN ('running','failed','aborted','awaiting_approval','released','superseded')),
              error TEXT, started_at TEXT NOT NULL, finished_at TEXT, summary_json TEXT,
              roster_json TEXT, parent_run_id TEXT, superseded_by TEXT);
            INSERT INTO runs (run_id, as_of_date, scenario, institution, config_hash, model_run_id, llm_mode, status,
                              error, started_at, finished_at, summary_json)
              SELECT run_id, as_of_date, scenario, institution, config_hash, model_run_id, llm_mode, status,
                     error, started_at, finished_at, summary_json FROM runs_old;
            DROP TABLE runs_old;
            COMMIT;""")
    llm = _columns(con, "llm_calls")
    if llm and "thinking_tokens" not in llm:
        con.execute("ALTER TABLE llm_calls ADD COLUMN thinking_tokens INTEGER")
    rec = _columns(con, "recommendations")
    for name, typ in [("collector_id", "TEXT"), ("queue_position", "INTEGER")]:
        if rec and name not in rec:
            con.execute(f"ALTER TABLE recommendations ADD COLUMN {name} {typ}")


def init(data_dir: Path | None = None) -> None:
    with connect(data_dir) as con:
        _migrate(con)
        con.executescript(SCHEMA)


def rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params).fetchall()]
