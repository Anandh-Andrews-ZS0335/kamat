#!/usr/bin/env bash
# Ship the committed HEAD to the EC2 box and restart both services.
#
# The server keeps its own .env and data/ directory. Neither is in git, so the archive
# below cannot overwrite them - credentials, the run database and the identity vault survive
# every deploy. Nothing here opens a port: 8000 and 8001 stay on 127.0.0.1 behind Caddy.
#
#   ./scripts/deploy.sh --check                 verify locally, ship nothing
#   ./scripts/deploy.sh --working-tree          ship what is on disk, committed or not
#   HOST=1.2.3.4 ./scripts/deploy.sh            deploy to a different address
set -euo pipefail

HOST="${HOST:-54.227.18.251}"
USER="${SSH_USER:-ubuntu}"
KEY="${SSH_KEY:-$(cd "$(dirname "$0")/../.." && pwd)/the-last-mile.pem}"
APP_DIR="${APP_DIR:-kamat}"
CHECK_ONLY=false; WORKING_TREE=false
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=true
[[ "${1:-}" == "--working-tree" ]] && WORKING_TREE=true

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- local gate
say "1/6  Checking what will be shipped"
cd "$(dirname "$0")/.."
if $WORKING_TREE; then
  # Ship exactly what is on disk. `git ls-files -co --exclude-standard` is tracked plus untracked
  # files that are NOT gitignored, so .env and data/ are excluded the same way git excludes them.
  git ls-files -co --exclude-standard > /tmp/lm_deploy_files.txt
  echo "     working tree: $(wc -l < /tmp/lm_deploy_files.txt | tr -d ' ') files, $(git status --porcelain | wc -l | tr -d ' ') of them uncommitted"
  echo "     NOTE: the server will run code that is in no commit. Reproduce it by committing the same tree."
elif [[ -n "$(git status --porcelain)" ]]; then
  git status --short
  die "Uncommitted changes. This deploys 'git archive HEAD', so anything not committed is NOT shipped. Use --working-tree to ship what is on disk."
else
  echo "     clean, at $(git rev-parse --short HEAD)"
fi

say "2/6  Running the checks that must pass before anything ships"
uv run ruff check src tests >/dev/null && echo "     ruff ok"
uv run lint-imports >/dev/null && echo "     layer contracts ok"
uv run pytest -q >/dev/null && echo "     tests ok"

if $CHECK_ONLY; then say "--check: everything passes locally. Nothing was sent."; exit 0; fi

# ---------------------------------------------------------------- reachability
say "3/6  Reaching $USER@$HOST"
[[ -f "$KEY" ]] || die "SSH key not found at $KEY (set SSH_KEY=...)"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$USER@$HOST")
"${SSH[@]}" true 2>/dev/null || die "Cannot reach $HOST on port 22. If the instance was stopped and started, its public IP has changed - pass HOST=<new ip>."

# ---------------------------------------------------------------- ship
say "4/6  Sending the code"
if $WORKING_TREE; then
  tar -cf - -T /tmp/lm_deploy_files.txt | "${SSH[@]}" "mkdir -p ~/$APP_DIR && tar -x -C ~/$APP_DIR"
else
  git archive HEAD | "${SSH[@]}" "mkdir -p ~/$APP_DIR && tar -x -C ~/$APP_DIR"
fi
echo "     extracted to ~/$APP_DIR (.env and data/ untouched - neither is in git)"

say "5/6  Installing and restarting"
"${SSH[@]}" "bash -lc '
  export PATH=\$HOME/.local/bin:\$PATH        # uv is not on a non-interactive PATH
  cd ~/$APP_DIR && uv sync --frozen
  sudo systemctl restart lastmile-bank lastmile-engine
  sleep 4
  systemctl is-active lastmile-bank lastmile-engine
'"

# ---------------------------------------------------------------- prove it
say "6/6  Checking it answers"
"${SSH[@]}" "bash -lc '
  curl -fsS -o /dev/null -w \"  engine /login  -> %{http_code}\n\" http://127.0.0.1:8000/login
  curl -fsS -o /dev/null -w \"  bank   /docs   -> %{http_code}\n\" http://127.0.0.1:8001/docs
'"
if $WORKING_TREE; then say "Deployed the working tree (uncommitted) to $HOST"
else say "Deployed $(git rev-parse --short HEAD) to $HOST"; fi
echo "Public: https://$HOST.sslip.io   (8000/8001 remain bound to 127.0.0.1)"
echo
echo "New in this release - do these once, signed in as admin:"
echo "  * Users & access: create a collection-agent account and give it a roster collector id (e.g. C01)."
echo "  * The 'guest' role is retired. An old guest line in the server .env is ignored, not an error."
echo "  * app_users and attempt_outcomes are created on startup; existing data is migrated in place."
