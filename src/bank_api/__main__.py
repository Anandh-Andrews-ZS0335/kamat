"""`python -m bank_api seed [--members N --days D]` regenerates the synthetic credit union."""

import argparse
import json

from bank_api.generator import GenParams, generate
from bank_api.main import DATA_DIR


def main() -> None:
    p = argparse.ArgumentParser(prog="bank_api")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--members", type=int, default=4000)
    s.add_argument("--days", type=int, default=120)
    s.add_argument("--seed", type=int, default=20260914)
    s.add_argument("--profile", choices=["riverbend", "harbor"], default="riverbend",
                   help="riverbend: grades on /api/v1; harbor: 180-day probabilities, different paths and columns")
    args = p.parse_args()
    if args.cmd == "seed" and args.profile == "harbor":
        from bank_api import harbor
        print(json.dumps(generate(harbor.DATA_DIR, harbor.PARAMS), indent=2))
    elif args.cmd == "seed":
        print(json.dumps(generate(DATA_DIR, GenParams(members=args.members, history_days=args.days, seed=args.seed)),
                         indent=2))


if __name__ == "__main__":
    main()
