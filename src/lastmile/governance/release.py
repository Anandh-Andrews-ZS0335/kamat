"""Release approved actions to the bank. Identity is rejoined here and only here."""

from __future__ import annotations

import pandas as pd

from lastmile.governance import audit
from lastmile.ingest.bank_client import BankClient
from lastmile.store import db


def release(run_id: str, approved: list[dict], identity: pd.DataFrame, client: BankClient, endpoint: str,
            channel_of: dict[str, str], released_by: str, data_dir=None) -> dict:
    with db.connect(data_dir) as con:
        already = {r["account_token"] for r in db.rows(con, "SELECT account_token FROM releases WHERE run_id = ?", (run_id,))}
    todo = [a for a in approved if a["account_token"] not in already and a["final_action"]]
    if not todo:
        return {"released": 0, "skipped_already_released": len(already), "bank_response": None}
    ids = identity.set_index("account_token")["account_id"]
    payload = {"actions": [{"ACCT_NBR": str(ids[a["account_token"]]), "ACTION_CD": a["final_action"],
                            "CHANNEL": channel_of[a["final_action"]], "SCRIPT_TXT": a["script_text"],
                            "APPROVED_BY": a["approver_id"], "SOURCE_RUN_ID": run_id} for a in todo]}
    resp = client.post(endpoint, payload)
    with db.connect(data_dir) as con:
        # strict: if the bank returns a different number of ids, fail rather than misattribute actions
        for a, bank_id in zip(todo, resp["action_ids"], strict=True):
            con.execute("INSERT INTO releases (run_id, account_token, action, bank_action_id, released_by, released_at)"
                        " VALUES (?,?,?,?,?,?)", (run_id, a["account_token"], a["final_action"], bank_id, released_by, db.now()))
        con.execute("UPDATE runs SET status = 'released' WHERE run_id = ?", (run_id,))
    audit.append(run_id, "release.sent_to_bank", released_by,
                 {"count": len(todo), "bank_action_ids": resp["action_ids"], "endpoint": endpoint})
    return {"released": len(todo), "skipped_already_released": len(already), "bank_response": resp}
