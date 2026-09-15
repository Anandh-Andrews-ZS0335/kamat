"""Tamper-evident audit: SQL triggers refuse edits, and every row hashes the previous one."""

from __future__ import annotations

import hashlib

from lastmile.store import db


def append(run_id: str | None, event_type: str, actor: str, payload: dict, data_dir=None) -> str:
    with db.connect(data_dir) as con:
        con.execute("BEGIN IMMEDIATE")
        prev = con.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev["hash"] if prev else "GENESIS"
        body = db.dumps(payload)
        created = db.now()
        h = hashlib.sha256(f"{prev_hash}|{run_id}|{event_type}|{actor}|{body}|{created}".encode()).hexdigest()
        con.execute("INSERT INTO audit_events (run_id, event_type, actor, payload_json, prev_hash, hash, created_at)"
                    " VALUES (?,?,?,?,?,?,?)", (run_id, event_type, actor, body, prev_hash, h, created))
        con.execute("COMMIT")
    return h


def verify_chain(data_dir=None) -> dict:
    with db.connect(data_dir) as con:
        events = db.rows(con, "SELECT * FROM audit_events ORDER BY seq")
    prev = "GENESIS"
    for e in events:
        expected = hashlib.sha256(
            f"{prev}|{e['run_id']}|{e['event_type']}|{e['actor']}|{e['payload_json']}|{e['created_at']}".encode()).hexdigest()
        if e["prev_hash"] != prev or e["hash"] != expected:
            return {"valid": False, "events": len(events), "broken_at_seq": e["seq"]}
        prev = e["hash"]
    return {"valid": True, "events": len(events), "head": prev}


def events(run_id: str, data_dir=None) -> list[dict]:
    with db.connect(data_dir) as con:
        return db.rows(con, "SELECT * FROM audit_events WHERE run_id = ? ORDER BY seq", (run_id,))
