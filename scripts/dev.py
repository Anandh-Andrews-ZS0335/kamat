"""Start the bank API (8001) and the engine + consoles (8000) together. Ctrl+C stops both."""

from __future__ import annotations

import signal
import subprocess
import sys
import time

PROCS = [
    ["-m", "uvicorn", "bank_api.main:app", "--host", "127.0.0.1", "--port", "8001"],
    ["-m", "uvicorn", "bank_api.harbor:app", "--host", "127.0.0.1", "--port", "8002"],
    ["-m", "uvicorn", "lastmile.api.app:app", "--host", "127.0.0.1", "--port", "8000"],
]


def main() -> None:
    running = [subprocess.Popen([sys.executable, *p]) for p in PROCS]
    print("\n  Bank API          http://127.0.0.1:8001/docs"
          "\n  Harbor bank API   http://127.0.0.1:8002/docs   (second bank, for onboarding)"
          "\n  Guided tour       http://127.0.0.1:8000/guide"
          "\n  Demo & Showcase   http://127.0.0.1:8000/demo"
          "\n  Admin Console     http://127.0.0.1:8000/admin"
          "\n  Manager Console   http://127.0.0.1:8000/manager"
          "\n  Daily report      http://127.0.0.1:8000/report"
          "\n  Configuration     http://127.0.0.1:8000/admin/config\n", flush=True)

    def stop(*_):
        for p in running:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while all(p.poll() is None for p in running):
        time.sleep(0.5)
    stop()


if __name__ == "__main__":
    main()
