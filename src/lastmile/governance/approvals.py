"""The blocking human gate. Nothing is released without an approval row; overrides require a reason."""

from __future__ import annotations

from lastmile.governance import audit
from lastmile.store import db


class ApprovalError(ValueError):
    pass


def latest(run_id: str, data_dir=None) -> dict[str, dict]:
    with db.connect(data_dir) as con:
        rs = db.rows(con, """SELECT a.* FROM approvals a JOIN (
                               SELECT account_token, MAX(id) mid FROM approvals WHERE run_id = ? GROUP BY account_token
                             ) m ON a.id = m.mid""", (run_id,))
    return {r["account_token"]: r for r in rs}


def record(run_id: str, account_token: str, decision: str, approver_id: str, final_action: str | None,
           reason_code: str | None, reason_text: str | None, allowed_actions: set[str], reason_codes: list[str],
           recommended_action: str, data_dir=None) -> dict:
    if decision not in ("approved", "edited", "rejected"):
        raise ApprovalError("decision must be approved, edited or rejected")
    if decision != "approved" and not reason_code:
        raise ApprovalError("a reason code is required to edit or reject a recommendation")
    if reason_code and reason_code not in reason_codes:
        raise ApprovalError(f"unknown reason code {reason_code}")
    if decision == "approved":
        final_action = recommended_action
    if decision == "edited":
        if not final_action or final_action not in allowed_actions:
            raise ApprovalError(f"{final_action} is not an allowed action for this account: {sorted(allowed_actions)}")
        if final_action == recommended_action:
            raise ApprovalError("edited action is the same as the recommendation; approve instead")
    if decision == "rejected":
        final_action = None
    with db.connect(data_dir) as con:
        cur = con.execute("INSERT INTO approvals (run_id, account_token, decision, final_action, reason_code, reason_text,"
                          " approver_id, decided_at) VALUES (?,?,?,?,?,?,?,?)",
                          (run_id, account_token, decision, final_action, reason_code, reason_text, approver_id, db.now()))
        row_id = cur.lastrowid
    audit.append(run_id, f"approval.{decision}", approver_id,
                 {"account_token": account_token, "recommended": recommended_action, "final_action": final_action,
                  "reason_code": reason_code, "reason_text": reason_text}, data_dir)
    return {"id": row_id, "decision": decision, "final_action": final_action}


def override_signals(run_id: str, data_dir=None) -> list[dict]:
    """Rejections and edits are labelled training data: where a domain expert disagreed, and why."""
    with db.connect(data_dir) as con:
        return db.rows(con, """SELECT reason_code, decision, COUNT(*) n FROM approvals
                               WHERE run_id = ? AND decision != 'approved' GROUP BY 1, 2 ORDER BY n DESC""", (run_id,))
