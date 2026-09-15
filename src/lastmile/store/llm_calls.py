"""Full record of each LLM call. The agent trace keeps a short summary and points here by id."""

from __future__ import annotations

from pathlib import Path

from lastmile.store import db


def record(run_id: str, stage: str | None, agent: str, tool: str, purpose: str, provider: str, model: str,
           system: str, prompt: str, exchange: dict | None, parsed, error: str | None,
           data_dir: Path | None = None) -> int:
    ex = exchange or {}
    usage = ex.get("usage") or {}
    with db.connect(data_dir) as con:
        cur = con.execute(
            "INSERT INTO llm_calls (run_id, stage, agent, tool, purpose, provider, model, endpoint, status, http_status,"
            " latency_ms, system_prompt, user_prompt, request_json, response_text, parsed_json, error, prompt_tokens,"
            " response_tokens, thinking_tokens, total_tokens, finish_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, stage, agent, tool, purpose, provider, model, ex.get("endpoint"), "error" if error else "ok",
             ex.get("http_status"), ex.get("latency_ms"), system, prompt, db.dumps(ex.get("request")),
             ex.get("response_text"), db.dumps(parsed) if parsed is not None else None, error,
             usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), usage.get("thoughtsTokenCount"),
             usage.get("totalTokenCount"),
             ex.get("finish_reason"), db.now()))
        return cur.lastrowid


def for_run(run_id: str, data_dir: Path | None = None) -> list[dict]:
    with db.connect(data_dir) as con:
        return db.rows(con, "SELECT id, run_id, stage, agent, tool, purpose, provider, model, status, http_status, latency_ms,"
                            " prompt_tokens, response_tokens, thinking_tokens, total_tokens, finish_reason, error, created_at,"
                            " LENGTH(user_prompt) prompt_chars, LENGTH(response_text) response_chars"
                            " FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,))


def get(call_id: int, data_dir: Path | None = None) -> dict | None:
    with db.connect(data_dir) as con:
        r = con.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["request"], out["parsed"] = db.loads(out.pop("request_json")), db.loads(out.pop("parsed_json"))
    return out
