"""Operate the simulated bank for N business days, the way a manager would, so 30-day outcomes exist to measure.

Each day, through the engine's own API: start today's run, approve every non-escalated recommendation, release,
close the day (the simulated bank executes the actions and moves to tomorrow). Uses deterministic templates for
explanations unless --llm is given.

    uv run python scripts/simulate_days.py --days 35 --yes

It writes to the same engine and bank data as `make up` (LASTMILE_DATA_DIR / BANK_DATA_DIR if set). Only for the
simulated bank: a real bank's business day is not closed by software.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--llm", action="store_true", help="use the configured LLM for explanations (slower)")
    ap.add_argument("--yes", action="store_true", help="confirm writing runs, approvals and closed days")
    args = ap.parse_args()
    if not args.yes:
        sys.exit("This operates the simulated bank and writes runs, approvals, releases and closed days. Re-run with --yes.")
    if not args.llm:
        os.environ["LLM_PROVIDER"] = "none"

    from lastmile.api import auth
    user = auth.User("simulator", "admin", auth.hash_password(os.urandom(16).hex(), 1000))
    os.environ["LASTMILE_USERS"] = f"{user.username}:{user.role}:{user.pw_hash}"   # this process only

    from fastapi.testclient import TestClient

    from lastmile.api.app import app
    client = TestClient(app)
    client.cookies.set(auth.COOKIE, auth.make_session(user))

    def call(method: str, path: str, **kw):
        r = client.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    for n in range(1, args.days + 1):
        day = call("GET", "/api/day")
        if not day.get("bank_ok"):
            sys.exit(f"bank not reachable: {day.get('bank_error')}")
        t0 = time.time()
        run = day.get("run")
        if not run or run["status"] not in ("awaiting_approval", "released"):
            run_id = call("POST", "/api/runs")["run_id"]
            while (status := call("GET", f"/api/runs/{run_id}")["run"]["status"]) == "running":
                time.sleep(1)
            if status != "awaiting_approval":
                sys.exit(f"{day['business_date']}: run {run_id} ended {status}")
        else:
            run_id = run["run_id"]
        approved = call("POST", f"/api/runs/{run_id}/approve-bulk")["approved"]
        try:
            released = call("POST", f"/api/runs/{run_id}/release")["released"]
        except RuntimeError:
            released = 0
        closed = call("POST", "/api/day/close", json={"confirm_unreleased": True, "note": "simulated business day"})
        print(f"day {n:>2}/{args.days}  {day['business_date']}  run {run_id}  approved {approved:>3}  released {released:>3}"
              f"  -> bank now {closed.get('next_business_date')}  ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
