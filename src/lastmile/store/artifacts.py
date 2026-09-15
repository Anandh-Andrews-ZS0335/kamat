"""Parquet artifacts per run, so every intermediate table can be inspected in the Admin Console."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from lastmile.config.settings import DATA_DIR


def run_dir(run_id: str, data_dir: Path | None = None) -> Path:
    d = (data_dir or DATA_DIR) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_frame(run_id: str, name: str, df: pd.DataFrame, data_dir: Path | None = None) -> dict:
    path = run_dir(run_id, data_dir) / f"{name}.parquet"
    df.to_parquet(path, index=False)
    return {"name": name, "rows": int(len(df)), "columns": [str(c) for c in df.columns]}


def load_frame(run_id: str, name: str, data_dir: Path | None = None) -> pd.DataFrame:
    path = run_dir(run_id, data_dir) / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"artifact '{name}' not found for run {run_id}")
    return pd.read_parquet(path)


def has_frame(run_id: str, name: str, data_dir: Path | None = None) -> bool:
    return (run_dir(run_id, data_dir) / f"{name}.parquet").exists()


def save_json(run_id: str, name: str, obj: dict, data_dir: Path | None = None) -> None:
    (run_dir(run_id, data_dir) / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str))


def load_json(run_id: str, name: str, data_dir: Path | None = None) -> dict:
    return json.loads((run_dir(run_id, data_dir) / f"{name}.json").read_text())


def identity_path(run_id: str, data_dir: Path | None = None) -> Path:
    """PII lives outside the run folder so the artifact browser can never serve it."""
    d = (data_dir or DATA_DIR) / "identity"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{run_id}.parquet"


def model_path(model_run_id: str, data_dir: Path | None = None) -> Path:
    d = (data_dir or DATA_DIR) / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{model_run_id}.pkl"
