"""Onboarding sessions: one row per attempt to connect a new bank."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

from lastmile.store import db

JSON = ("plan", "proposal", "profiles", "problems")


def new_session(base_url: str, created_by: str, data_dir: Path | None = None) -> str:
    db.init(data_dir)
    sid = f"onb_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(2)}"
    with db.connect(data_dir) as con:
        con.execute("INSERT INTO onboarding_sessions (session_id, base_url, status, created_by, created_at) VALUES (?,?,?,?,?)",
                    (sid, base_url, "running", created_by, db.now()))
    return sid


def update(session_id: str, data_dir: Path | None = None, **fields) -> None:
    for k in JSON:
        if k in fields:
            fields[f"{k}_json"] = db.dumps(fields.pop(k))
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect(data_dir) as con:
        con.execute(f"UPDATE onboarding_sessions SET {cols} WHERE session_id = ?", (*fields.values(), session_id))


def get(session_id: str, data_dir: Path | None = None) -> dict | None:
    with db.connect(data_dir) as con:
        r = con.execute("SELECT * FROM onboarding_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    for k in JSON:
        out[k] = db.loads(out.pop(f"{k}_json"))
    return out


def recent(limit: int = 20, data_dir: Path | None = None) -> list[dict]:
    with db.connect(data_dir) as con:
        return db.rows(con, "SELECT session_id, base_url, status, institution_id, institution_name, attempts, created_by,"
                            " created_at, finished_at, decided_by, test_run_id FROM onboarding_sessions"
                            " ORDER BY created_at DESC LIMIT ?", (limit,))
