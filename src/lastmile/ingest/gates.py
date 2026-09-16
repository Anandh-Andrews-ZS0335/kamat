"""Data quality gates. Every gate has a defined action; rows are quarantined and counted, never silently dropped."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from lastmile.config.schema import InstitutionPack


@dataclass
class GateResult:
    gate: str
    feed: str
    passed: bool
    severity: str          # abort | quarantine | warn | info
    affected_rows: int
    detail: str
    overridable: bool

    def dict(self) -> dict:
        return asdict(self)


class GateAbort(RuntimeError):
    def __init__(self, results: list[GateResult]):
        self.results = results
        failed = [r for r in results if not r.passed and r.severity == "abort"]
        super().__init__("; ".join(f"{r.gate}[{r.feed}]: {r.detail}" for r in failed))


def run_gates(frames: dict[str, pd.DataFrame], inst: InstitutionPack, as_of: date,
              previous_score_rows: int | None = None) -> tuple[dict[str, pd.DataFrame], list[GateResult], pd.DataFrame]:
    dq = inst.data_quality
    results: list[GateResult] = []
    quarantine: list[pd.DataFrame] = []
    out = {k: v.copy() for k, v in frames.items()}

    def quarantine_rows(feed: str, mask: pd.Series, reason: str):
        if mask.any():
            q = out[feed][mask].copy()
            q.insert(0, "quarantine_reason", reason)
            q.insert(0, "feed", feed)
            quarantine.append(q.astype(str))
            out[feed] = out[feed][~mask].reset_index(drop=True)

    # 1. freshness of the prediction feed - stale scores are worse than none
    score_dates = pd.to_datetime(out["scores"]["score_date"], errors="coerce")
    newest = score_dates.max()
    age = (pd.Timestamp(as_of) - newest).days if pd.notna(newest) else None
    ok = age is not None and 0 <= age <= dq.max_score_age_days
    results.append(GateResult("freshness", "scores", ok, "abort", 0 if ok else len(out["scores"]),
                              f"newest score {newest.date() if pd.notna(newest) else 'n/a'}, age {age} day(s), "
                              f"limit {dq.max_score_age_days}", overridable=True))

    # 2. volume
    for feed, n in dq.min_rows.items():
        rows = len(out.get(feed, []))
        results.append(GateResult("min_rows", feed, rows >= n, "abort", 0 if rows >= n else rows,
                                  f"{rows:,} rows, minimum {n:,}", overridable=True))

    # 3. completeness of required fields
    for feed, fields in dq.required_fields.items():
        df = out[feed]
        missing_cols = [f for f in fields if f not in df.columns]
        if missing_cols:
            results.append(GateResult("required_columns", feed, False, "abort", len(df),
                                      f"missing columns {missing_cols}", overridable=False))
            continue
        null_mask = df[fields].isna().any(axis=1)
        frac = float(null_mask.mean()) if len(df) else 0.0
        if frac > dq.max_null_fraction:
            results.append(GateResult("completeness", feed, False, "abort", int(null_mask.sum()),
                                      f"{frac:.1%} rows have null required fields, limit {dq.max_null_fraction:.0%}",
                                      overridable=True))
        else:
            quarantine_rows(feed, null_mask, "null required field")
            results.append(GateResult("completeness", feed, True, "quarantine", int(null_mask.sum()),
                                      f"{int(null_mask.sum())} row(s) with null required fields quarantined",
                                      overridable=False))

    # 4. ranges and domains
    if inst.pd_calibration.input_type == "band":
        bad_grade = ~out["scores"]["risk_grade"].isin(dq.valid_grades)
        quarantine_rows("scores", bad_grade, "unknown risk grade")
        results.append(GateResult("domain", "scores", True, "quarantine", int(bad_grade.sum()),
                                  f"{int(bad_grade.sum())} score(s) with grade outside {dq.valid_grades}", False))
    else:
        p = pd.to_numeric(out["scores"]["risk_grade"], errors="coerce")
        bad_p = p.isna() | (p < 0) | (p > 1)
        quarantine_rows("scores", bad_p, "risk probability missing or outside 0-1")
        results.append(GateResult("domain", "scores", True, "quarantine", int(bad_p.sum()),
                                  f"{int(bad_p.sum())} score(s) not a probability between 0 and 1", False))
    acc = out["accounts"]
    bad_range = (pd.to_numeric(acc["curr_bal"], errors="coerce") < 0) | (pd.to_numeric(acc["dpd"], errors="coerce") < 0)
    quarantine_rows("accounts", bad_range, "negative balance or dpd")
    results.append(GateResult("range", "accounts", True, "quarantine", int(bad_range.sum()),
                              f"{int(bad_range.sum())} account(s) with negative balance or dpd", False))

    # 5. referential integrity - contact limits are impossible without a member
    orphan = ~out["accounts"]["member_id"].isin(set(out["members"]["member_id"]))
    quarantine_rows("accounts", orphan, "account has no member record")
    results.append(GateResult("referential", "accounts", True, "quarantine", int(orphan.sum()),
                              f"{int(orphan.sum())} account(s) without a member record", False))

    # 6. suppression integrity - never run blind to suppressions, no override
    member_flags = [f for f in inst.suppression if f in inst.field_map["members"]]
    acct_flags = [f for f in inst.suppression if f in inst.field_map["accounts"]]
    consent_flags = [f for f in inst.suppression if f in inst.field_map["consents"]]
    present = (all(f in out["members"].columns for f in member_flags) and
               all(f in out["accounts"].columns for f in acct_flags) and
               all(f in out["consents"].columns for f in consent_flags))
    covered = set(member_flags) | set(acct_flags) | set(consent_flags)
    ok = present and covered == set(inst.suppression)
    results.append(GateResult("suppression_integrity", "members/accounts/consents", ok, "abort", 0,
                              f"{len(covered)}/{len(inst.suppression)} suppression flags present: {sorted(covered)}",
                              overridable=False))

    # 7. volume drift against the previous run
    if previous_score_rows:
        drift = abs(len(out["scores"]) - previous_score_rows) / previous_score_rows
        results.append(GateResult("volume_drift", "scores", drift <= 0.30, "warn", 0,
                                  f"{drift:.1%} change vs previous run ({previous_score_rows:,} rows)", True))
    else:
        results.append(GateResult("volume_drift", "scores", True, "info", 0, "no previous run to compare", False))

    q = pd.concat(quarantine, ignore_index=True) if quarantine else pd.DataFrame(columns=["feed", "quarantine_reason"])
    if any(not r.passed and r.severity == "abort" for r in results):
        raise GateAbort(results)
    return out, results, q
