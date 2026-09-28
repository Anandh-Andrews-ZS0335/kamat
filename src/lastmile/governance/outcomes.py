"""What the collection agent found when they worked an account.

The first half of the feedback loop, and the only part of it that arrives the same day. Append-only
like approvals: correcting a comment writes a new row rather than overwriting one, so what was said
at the time survives. The bank's own confirmation - reached, paid, cured - still arrives separately
and matures at 30 days; nothing here replaces it.
"""

from __future__ import annotations

from lastmile.governance import audit
from lastmile.store import db


class OutcomeError(ValueError):
    pass


# Call dispositions. Fixed operational vocabulary rather than institution policy, so it lives in
# code; move it to the institution pack if a bank needs its own list.
DISPOSITIONS = {
    "REACHED": "Spoke to the customer",
    "PROMISE": "Promise to pay",
    "ALREADY_PAID": "Already paid",
    "CALLBACK": "Asked to be called back",
    "NO_ANSWER": "No answer",
    "VOICEMAIL": "Left a message",
    "WRONG_NUMBER": "Wrong number",
    "REFUSED": "Refused or disputed",
    "HARDSHIP": "Reported hardship",
    "NOT_WORKED": "Could not get to it today",
}
NEEDS_PROMISE = "PROMISE"


def latest(run_id: str, data_dir=None) -> dict[str, dict]:
    """The current outcome per account: the most recent row wins, earlier ones stay for the record."""
    with db.connect(data_dir) as con:
        rows = db.rows(con, """SELECT o.*, (SELECT COUNT(*) FROM attempt_outcomes e
                                            WHERE e.run_id = o.run_id AND e.account_token = o.account_token) revisions
                               FROM attempt_outcomes o JOIN (
                                 SELECT account_token, MAX(id) mid FROM attempt_outcomes WHERE run_id = ? GROUP BY account_token
                               ) m ON o.id = m.mid""", (run_id,))
    return {r["account_token"]: r for r in rows}


def history(run_id: str, account_token: str, data_dir=None) -> list[dict]:
    with db.connect(data_dir) as con:
        return db.rows(con, "SELECT * FROM attempt_outcomes WHERE run_id = ? AND account_token = ? ORDER BY id",
                       (run_id, account_token), )


def record(run_id: str, account_token: str, collector_id: str, disposition: str, comment: str | None,
           promise_date: str | None, promise_amount: float | None, recorded_by: str, data_dir=None,
           spent_minutes: int | None = None) -> dict:
    if disposition not in DISPOSITIONS:
        raise OutcomeError(f"unknown disposition {disposition!r}; expected one of {sorted(DISPOSITIONS)}")
    if disposition == NEEDS_PROMISE and not promise_date:
        raise OutcomeError("a promise to pay needs the date it was promised for")
    if disposition != NEEDS_PROMISE and (promise_date or promise_amount):
        raise OutcomeError("a promise date or amount only belongs on a promise to pay")
    if spent_minutes is not None and not 0 <= spent_minutes <= 600:
        raise OutcomeError("time spent must be between 0 and 600 minutes")
    with db.connect(data_dir) as con:
        cur = con.execute(
            "INSERT INTO attempt_outcomes (run_id, account_token, collector_id, disposition, comment,"
            " promise_date, promise_amount, spent_minutes, recorded_by, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, account_token, collector_id, disposition, (comment or "").strip() or None,
             promise_date, promise_amount, spent_minutes, recorded_by, db.now()))
        row_id = cur.lastrowid
    audit.append(run_id, "attempt.recorded", recorded_by,
                 {"account_token": account_token, "collector_id": collector_id, "disposition": disposition,
                  "has_comment": bool(comment), "row_id": row_id}, data_dir=data_dir)
    return {"recorded": True, "id": row_id, "disposition": disposition}
