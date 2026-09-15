"""Configuration editor: read, explain, check and save the three YAML packs.

Nothing is written until the new text passes the same validation a run applies (YAML syntax, the pack's
schema, cross-pack references), plus warnings for mistakes that would only show up at run time. Every save
keeps the previous version, records who changed what and why in the audit trail, and takes effect from the
next run: runs already made keep the configuration hash they were produced with.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from lastmile.api import auth, services
from lastmile.config import docs
from lastmile.config.resolve import resolve
from lastmile.config.schema import InstitutionPack, RunConfig, ScenarioPack
from lastmile.config.settings import CONFIG_DIR
from lastmile.governance import audit
from lastmile.store import artifacts, db

router = APIRouter()
MODELS = {"scenarios": ScenarioPack, "institutions": InstitutionPack, "runs": RunConfig}
ID_KEY = {"scenarios": ("scenario", "id"), "institutions": ("institution", "id")}
KNOWN_ACTIONS = {"SMS", "CALL", "PLAN", "HARDSHIP"}   # the bank's release endpoint and parts of the planner know these
_ID = re.compile(r"^[a-z0-9_]{1,60}$")


def _file(kind: str, pack_id: str) -> Path:
    if kind not in MODELS or not _ID.match(pack_id):
        raise HTTPException(404, "unknown configuration file")
    path = CONFIG_DIR / kind / f"{pack_id}.yaml"
    if not path.exists():
        raise HTTPException(404, f"{kind}/{pack_id}.yaml not found")
    return path


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _history_dir(kind: str, pack_id: str) -> Path:
    return CONFIG_DIR / ".history" / kind / pack_id


def _active() -> dict[str, str]:
    try:
        cfg = resolve()
        return {"runs": "default", "scenarios": cfg.scenario.scenario.id, "institutions": cfg.institution.institution.id}
    except Exception:  # an invalid pack on disk must not hide the editor that fixes it
        return {"runs": "default"}


# ------------------------------------------------------------------------------------------- locate
def locate(text: str, loc: tuple) -> int | None:
    """Best-effort 1-based line for a validation error path like ('capacity', 'planning_buffer') or ('actions', 2, 'id')."""
    lines = text.splitlines()
    line, indent = 0, -1
    found = None
    for part in loc:
        if isinstance(part, int):
            count, item_indent = -1, None
            for i in range(line, len(lines)):
                raw = lines[i]
                st = raw.lstrip()
                if not st or st.startswith("#"):
                    continue
                ind = len(raw) - len(st)
                if i > line and ind <= indent and not st.startswith("- "):
                    break
                if st.startswith("- ") and (item_indent is None or ind == item_indent):
                    item_indent = ind
                    count += 1
                    if count == part:
                        line, indent, found = i, ind, i + 1
                        break
            continue
        key = str(part)
        pat = re.compile(rf"(^|[\s{{,-]){re.escape(key)}\s*:")
        for i in range(line, len(lines)):
            raw = lines[i]
            st = raw.lstrip()
            if not st or st.startswith("#"):
                continue
            ind = len(raw) - len(st)
            if i > line and ind <= indent and found is not None and not pat.search(raw):
                break
            if pat.search(raw):
                line, indent, found = i, ind, i + 1
                break
    return found


# --------------------------------------------------------------------------------------------- diff
def _flatten(obj, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        for i, v in enumerate(obj):
            label = v.get("id") or v.get("product") and f"{v.get('product')}/{v.get('dpd_min')}-{v.get('dpd_max')}" or i
            out.update(_flatten(v, f"{prefix}[{label}]"))
    else:
        out[prefix] = obj
    return out


def _explain(kind: str, path: str) -> dict:
    section = re.split(r"[.\[]", path)[0]
    sec = docs.SECTIONS.get(kind, {}).get(section, {})
    rest = path[len(section):].lstrip(".")
    field = re.sub(r"\[[^\]]*\]", "", rest).lstrip(".")
    fields = sec.get("fields", {})
    meaning = fields.get(field) or fields.get(field.split(".")[-1]) if field else None
    return {"section": section, "section_title": sec.get("title"), "meaning": meaning, "effect": sec.get("effect"),
            "risk": sec.get("risk"), "used_by": sec.get("used_by", [])}


def changes(kind: str, old_text: str, new_data: dict) -> list[dict]:
    try:
        old = _flatten(yaml.safe_load(old_text) or {})
    except yaml.YAMLError:
        old = {}
    new = _flatten(new_data)
    out = []
    for path in sorted(set(old) | set(new)):
        if old.get(path, "<absent>") != new.get(path, "<absent>"):
            out.append({"path": path, "old": old.get(path), "new": new.get(path),
                        "kind": "added" if path not in old else "removed" if path not in new else "changed",
                        **_explain(kind, path)})
    return out


# ---------------------------------------------------------------------------------------- validate
def _warnings(kind: str, data: dict) -> list[dict]:
    w = []
    if kind == "scenarios":
        for a in data.get("actions", []):
            if isinstance(a, dict) and a.get("cost_minutes", 0) and a.get("cost_minutes", 0) > 120:
                w.append({"path": f"actions[{a.get('id')}].cost_minutes",
                          "message": f"{a.get('id')} takes {a['cost_minutes']} minutes; few collectors' shifts will fit many"})
    if kind == "institutions":
        cap = data.get("capacity", {}) or {}
        if isinstance(cap.get("planning_buffer"), int | float) and cap["planning_buffer"] >= 0.3:
            w.append({"path": "capacity.planning_buffer",
                      "message": f"{cap['planning_buffer']:.0%} of every shift is left unplanned; fewer members will be reached"})
        canonical = {k for m in (data.get("field_map") or {}).values() if isinstance(m, dict) for k in m}
        with db.connect() as con:
            row = con.execute("SELECT run_id FROM runs WHERE status IN ('awaiting_approval','released','superseded')"
                              " ORDER BY started_at DESC LIMIT 1").fetchone()
        if row and artifacts.has_frame(row["run_id"], "portfolio"):
            canonical |= set(artifacts.load_frame(row["run_id"], "portfolio").columns)
        if canonical:
            for name, rs in (data.get("eligibility") or {}).items():
                for group in ("all", "any"):
                    for r in (rs or {}).get(group, []) or []:
                        if isinstance(r, dict) and r.get("field") not in canonical:
                            w.append({"path": f"eligibility.{name}.{group}",
                                      "message": f"rule field '{r.get('field')}' is not a known data field; the next run will stop"})
            for flag in data.get("suppression") or []:
                if flag not in canonical:
                    w.append({"path": "suppression", "message": f"suppression flag '{flag}' is not a known data field"})
    if kind == "runs":
        if (data.get("run") or {}).get("as_of"):
            w.append({"path": "run.as_of", "message": "a fixed date makes every run use that date instead of the bank's "
                                                      "business day; leave it empty for daily runs"})
    return w


def validate(kind: str, pack_id: str, text: str) -> dict:
    path = _file(kind, pack_id)
    result = {"valid": False, "errors": [], "warnings": [], "changes": [], "config_hash_after": None}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        result["errors"].append({"stage": "YAML syntax", "path": None, "line": mark.line + 1 if mark else None,
                                 "message": f"{getattr(e, 'problem', None) or e}. Check indentation (spaces, not tabs) "
                                            "and that every 'key: value' has a space after the colon."})
        return result
    if not isinstance(data, dict):
        result["errors"].append({"stage": "YAML syntax", "path": None, "line": 1,
                                 "message": "the file must be a set of 'key: value' sections"})
        return result
    result["changes"] = changes(kind, path.read_text(), data)
    try:
        MODELS[kind].model_validate(data)
    except ValidationError as e:
        for err in e.errors():
            loc = tuple(x for x in err["loc"] if x not in ("__root__",))
            result["errors"].append({"stage": "Schema", "path": ".".join(str(x) for x in loc) or None,
                                     "line": locate(text, loc) if loc else None, "message": err["msg"],
                                     **(_explain(kind, ".".join(str(x) for x in loc)) if loc else {})})
        return result
    if kind == "scenarios":
        unknown = sorted({str(a.get("id")) for a in data.get("actions", []) if isinstance(a, dict)} - KNOWN_ACTIONS)
        if unknown:
            result["errors"].append({"stage": "Engine compatibility", "path": "actions", "line": locate(text, ("actions",)),
                                     "message": f"action id(s) {unknown} are not known to the bank's release endpoint or the "
                                                f"planner (known: {sorted(KNOWN_ACTIONS)}). Adding an action needs a code "
                                                "change first; change its label instead to rename it on screen."})
            return result
    if kind in ID_KEY:
        sec, key = ID_KEY[kind]
        if data[sec][key] != pack_id:
            result["errors"].append({"stage": "Schema", "path": f"{sec}.{key}", "line": locate(text, (sec, key)),
                                     "message": f"{sec}.{key} is '{data[sec][key]}' but the file is {pack_id}.yaml; "
                                                "they must match because other packs refer to it by id"})
            return result
    # cross-pack: resolve every run that uses this pack against a copy of the config with the new text in place
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "config"
        shutil.copytree(CONFIG_DIR, tmp_dir, ignore=shutil.ignore_patterns(".history"))
        (tmp_dir / kind / f"{pack_id}.yaml").write_text(text)
        for run_file in sorted((tmp_dir / "runs").glob("*.yaml")):
            run_name = run_file.stem
            try:
                run = RunConfig.model_validate(yaml.safe_load(run_file.read_text()))
            except (ValidationError, yaml.YAMLError):
                continue
            uses = {"runs": run_name == pack_id, "scenarios": run.run.scenario == pack_id,
                    "institutions": run.run.institution == pack_id}[kind]
            if not uses:
                continue
            try:
                cfg = resolve(run_name, config_dir=tmp_dir)
                if run_name == "default":
                    result["config_hash_after"] = cfg.config_hash
            except (ValidationError, FileNotFoundError, ValueError) as e:
                msg = "; ".join(x["msg"] for x in e.errors()) if isinstance(e, ValidationError) else str(e)
                result["errors"].append({"stage": "Across packs", "path": None, "line": None,
                                         "message": f"run '{run_name}' would not load: {msg}"})
    result["warnings"] = _warnings(kind, data)
    result["valid"] = not result["errors"]
    return result


# ------------------------------------------------------------------------------------------ routes
@router.get("/api/config/files")
def list_files():
    active = _active()
    out = []
    for kind, meta in docs.PACKS.items():
        for f in sorted((CONFIG_DIR / kind).glob("*.yaml")):
            out.append({"kind": kind, "id": f.stem, "pack": meta["title"], "path": f"config/{kind}/{f.name}",
                        "active": active.get(kind) == f.stem, "modified_at": datetime.fromtimestamp(f.stat().st_mtime, UTC).isoformat(),
                        "versions": len(list(_history_dir(kind, f.stem).glob("*.yaml")))})
    return {"files": out, "packs": docs.PACKS, "current_hash": _safe_hash()}


def _safe_hash() -> str | None:
    try:
        return resolve().config_hash
    except Exception:
        return None


@router.get("/api/config/files/{kind}/{pack_id}")
def read_file(kind: str, pack_id: str):
    path = _file(kind, pack_id)
    text = path.read_text()
    log = _history_dir(kind, pack_id) / "history.jsonl"
    history = [json.loads(x) for x in log.read_text().splitlines() if x.strip()] if log.exists() else []
    return {"kind": kind, "id": pack_id, "path": f"config/{kind}/{path.name}", "text": text, "sha": _sha(text),
            "pack": docs.PACKS[kind], "sections": docs.SECTIONS[kind], "active": _active().get(kind) == pack_id,
            "history": list(reversed(history)), "check": validate(kind, pack_id, text)}


class CheckIn(BaseModel):
    text: str = Field(max_length=200_000)


@router.post("/api/config/files/{kind}/{pack_id}/check")
def check_file(kind: str, pack_id: str, body: CheckIn):
    return validate(kind, pack_id, body.text)


class SaveIn(BaseModel):
    text: str = Field(max_length=200_000)
    base_sha: str
    reason: str = Field(min_length=5, max_length=500)


@router.put("/api/config/files/{kind}/{pack_id}")
def save_file(kind: str, pack_id: str, body: SaveIn, request: Request):
    changed_by = auth.actor(request)
    path = _file(kind, pack_id)
    current = path.read_text()
    if _sha(current) != body.base_sha:
        raise HTTPException(409, "this file changed on disk after you opened it; reload it and apply your edit again")
    if current == body.text:
        raise HTTPException(422, "nothing changed")
    busy = services.running_run()
    if busy:
        raise HTTPException(409, f"run {busy['run_id']} is in progress; it would stop on a configuration change. "
                                 "Save after it finishes")
    result = validate(kind, pack_id, body.text)
    if not result["valid"]:
        raise HTTPException(422, {"message": "the new configuration did not pass the checks", "check": result})
    hash_before = _safe_hash()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    hist = _history_dir(kind, pack_id)
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"{stamp}.yaml").write_text(current)
    path.write_text(body.text)
    entry = {"version": stamp, "saved_at": datetime.now(UTC).isoformat(), "changed_by": changed_by, "reason": body.reason,
             "sha_before": _sha(current), "sha_after": _sha(body.text), "config_hash_before": hash_before,
             "config_hash_after": _safe_hash(), "changes": [{k: c[k] for k in ("path", "old", "new", "kind")} for c in result["changes"]]}
    with (hist / "history.jsonl").open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    audit.append(None, "config.changed", changed_by, {"file": f"config/{kind}/{path.name}", **entry})
    return {"saved": True, **entry, "warnings": result["warnings"]}


@router.get("/api/config/files/{kind}/{pack_id}/versions/{version}")
def read_version(kind: str, pack_id: str, version: str):
    _file(kind, pack_id)
    if not re.fullmatch(r"\d{8}T\d{6}", version):
        raise HTTPException(404, "unknown version")
    f = _history_dir(kind, pack_id) / f"{version}.yaml"
    if not f.exists():
        raise HTTPException(404, "unknown version")
    return {"version": version, "text": f.read_text()}
