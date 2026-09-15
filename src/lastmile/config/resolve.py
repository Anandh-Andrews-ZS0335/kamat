"""Load, validate and merge the three packs into one immutable, hashed config."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from lastmile.config.schema import InstitutionPack, ResolvedConfig, RunConfig, ScenarioPack
from lastmile.config.settings import CONFIG_DIR


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f)


def load_scenario(scenario_id: str, config_dir: Path = CONFIG_DIR) -> ScenarioPack:
    return ScenarioPack.model_validate(_load(config_dir / "scenarios" / f"{scenario_id}.yaml"))


def load_institution(institution_id: str, config_dir: Path = CONFIG_DIR) -> InstitutionPack:
    return InstitutionPack.model_validate(_load(config_dir / "institutions" / f"{institution_id}.yaml"))


def config_hash(scenario: ScenarioPack, institution: InstitutionPack, run: RunConfig) -> str:
    canonical = json.dumps({"scenario": scenario.model_dump(mode="json"),
                            "institution": institution.model_dump(mode="json"),
                            "run": run.model_dump(mode="json")}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def resolve(run_name: str = "default", config_dir: Path = CONFIG_DIR, as_of: str | None = None) -> ResolvedConfig:
    run_path = config_dir / "runs" / f"{run_name}.yaml"
    run = RunConfig.model_validate(_load(run_path))
    if as_of:
        run.run.as_of = as_of
    scenario = load_scenario(run.run.scenario, config_dir)
    institution = load_institution(run.run.institution, config_dir)
    return ResolvedConfig(
        scenario=scenario, institution=institution, run=run,
        config_hash=config_hash(scenario, institution, run),
        files={"scenario": f"config/scenarios/{run.run.scenario}.yaml",
               "institution": f"config/institutions/{run.run.institution}.yaml",
               "run": f"config/runs/{run_name}.yaml"},
    )
