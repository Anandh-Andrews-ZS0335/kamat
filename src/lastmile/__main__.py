"""`python -m lastmile run` executes one full run in the foreground and prints the outcome."""

import argparse
import json

from lastmile.pipeline.run import run_blocking
from lastmile.store import db


def main() -> None:
    p = argparse.ArgumentParser(prog="lastmile")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", default="default")
    args = p.parse_args()
    if args.cmd == "run":
        run_id = run_blocking(args.config)
        with db.connect() as con:
            run = dict(con.execute("SELECT run_id, status, error, as_of_date, llm_mode, summary_json FROM runs WHERE run_id=?",
                                   (run_id,)).fetchone())
            stages = db.rows(con, "SELECT stage, status, ms FROM stages WHERE run_id=? ORDER BY ord", (run_id,))
        print(json.dumps({"run": run, "stages": stages}, indent=2))


if __name__ == "__main__":
    main()
