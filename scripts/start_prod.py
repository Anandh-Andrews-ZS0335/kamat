"""Production multi-process runner for Last Mile.

Starts:
  1. bank_api on internal port (default: 8001)
  2. lastmile engine & consoles on public port (default: $PORT or 8000)

Binds to 0.0.0.0 so it is accessible over local network and cloud environments.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

BANK_PORT = int(os.environ.get("BANK_API_PORT", "8001"))
PUBLIC_PORT = int(os.environ.get("PORT", "8000"))

# Ensure bank data exists before starting
try:
    from bank_api.generator import GenParams, generate
    from bank_api.main import DATA_DIR, DB_PATH
    if not DB_PATH.exists():
        print(f"[*] Initializing synthetic credit union bank data in {DATA_DIR}...", flush=True)
        generate(DATA_DIR, GenParams())
except Exception as e:
    print(f"[!] Warning during bank pre-seed: {e}", flush=True)

PROCS = [
    [sys.executable, "-m", "uvicorn", "bank_api.main:app", "--host", "127.0.0.1", "--port", str(BANK_PORT)],
    [sys.executable, "-m", "uvicorn", "lastmile.api.app:app", "--host", "0.0.0.0", "--port", str(PUBLIC_PORT)],
]


def main() -> None:
    print("\n=======================================================", flush=True)
    print("  🚀 LAST MILE - AGENTIC PRESCRIPTIVE ANALYTICS", flush=True)
    print("=======================================================", flush=True)
    print(f"  Public Web Console:  http://0.0.0.0:{PUBLIC_PORT}/demo", flush=True)
    print(f"  Manager Console:     http://0.0.0.0:{PUBLIC_PORT}/manager", flush=True)
    print(f"  Admin Trace Console: http://0.0.0.0:{PUBLIC_PORT}/admin", flush=True)
    print(f"  Internal Bank API:   http://127.0.0.1:{BANK_PORT}/docs", flush=True)
    print("=======================================================\n", flush=True)

    running = [subprocess.Popen(p) for p in PROCS]

    def stop(*_):
        print("\n[*] Gracefully stopping services...", flush=True)
        for p in running:
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    while all(p.poll() is None for p in running):
        time.sleep(0.5)

    stop()


if __name__ == "__main__":
    main()
