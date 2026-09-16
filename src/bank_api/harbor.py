"""Harbor Community Bank - a second, differently built bank for the onboarding demo.

Same kind of business as Riverbend, but nothing lines up: other endpoint paths, other column names, and its
risk model is a gradient-boosted classifier that sends a default probability over 180 days instead of a grade.
Last Mile has no configuration for this bank until the Onboarding Agent works one out.

Kept deliberately the same as Riverbend, and stated as the integration contract: the JSON envelope of a page
(items / total / has_more / as_of), the status and catalogue response keys, the code values (product, status,
treatment), and the payload of an approved action.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from bank_api.generator import GenParams, NotAdvanceable, advance_day, generate
from bank_api.main import ActionBatch

DATA_DIR = Path(os.environ.get("HARBOR_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "bank_harbor"))
DB_PATH = DATA_DIR / "bank.db"
PARAMS = GenParams(members=3000, seed=4242, institution="Harbor Community Bank", score_style="probability",
                   score_horizon_days=180)

# feed -> (table, order, pii, description, [(internal column, published column)])
FEEDS: dict[str, dict] = {
    "risk/pd-scores": {"table": "risk_scores", "order": "ACCT_NBR", "pii": False,
                       "description": "Daily probability of default from the credit risk model (xgb-collect), 180-day horizon.",
                       "columns": [("ACCT_NBR", "acct_ref"), ("PD_EST", "pd_180d"), ("SCORE_DT", "scored_on"),
                                   ("MDL_VER", "model_id"), ("PD_HORIZON_DAYS", "horizon_days")]},
    "lending/loans": {"table": "accounts", "order": "ACCT_NBR", "pii": False,
                      "description": "Loan and card book from the lending platform.",
                      "columns": [("ACCT_NBR", "acct_ref"), ("CIF_KEY", "cust_ref"), ("PROD_CD", "product_type"),
                                  ("SECURED_IND", "is_secured"), ("ORIG_AMT", "original_amount"),
                                  ("INT_RATE", "interest_rate"), ("OPEN_DT", "opened_on"), ("CURR_BAL", "outstanding"),
                                  ("DPD_CNT", "days_overdue"), ("MIN_PMT_AMT", "min_due"), ("ACCT_STATUS", "loan_state"),
                                  ("LITIGATION_IND", "in_litigation"), ("FRAUD_HOLD_IND", "fraud_flag"),
                                  ("HARDSHIP_ACTIVE_IND", "hardship_now"), ("HARDSHIP_PLANS_12M", "hardship_count_12m"),
                                  ("PTP_KEPT_RATE_12M", "promise_kept_ratio"), ("COMPLAINTS_12M", "complaints_last_year"),
                                  ("PMTS_MISSED_12M", "missed_payments_12m"), ("MOS_SINCE_LAST_PMT", "months_since_payment")]},
    "crm/customers": {"table": "members", "order": "CIF_KEY", "pii": True,
                      "description": "Customer master. Contains personal data.",
                      "columns": [("CIF_KEY", "cust_ref"), ("FIRST_NM", "given_name"), ("LAST_NM", "family_name"),
                                  ("PHONE_NBR", "mobile_no"), ("EMAIL_ADDR", "email_address"), ("TZ_CD", "timezone"),
                                  ("LANG_CD", "preferred_language"), ("MEMBER_SINCE_DT", "customer_since"),
                                  ("TENURE_MOS", "months_as_customer"), ("DIRECT_DEPOSIT_IND", "salary_credited"),
                                  ("DECEASED_IND", "deceased_flag"), ("BANKRUPTCY_IND", "bankruptcy_flag"),
                                  ("CEASE_DESIST_IND", "cease_contact_flag")]},
    "crm/contact-permissions": {"table": "consents", "order": "CIF_KEY", "pii": False,
                                "description": "What the customer allows and recent contact counts.",
                                "columns": [("CIF_KEY", "cust_ref"), ("PHONE_CONSENT_IND", "may_call"),
                                            ("SMS_CONSENT_IND", "may_text"), ("EMAIL_CONSENT_IND", "may_email"),
                                            ("DNC_IND", "do_not_call"), ("CONTACTS_7D", "contacts_last_7d"),
                                            ("LAST_RPC_DT", "last_reached_on")]},
    "collections/worklog": {"table": "collections_queue_history", "order": "QUEUE_DT, ACCT_NBR", "pii": False,
                            "description": "Each day's collections work per account: state at the time and the treatment given.",
                            "columns": [("QUEUE_DT", "worked_on"), ("ACCT_NBR", "acct_ref"), ("CIF_KEY", "cust_ref"),
                                        ("DPD_CNT", "days_overdue_then"), ("CURR_BAL", "balance_then"),
                                        ("PD_EST", "pd_180d_then"), ("PMTS_MISSED_12M", "missed_payments_then"),
                                        ("MOS_SINCE_LAST_PMT", "months_since_payment_then"), ("ACTION_CD", "treatment"),
                                        ("RPC_IND", "reached_customer")]},
    "collections/results": {"table": "outcomes", "order": "QUEUE_DT, ACCT_NBR", "pii": False,
                            "description": "Whether each worked account recovered within 30 days (published once 30 days pass).",
                            "columns": [("ACCT_NBR", "acct_ref"), ("QUEUE_DT", "worked_on"),
                                        ("CURED_30D_IND", "recovered_within_30d"), ("AMT_PAID_30D", "paid_within_30d")]},
}


def _con() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, "Harbor data not initialised. Run `make seed`.")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _meta(con: sqlite3.Connection) -> dict:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not DB_PATH.exists():
        generate(DATA_DIR, PARAMS)
    yield


app = FastAPI(title="Harbor Community Bank - Core Data Services", version="2.0.0", lifespan=lifespan)


@app.get("/v2/status")
def status():
    with _con() as con:
        meta = _meta(con)
    return {"status": "ok", "institution": meta.get("institution"), "as_of": meta.get("as_of")}


@app.get("/v2/catalogue")
def catalogue():
    with _con() as con:
        meta = _meta(con)
        out = [{"feed": name.replace("/", "_"), "path": f"/v2/{name}", "row_count": con.execute(f"SELECT COUNT(*) FROM {f['table']}").fetchone()[0],
                "contains_pii": f["pii"], "description": f["description"]} for name, f in FEEDS.items()]
    return {"as_of": meta.get("as_of"), "feeds": out}


def _endpoint(name: str):
    f = FEEDS[name]
    select = ", ".join(f'"{src}" AS "{pub}"' for src, pub in f["columns"])

    def endpoint(page: int = Query(1, ge=1), page_size: int = Query(5000, ge=1, le=20000)):
        with _con() as con:
            meta = _meta(con)
            total = con.execute(f"SELECT COUNT(*) FROM {f['table']}").fetchone()[0]
            rows = con.execute(f"SELECT {select} FROM {f['table']} ORDER BY {f['order']} LIMIT ? OFFSET ?",
                               (page_size, (page - 1) * page_size)).fetchall()
        return {"feed": name, "as_of": meta.get("as_of"), "page": page, "page_size": page_size, "total": total,
                "has_more": page * page_size < total, "items": [dict(r) for r in rows]}
    endpoint.__name__ = "get_" + name.replace("/", "_").replace("-", "_")
    return endpoint


for _name in FEEDS:
    app.get(f"/v2/{_name}", summary=FEEDS[_name]["description"])(_endpoint(_name))


@app.post("/v2/collections/instructions", summary="Receive approved collection actions for execution.")
def receive_instructions(batch: ActionBatch):
    now = datetime.now(UTC).isoformat()
    with _con() as con:
        known = {r[0] for r in con.execute("SELECT ACCT_NBR FROM accounts WHERE ACCT_STATUS = 'DELINQUENT'")}
        bad = [a.ACCT_NBR for a in batch.actions if a.ACCT_NBR not in known]
        if bad:
            raise HTTPException(422, f"Unknown or non-delinquent accounts: {bad[:5]}")
        ids = [con.execute("INSERT INTO collection_actions (ACCT_NBR, ACTION_CD, CHANNEL, SCRIPT_TXT, APPROVED_BY, SOURCE_RUN_ID,"
                           " RECEIVED_AT) VALUES (?,?,?,?,?,?,?)",
                           (a.ACCT_NBR, a.ACTION_CD, a.CHANNEL, a.SCRIPT_TXT, a.APPROVED_BY, a.SOURCE_RUN_ID, now)).lastrowid
               for a in batch.actions]
    return {"accepted": len(ids), "action_ids": ids, "received_at": now}


@app.post("/v2/sim/close-business-day", tags=["simulation"], summary="SIMULATION ONLY - close today and open tomorrow.")
def close_business_day():
    try:
        return advance_day(DATA_DIR)
    except NotAdvanceable as e:
        raise HTTPException(409, str(e)) from e
