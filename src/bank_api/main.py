"""Riverbend Credit Union - Collections Data API.

A deliberately ordinary bank integration surface: paged JSON feeds, the bank's own column
names, and one write endpoint that receives approved collection actions for execution.
The decision engine consumes this over HTTP and has no other access to bank data.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from bank_api.generator import GenParams, NotAdvanceable, advance_day, generate

DATA_DIR = Path(os.environ.get("BANK_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "bank"))
DB_PATH = DATA_DIR / "bank.db"

FEEDS: dict[str, dict] = {
    "risk-scores": {"table": "risk_scores", "key": "ACCT_NBR", "order": "ACCT_NBR",
                    "description": "Nightly delinquency risk grades from the credit risk model platform."},
    "accounts": {"table": "accounts", "key": "ACCT_NBR", "order": "ACCT_NBR",
                 "description": "Loan and card servicing records from core banking."},
    "members": {"table": "members", "key": "CIF_KEY", "order": "CIF_KEY", "pii": True,
                "description": "Member identity and suppression flags from the CIF. Contains PII."},
    "consents": {"table": "consents", "key": "CIF_KEY", "order": "CIF_KEY",
                 "description": "Contact consents and recent contact counts from the CRM consent store."},
    "collections-queue-history": {"table": "collections_queue_history", "key": "ACCT_NBR,QUEUE_DT",
                                  "order": "QUEUE_DT, ACCT_NBR",
                                  "description": "Daily collections queue with the action taken and account state at the time."},
    "outcomes": {"table": "outcomes", "key": "ACCT_NBR,QUEUE_DT", "order": "QUEUE_DT, ACCT_NBR",
                 "description": "30-day cure outcomes for queue entries whose outcome window has closed."},
}


def _con() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, "Bank data not initialised. Run `make seed`.")
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _meta(con: sqlite3.Connection) -> dict:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not DB_PATH.exists():
        generate(DATA_DIR, GenParams())
    yield


app = FastAPI(title="Riverbend Credit Union - Collections Data API", version="1.0.0", lifespan=lifespan)


@app.get("/api/v1/health")
def health():
    with _con() as con:
        meta = _meta(con)
    return {"status": "ok", "institution": meta.get("institution"), "as_of": meta.get("as_of")}


@app.get("/api/v1/feeds")
def feeds():
    with _con() as con:
        meta = _meta(con)
        out = []
        for name, f in FEEDS.items():
            n = con.execute(f"SELECT COUNT(*) FROM {f['table']}").fetchone()[0]
            out.append({"feed": name, "path": f"/api/v1/{name}", "primary_key": f["key"], "row_count": n,
                        "contains_pii": bool(f.get("pii")), "description": f["description"]})
    return {"as_of": meta.get("as_of"), "feeds": out}


def _page(feed: str, page: int, page_size: int):
    f = FEEDS[feed]
    with _con() as con:
        meta = _meta(con)
        total = con.execute(f"SELECT COUNT(*) FROM {f['table']}").fetchone()[0]
        rows = con.execute(f"SELECT * FROM {f['table']} ORDER BY {f['order']} LIMIT ? OFFSET ?",
                           (page_size, (page - 1) * page_size)).fetchall()
    return {"feed": feed, "as_of": meta.get("as_of"), "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total, "items": [dict(r) for r in rows]}


def _feed_endpoint(feed: str):
    def endpoint(page: int = Query(1, ge=1), page_size: int = Query(5000, ge=1, le=20000)):
        return _page(feed, page, page_size)
    endpoint.__name__ = f"get_{feed.replace('-', '_')}"
    return endpoint


for _feed in FEEDS:
    app.get(f"/api/v1/{_feed}", summary=FEEDS[_feed]["description"])(_feed_endpoint(_feed))


class CollectionAction(BaseModel):
    ACCT_NBR: str
    ACTION_CD: str = Field(pattern="^(SMS|CALL|PLAN|HARDSHIP)$")
    CHANNEL: str
    SCRIPT_TXT: str
    APPROVED_BY: str
    SOURCE_RUN_ID: str


class ActionBatch(BaseModel):
    actions: list[CollectionAction]


@app.post("/api/v1/collections/actions")
def receive_actions(batch: ActionBatch):
    now = datetime.now(UTC).isoformat()
    with _con() as con:
        known = {r[0] for r in con.execute("SELECT ACCT_NBR FROM accounts WHERE ACCT_STATUS = 'DELINQUENT'")}
        bad = [a.ACCT_NBR for a in batch.actions if a.ACCT_NBR not in known]
        if bad:
            raise HTTPException(422, f"Unknown or non-delinquent accounts: {bad[:5]}")
        ids = []
        for a in batch.actions:
            cur = con.execute(
                "INSERT INTO collection_actions (ACCT_NBR, ACTION_CD, CHANNEL, SCRIPT_TXT, APPROVED_BY, SOURCE_RUN_ID, RECEIVED_AT)"
                " VALUES (?,?,?,?,?,?,?)",
                (a.ACCT_NBR, a.ACTION_CD, a.CHANNEL, a.SCRIPT_TXT, a.APPROVED_BY, a.SOURCE_RUN_ID, now))
            ids.append(cur.lastrowid)
    return {"accepted": len(ids), "action_ids": ids, "received_at": now}


@app.get("/api/v1/collections/actions")
def list_actions(limit: int = Query(200, le=5000), source_run_id: str | None = None):
    """Actions received for execution, with how each one went once its business day has closed."""
    sql, params = "SELECT * FROM collection_actions", []
    if source_run_id:
        sql, params = sql + " WHERE SOURCE_RUN_ID = ?", [source_run_id]
    with _con() as con:
        rows = con.execute(sql + " ORDER BY ACTION_ID DESC LIMIT ?", (*params, limit)).fetchall()
        items = [dict(r) for r in rows]
        # 30-day cure outcomes are published only once their window has closed - never earlier
        for it in items:
            it.setdefault("EXECUTED_DT", None)
            out = con.execute("SELECT CURED_30D_IND, AMT_PAID_30D FROM outcomes WHERE ACCT_NBR = ? AND QUEUE_DT = ?",
                              (it["ACCT_NBR"], it["EXECUTED_DT"])).fetchone() if it.get("EXECUTED_DT") else None
            it["CURED_30D_IND"] = out["CURED_30D_IND"] if out else None
            it["AMT_PAID_30D"] = out["AMT_PAID_30D"] if out else None
    return {"items": items}


@app.post("/api/v1/simulation/advance-day", tags=["simulation"])
def simulation_advance_day():
    """SIMULATION ONLY - a real bank's business day closes on its own.

    Executes the actions received today, runs the rest of the book through the legacy process,
    publishes newly matured outcomes and moves the as-of date forward by one day.
    """
    try:
        return advance_day(DATA_DIR)
    except NotAdvanceable as e:
        raise HTTPException(409, str(e)) from e


@app.get("/api/v1/simulation/day-log", tags=["simulation"])
def simulation_day_log():
    with _con() as con:
        has = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'day_log'").fetchone()
        rows = con.execute("SELECT * FROM day_log ORDER BY BUSINESS_DT DESC").fetchall() if has else []
    return {"items": [dict(r) for r in rows]}
