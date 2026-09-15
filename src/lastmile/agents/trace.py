"""Agent trace: every stage transition, handoff, tool call, API call and LLM call is written here.

The Admin Console reads nothing else to show who did what.
"""

from __future__ import annotations

from datetime import datetime

from lastmile.store import db


def _summarise(obj, depth: int = 0):
    """Keep trace payloads small and JSON-safe."""
    import numpy as np
    import pandas as pd

    if isinstance(obj, pd.DataFrame):
        return {"_frame": True, "rows": int(len(obj)), "columns": [str(c) for c in obj.columns][:24]}
    if isinstance(obj, dict):
        items = list(obj.items())[:30]
        return {str(k): _summarise(v, depth + 1) for k, v in items} if depth < 3 else f"{{{len(obj)} keys}}"
    if isinstance(obj, list | tuple):
        return [_summarise(v, depth + 1) for v in list(obj)[:12]] if depth < 3 else f"[{len(obj)} items]"
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, str) and len(obj) > 400:
        return obj[:400] + "…"
    return obj


class Tracer:
    def __init__(self, run_id: str, data_dir=None):
        self.run_id, self.data_dir = run_id, data_dir
        self.stage: str | None = None
        self._stage_started: dict[str, datetime] = {}

    def event(self, kind: str, agent: str, *, target: str | None = None, tool: str | None = None,
              module: str | None = None, status: str = "ok", ms: int = 0, input=None, output=None,
              message: str | None = None, stage: str | None = None) -> None:
        with db.connect(self.data_dir) as con:
            con.execute(
                "INSERT INTO trace_events (run_id, stage, ts, kind, agent, target, tool, module, status, ms,"
                " input_json, output_json, message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, stage or self.stage, db.now(), kind, agent, target, tool, module, status, ms,
                 db.dumps(_summarise(input)), db.dumps(_summarise(output)), message))

    def stage_plan(self, stages: list[tuple[str, str, list[str]]]) -> None:
        with db.connect(self.data_dir) as con:
            for i, (sid, title, agents) in enumerate(stages, start=1):
                con.execute("INSERT OR REPLACE INTO stages (run_id, stage, ord, title, agents_json, status)"
                            " VALUES (?,?,?,?,?,'pending')", (self.run_id, sid, i, title, db.dumps(agents)))

    def stage_begin(self, stage: str) -> None:
        self.stage = stage
        self._stage_started[stage] = datetime.now()
        with db.connect(self.data_dir) as con:
            con.execute("UPDATE stages SET status='running', started_at=? WHERE run_id=? AND stage=?",
                        (db.now(), self.run_id, stage))

    def stage_end(self, stage: str, status: str, metrics: dict | None = None, notes: list | None = None,
                  artifacts: list | None = None) -> None:
        started = self._stage_started.get(stage)
        ms = int((datetime.now() - started).total_seconds() * 1000) if started else None
        with db.connect(self.data_dir) as con:
            con.execute("UPDATE stages SET status=?, finished_at=?, ms=?, metrics_json=?, notes_json=?, artifacts_json=?"
                        " WHERE run_id=? AND stage=?",
                        (status, db.now(), ms, db.dumps(metrics or {}), db.dumps(notes or []),
                         db.dumps(artifacts or []), self.run_id, stage))
