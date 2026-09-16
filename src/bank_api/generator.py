"""Synthetic credit union.

Plays the client. Simulates a delinquent portfolio for HISTORY_DAYS under the bank's
legacy collections policy (work the worst risk grades first), then stops at "today".
`advance_day` then moves the bank forward one day at a time: actions released by the engine
are executed on the accounts they name, everything else follows the legacy process.

It deliberately produces every real-world problem the engine claims to handle:
  * risk scores are GRADES (A-E), not probabilities, on a 365-day horizon
  * the treatment log is confounded: worse grades were contacted more often
  * a small random holdout exists, so the causal effect is identifiable
  * some members hold two or three accounts (contact limits are per person)
  * outcomes only exist once 30 days have passed (label maturity)
  * suppression flags, missing consents, hardship plans

The true effect of each action on each account lives in data/bank/sealed/ and is
never served by the API. Only scripts/evaluate_against_truth.py reads it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ACTIONS = ["SMS", "CALL", "PLAN", "HARDSHIP"]
SEGMENTS = ["persuadable", "sure_thing", "lost_cause", "sleeping_dog"]

# Base 30-day cure probability with no contact, by latent segment.
BASE_CURE = {"persuadable": 0.16, "sure_thing": 0.70, "lost_cause": 0.04, "sleeping_dog": 0.42}

# True change in 30-day cure probability, by segment and action. Sealed.
TRUE_EFFECT = {
    "persuadable": {"SMS": 0.07, "CALL": 0.20, "PLAN": 0.27, "HARDSHIP": 0.22},
    "sure_thing": {"SMS": 0.01, "CALL": 0.01, "PLAN": 0.02, "HARDSHIP": 0.02},
    "lost_cause": {"SMS": 0.00, "CALL": 0.01, "PLAN": 0.03, "HARDSHIP": 0.05},
    "sleeping_dog": {"SMS": -0.04, "CALL": -0.14, "PLAN": -0.08, "HARDSHIP": -0.03},
}

FIRST = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn",
         "Priya", "Marcus", "Lena", "Ken", "Dana", "Tom", "Aisha", "Diego", "Mei", "Omar"]
LAST = ["Nguyen", "Patel", "Garcia", "Smith", "Okafor", "Kim", "Rossi", "Silva", "Cohen", "Novak",
        "Reyes", "Osei", "Whitfield", "Haddad", "Larsen", "Moreau", "Tanaka", "Brooks", "Iyer", "Walsh"]

# Simulation state carried from one day to the next (sealed: latent traits live here).
DYNAMIC = ["dpd", "bal", "missed", "mos", "status", "resolve_day", "last_queued", "entry_day"]
STATIC = ["acct", "cif", "owner_idx", "segment", "sev", "ptp", "dd", "orig", "prod", "hardship_active",
          "phone_ok", "sms_ok"]


class NotAdvanceable(RuntimeError):
    pass


@dataclass(frozen=True)
class GenParams:
    members: int = 4000
    history_days: int = 120
    seed: int = 20260914
    as_of: date | None = None  # defaults to today
    institution: str = "Riverbend Credit Union"
    # How this bank's own predictive model reports risk: "grade" (A-E scorecard) or "probability" (a default
    # probability over score_horizon_days, as a logistic or gradient-boosted model would output).
    score_style: str = "grade"
    score_horizon_days: int = 365


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _grade(pd12: np.ndarray) -> np.ndarray:
    return np.select([pd12 < 0.03, pd12 < 0.10, pd12 < 0.25, pd12 < 0.50], ["A", "B", "C", "D"], "E")


def _true_pd12(dpd, missed, mos, ptp, dd, noise):
    return _sigmoid(-2.4 + 0.022 * (dpd - 30) + 0.28 * missed + 0.22 * mos - 1.1 * ptp - 0.5 * dd + noise)


def _bank_probability(pd12: np.ndarray, horizon_days: int) -> np.ndarray:
    """The bank's model output: default probability over its own horizon, a little over-confident, as an
    uncalibrated boosted-tree model tends to be. Deterministic - it consumes no random numbers."""
    p = 1.0 - np.power(1.0 - np.clip(pd12, 1e-4, 0.9999), horizon_days / 365.0)
    logit = np.log(p / (1.0 - p))
    return np.round(1.0 / (1.0 + np.exp(-(1.15 * logit + 0.08))), 4)


def _true_effect(segment: str, action: str, dpd: float) -> float:
    eff = TRUE_EFFECT[segment][action]
    if segment == "persuadable":
        eff *= max(0.35, 1.15 - dpd / 200.0)  # influence decays as delinquency deepens
    return eff


def _simulate_day(st: dict, d: int, day: date, rng: np.random.Generator, queue_rows: list, outcome_rows: list,
                  hist_truth: list, forced: dict[int, str] | None = None, executed: list | None = None,
                  score_style: str = "grade", score_horizon_days: int = 365) -> None:
    """One day of the book. `forced` maps account index -> action released by the engine for this day."""
    dpd, bal, missed, mos, status = st["dpd"], st["bal"], st["missed"], st["mos"], st["status"]
    resolve_day, last_queued, entry_day = st["resolve_day"], st["last_queued"], st["entry_day"]
    acct, cif, owner_idx, segment = st["acct"], st["cif"], st["owner_idx"], st["segment"]
    sev, ptp, dd, orig, hardship_active = st["sev"], st["ptp"], st["dd"], st["orig"], st["hardship_active"]
    phone_ok, sms_ok = st["phone_ok"], st["sms_ok"]
    n_a = len(acct)
    grade_rank = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
    forced = forced or {}

    # resolutions scheduled from earlier cures
    cured_now = (resolve_day == d) & (status == "DELINQUENT")
    status[cured_now] = "CURED"
    status[(entry_day == d) & (status == "CURRENT")] = "DELINQUENT"
    active = status == "DELINQUENT"

    eligible = active & (d - last_queued >= 7)
    queued = np.where(eligible & (rng.random(n_a) < 0.17))[0]
    if forced:
        in_queue = set(queued.tolist())
        extra = [i for i in sorted(forced) if active[i] and i not in in_queue]
        queued = np.concatenate([queued, np.array(extra, dtype=int)]).astype(int)
        for i in sorted(forced):
            if not active[i] and executed is not None:
                executed.append({"idx": i, "action": forced[i], "outcome": f"not executed: account {status[i].lower()}",
                                 "rpc": 0})
    if len(queued):
        pd12 = _true_pd12(dpd[queued], missed[queued], mos[queued], ptp[queued], dd[queued],
                          rng.normal(0, 0.35, len(queued)))
        grades = _grade(pd12)
        probs = _bank_probability(pd12, score_horizon_days) if score_style == "probability" else None
        # legacy policy: worst grade first, noisy, capacity-limited, with a random holdout
        priority = np.array([grade_rank[g] for g in grades]) + rng.normal(0, 1.3, len(queued))
        order = np.argsort(-priority)
        worked = np.zeros(len(queued), dtype=bool)
        worked[order[: int(0.45 * len(queued))]] = True
        holdout = rng.random(len(queued)) < 0.12
        worked &= ~holdout

        for k, i in enumerate(queued):
            last_queued[i] = d
            action = "NONE"
            if i in forced:
                action = forced[i]                      # approved by a manager, executed as released
            elif worked[k]:
                if dpd[i] < 45:
                    action = "SMS" if (rng.random() < 0.6 and sms_ok[i]) else "CALL"
                elif dpd[i] <= 90:
                    action = "CALL" if rng.random() < 0.6 else "PLAN"
                else:
                    action = ("HARDSHIP" if (rng.random() < 0.35 and hardship_active[i] == 0) else "PLAN")
                if action != "SMS" and not phone_ok[i]:
                    action = "SMS" if sms_ok[i] else "NONE"
            p0 = BASE_CURE[segment[i]]
            eff = 0.0 if action == "NONE" else _true_effect(segment[i], action, dpd[i])
            p = float(np.clip(p0 + eff, 0.01, 0.97))
            cured = rng.random() < p
            rpc = int(action in ("CALL", "PLAN", "HARDSHIP") and rng.random() < 0.62)
            queue_rows.append({
                "QUEUE_DT": day.isoformat(), "ACCT_NBR": acct[i], "CIF_KEY": cif[owner_idx[i]],
                "DPD_CNT": int(dpd[i]), "CURR_BAL": round(float(bal[i]), 2),
                **({"PD_EST": float(probs[k])} if score_style == "probability" else {"RISK_GRADE": grades[k]}),
                "PMTS_MISSED_12M": int(missed[i]), "MOS_SINCE_LAST_PMT": int(mos[i]),
                "ACTION_CD": action, "RPC_IND": rpc,
            })
            outcome_rows.append({
                "ACCT_NBR": acct[i], "QUEUE_DT": day.isoformat(), "CURED_30D_IND": int(cured),
                "AMT_PAID_30D": round(float(min(bal[i], (dpd[i] / 30.0) * orig[i] * 0.03)) if cured else 0.0, 2),
            })
            hist_truth.append({"ACCT_NBR": acct[i], "QUEUE_DT": day.isoformat(), "SEGMENT": segment[i],
                               "ACTION_CD": action, "TRUE_P": p})
            if i in forced and executed is not None:
                executed.append({"idx": int(i), "action": action, "outcome": "executed", "rpc": rpc})
            if cured and rng.random() < 0.6:   # some cures re-default before the account leaves the book
                resolve_day[i] = d + int(rng.integers(5, 25))

    # delinquency evolves for everyone still delinquent
    dpd[active] += 1
    new_miss = active & (dpd % 30 == 0) & (rng.random(n_a) < 0.5 + 0.2 * np.clip(sev, -1, 1))
    missed[new_miss] = np.minimum(12, missed[new_miss] + 1)
    mos[new_miss] = np.minimum(12, mos[new_miss] + 1)
    bal[active] *= 1.0002
    charged = active & (dpd > 180)
    status[charged] = "CHARGED_OFF"


def _views(st: dict, history_days: int, as_of: date, rng: np.random.Generator, score_style: str = "grade",
           score_horizon_days: int = 365):
    """What the bank publishes today: account state, today's scores and today's sealed truth."""
    dpd, bal, missed, mos, orig, prod = st["dpd"], st["bal"], st["missed"], st["mos"], st["orig"], st["prod"]
    status = st["status"].copy()
    resolve_day = st["resolve_day"]
    acct, segment = st["acct"], st["segment"]
    n_a = len(acct)

    status[(resolve_day >= 0) & (resolve_day <= history_days) & (status == "DELINQUENT")] = "CURED"
    status[status == "CURRENT"] = "CURED"   # never went delinquent inside the window
    dynamic = pd.DataFrame({
        "ACCT_STATUS": status,
        "DPD_CNT": np.where(status == "DELINQUENT", dpd, 0).astype(int),
        "CURR_BAL": np.where(status == "CHARGED_OFF", 0, bal).round(2),
        "PMTS_MISSED_12M": missed.astype(int),
        "MOS_SINCE_LAST_PMT": mos.astype(int),
        "MIN_PMT_AMT": (orig * np.where(prod == "CARD", 0.03, 0.025)).round(2),
    })

    live = status == "DELINQUENT"
    pd12_today = _true_pd12(dpd, missed, mos, st["ptp"], st["dd"], rng.normal(0, 0.35, n_a))
    if score_style == "probability":
        risk = {"PD_EST": _bank_probability(pd12_today[live], score_horizon_days)}
        model, horizon = "xgb-collect-2.3", score_horizon_days
    else:
        risk = {"RISK_GRADE": _grade(pd12_today[live])}
        model, horizon = "cu-delinq-v4.2", 365
    scores = pd.DataFrame({
        "ACCT_NBR": acct[live],
        **risk,
        "SCORE_DT": (as_of - timedelta(days=1)).isoformat(),
        "MDL_VER": model,
        "PD_HORIZON_DAYS": horizon,
    })

    live_idx = np.where(live)[0]
    truth_today = pd.DataFrame({
        "ACCT_NBR": acct[live_idx],
        "SEGMENT": segment[live_idx],
        "TRUE_P0": [BASE_CURE[segment[i]] for i in live_idx],
        **{f"TRUE_UPLIFT_{a}": [
            float(np.clip(BASE_CURE[segment[i]] + _true_effect(segment[i], a, dpd[i]), 0.01, 0.97)
                  - BASE_CURE[segment[i]]) for i in live_idx] for a in ACTIONS},
    })
    return dynamic, scores, truth_today, live


def _contacts(consents: pd.DataFrame, queue: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Contacts in the last 7 days, per member (consent/contact store view)."""
    recent = queue[(queue["ACTION_CD"] != "NONE") &
                   (pd.to_datetime(queue["QUEUE_DT"]) >= pd.Timestamp(as_of - timedelta(days=7)))]
    c7 = recent.groupby("CIF_KEY").size()
    rpc = queue[queue["RPC_IND"] == 1].groupby("CIF_KEY")["QUEUE_DT"].max()
    consents = consents.copy()
    consents["CONTACTS_7D"] = consents["CIF_KEY"].map(c7).fillna(0).astype(int)
    consents["LAST_RPC_DT"] = consents["CIF_KEY"].map(rpc)
    return consents


def _save_state(out_dir: Path, st: dict, rng: np.random.Generator, meta: dict) -> None:
    sealed = out_dir / "sealed"
    arrays = {k: (st[k].astype(str) if st[k].dtype == object else st[k]) for k in DYNAMIC + STATIC}
    np.savez_compressed(sealed / "state.npz", **arrays)
    (sealed / "state.json").write_text(json.dumps({**meta, "rng": rng.bit_generator.state}, default=str))


def _table_digest(db_path: Path, table: str) -> str:
    import hashlib

    with sqlite3.connect(db_path) as con:
        rows = con.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def recover_state(out_dir: Path) -> bool:
    """Rebuild the sealed day-by-day state of a bank seeded before it existed.

    Generation is deterministic, so the same seed, member count, history length and date reproduce the book
    exactly. The state is adopted only if the regenerated tables match the live ones row for row; the live
    database - including actions already received - is never touched.
    """
    import shutil
    import tempfile

    db_path = out_dir / "bank.db"
    with sqlite3.connect(db_path) as con:
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        members = con.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        first = con.execute("SELECT MIN(QUEUE_DT) FROM collections_queue_history").fetchone()[0]
    if not first or "seed" not in meta:
        return False
    as_of = date.fromisoformat(meta["as_of"])
    span = (as_of - date.fromisoformat(first)).days
    with tempfile.TemporaryDirectory() as tmp:
        for days in (span, span + 1, span + 2):
            trial = Path(tmp) / str(days)
            generate(trial, GenParams(members=members, history_days=days, seed=int(meta["seed"]), as_of=as_of))
            if all(_table_digest(trial / "bank.db", t) == _table_digest(db_path, t)
                   for t in ("accounts", "risk_scores", "collections_queue_history", "outcomes")):
                sealed = out_dir / "sealed"
                sealed.mkdir(exist_ok=True)
                for name in ("state.npz", "state.json", "pending_outcomes.parquet", f"truth_{as_of.isoformat()}.csv"):
                    shutil.copy2(trial / "sealed" / name, sealed / name)
                return True
    return False


def _load_state(out_dir: Path) -> tuple[dict, np.random.Generator, dict]:
    sealed = out_dir / "sealed"
    if not (sealed / "state.npz").exists() and not recover_state(out_dir):
        raise NotAdvanceable("this bank's data does not match any seed it could be rebuilt from, so it cannot move "
                             "day by day; run `make seed` to start a fresh simulated bank")
    with np.load(sealed / "state.npz", allow_pickle=False) as z:
        st = {k: z[k].copy() for k in z.files}
    st["status"] = st["status"].astype(object)
    meta = json.loads((sealed / "state.json").read_text())
    rng = np.random.default_rng()
    rng.bit_generator.state = meta.pop("rng")
    return st, rng, meta


def generate(out_dir: Path, params: GenParams = GenParams()) -> dict:
    rng = np.random.default_rng(params.seed)
    as_of = params.as_of or date.today()
    start = as_of - timedelta(days=params.history_days)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sealed").mkdir(exist_ok=True)

    # ---------------------------------------------------------------- members
    n_m = params.members
    cif = np.array([f"CIF{100000 + i}" for i in range(n_m)])
    tenure = rng.integers(3, 240, n_m)
    members = pd.DataFrame({
        "CIF_KEY": cif,
        "FIRST_NM": rng.choice(FIRST, n_m),
        "LAST_NM": rng.choice(LAST, n_m),
        "PHONE_NBR": [f"+1-555-01{rng.integers(0, 100):02d}-{rng.integers(1000, 9999)}" for _ in range(n_m)],
        "EMAIL_ADDR": [f"member{100000 + i}@example.org" for i in range(n_m)],
        "TZ_CD": rng.choice(["America/Chicago", "America/New_York", "America/Denver"], n_m, p=[0.6, 0.3, 0.1]),
        "LANG_CD": rng.choice(["en", "es"], n_m, p=[0.85, 0.15]),
        "MEMBER_SINCE_DT": [(as_of - timedelta(days=int(t) * 30)).isoformat() for t in tenure],
        "TENURE_MOS": tenure,
        "DIRECT_DEPOSIT_IND": (rng.random(n_m) < 0.45).astype(int),
        "DECEASED_IND": (rng.random(n_m) < 0.004).astype(int),
        "BANKRUPTCY_IND": (rng.random(n_m) < 0.015).astype(int),
        "CEASE_DESIST_IND": (rng.random(n_m) < 0.01).astype(int),
    })
    consents = pd.DataFrame({
        "CIF_KEY": cif,
        "PHONE_CONSENT_IND": (rng.random(n_m) < 0.88).astype(int),
        "SMS_CONSENT_IND": (rng.random(n_m) < 0.72).astype(int),
        "EMAIL_CONSENT_IND": (rng.random(n_m) < 0.60).astype(int),
        "DNC_IND": (rng.random(n_m) < 0.02).astype(int),
    })
    contact_sensitivity = rng.normal(0, 0.9, n_m)

    # --------------------------------------------------------------- accounts
    per_member = rng.choice([1, 2, 3], n_m, p=[0.75, 0.20, 0.05])
    owner_idx = np.repeat(np.arange(n_m), per_member)
    n_a = len(owner_idx)
    prod = rng.choice(["AUTO", "PERS", "CARD"], n_a, p=[0.30, 0.40, 0.30])
    orig = np.where(prod == "AUTO", rng.uniform(15000, 45000, n_a),
                    np.where(prod == "PERS", rng.uniform(3000, 25000, n_a), rng.uniform(1000, 15000, n_a)))
    bal0 = orig * rng.uniform(0.40, 0.95, n_a)
    ptp = rng.beta(2.2, 2.2, n_a)
    sev = rng.normal(0, 1, n_a)                      # financial severity trait, seen only through proxies
    dd = members["DIRECT_DEPOSIT_IND"].to_numpy()[owner_idx]
    ten = tenure[owner_idx]
    card = prod == "CARD"

    # steady inflow: each account becomes delinquent on its own day, some before the window opens
    entry_day = rng.integers(-90, params.history_days, n_a)
    dpd0 = np.where(entry_day <= 0, 30 + (-entry_day), 30).astype(float)
    missed0 = np.clip(np.round(1 + 1.2 * sev + rng.poisson(0.3, n_a) + np.maximum(0, -entry_day) / 30), 0, 12)
    mos0 = np.clip(np.round(1 + 0.9 * sev + rng.normal(0, 0.3, n_a) + np.maximum(0, -entry_day) / 30), 0, 12)

    # latent segment from stable traits: learnable from observables, never observed directly
    s = np.column_stack([
        0.9 + 1.6 * ptp - 0.35 * np.abs(sev) + 0.25 * (prod == "AUTO"),                    # persuadable
        -0.3 + 1.3 * dd + 0.006 * ten - 0.9 * sev + 0.9 * ptp,                              # sure thing
        -0.1 + 1.1 * sev - 1.3 * ptp - 0.6 * dd,                                            # lost cause
        -0.2 + 1.1 * (ten < 24) + 0.8 * card - 0.5 * dd + contact_sensitivity[owner_idx],   # sleeping dog
    ])
    s = s + rng.gumbel(0, 0.22, s.shape)
    segment = np.array(SEGMENTS)[s.argmax(axis=1)]

    acct = np.array([f"{7000000 + i}" for i in range(n_a)])
    hardship_active = (rng.random(n_a) < 0.06).astype(int)
    accounts = pd.DataFrame({
        "ACCT_NBR": acct,
        "CIF_KEY": cif[owner_idx],
        "PROD_CD": prod,
        "SECURED_IND": (prod == "AUTO").astype(int),
        "ORIG_AMT": orig.round(2),
        "INT_RATE": np.where(prod == "CARD", 0.219, np.where(prod == "AUTO", 0.069, 0.119)),
        "OPEN_DT": [(as_of - timedelta(days=int(d))).isoformat() for d in rng.integers(200, 2500, n_a)],
        "LITIGATION_IND": (rng.random(n_a) < 0.01).astype(int),
        "FRAUD_HOLD_IND": (rng.random(n_a) < 0.005).astype(int),
        "HARDSHIP_ACTIVE_IND": hardship_active,
        "HARDSHIP_PLANS_12M": rng.choice([0, 1, 2], n_a, p=[0.75, 0.2, 0.05]),
        "PTP_KEPT_RATE_12M": ptp.round(3),
        # complaint history: the observable trace of members who react badly to contact
        "COMPLAINTS_12M": rng.poisson(np.exp(-1.6 + 1.1 * contact_sensitivity[owner_idx])),
    })

    # ------------------------------------------------------ daily simulation
    st = {
        "dpd": dpd0.copy(), "bal": bal0.copy(), "missed": missed0.copy(), "mos": mos0.copy(),
        "status": np.where(entry_day <= 0, "DELINQUENT", "CURRENT").astype(object),
        "resolve_day": np.full(n_a, -1), "last_queued": np.full(n_a, -999), "entry_day": entry_day,
        "acct": acct, "cif": cif, "owner_idx": owner_idx, "segment": segment, "sev": sev, "ptp": ptp, "dd": dd,
        "orig": orig, "prod": prod, "hardship_active": hardship_active,
        "phone_ok": consents["PHONE_CONSENT_IND"].to_numpy()[owner_idx] == 1,
        "sms_ok": consents["SMS_CONSENT_IND"].to_numpy()[owner_idx] == 1,
    }
    queue_rows, outcome_rows, hist_truth = [], [], []
    style = {"score_style": params.score_style, "score_horizon_days": params.score_horizon_days}
    for d in range(params.history_days):
        _simulate_day(st, d, start + timedelta(days=d), rng, queue_rows, outcome_rows, hist_truth, **style)

    queue = pd.DataFrame(queue_rows)
    all_outcomes = pd.DataFrame(outcome_rows)
    matured = pd.to_datetime(all_outcomes["QUEUE_DT"]) <= pd.Timestamp(as_of - timedelta(days=30))  # label has matured
    outcomes = all_outcomes[matured].reset_index(drop=True)

    # ----------------------------------------------- today's view of the book
    dynamic, scores, truth_today, live = _views(st, params.history_days, as_of, rng, **style)
    for col in dynamic.columns:
        accounts[col] = dynamic[col].to_numpy()
    consents = _contacts(consents, queue, as_of)

    # ------------------------------------------------------- sealed truth
    truth_today.to_csv(out_dir / "sealed" / "truth_today.csv", index=False)
    truth_today.to_csv(out_dir / "sealed" / f"truth_{as_of.isoformat()}.csv", index=False)
    pd.DataFrame(hist_truth).to_csv(out_dir / "sealed" / "truth_history.csv", index=False)
    all_outcomes[~matured].to_parquet(out_dir / "sealed" / "pending_outcomes.parquet", index=False)
    _save_state(out_dir, st, rng, {"as_of": as_of.isoformat(), "history_days": params.history_days,
                                   "initial_history_days": params.history_days, "seed": params.seed,
                                   "institution": params.institution, **style})

    # ------------------------------------------------------------ persist
    db_path = out_dir / "bank.db"
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as con:
        members.to_sql("members", con, index=False)
        consents.to_sql("consents", con, index=False)
        accounts.to_sql("accounts", con, index=False)
        scores.to_sql("risk_scores", con, index=False)
        queue.to_sql("collections_queue_history", con, index=False)
        outcomes.to_sql("outcomes", con, index=False)
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("as_of", as_of.isoformat()), ("seed", str(params.seed)), ("institution", params.institution),
        ])
        con.execute("""CREATE TABLE collection_actions (
            ACTION_ID INTEGER PRIMARY KEY AUTOINCREMENT, ACCT_NBR TEXT, ACTION_CD TEXT, CHANNEL TEXT,
            SCRIPT_TXT TEXT, APPROVED_BY TEXT, SOURCE_RUN_ID TEXT, RECEIVED_AT TEXT,
            EXECUTED_DT TEXT, EXECUTION_STATUS TEXT, RPC_IND INTEGER)""")
        con.execute("""CREATE TABLE day_log (
            BUSINESS_DT TEXT PRIMARY KEY, CLOSED_AT TEXT, ENGINE_ACTIONS INTEGER, EXECUTED INTEGER,
            NEW_DELINQUENT INTEGER, QUEUE_ROWS INTEGER, MATURED_OUTCOMES INTEGER)""")
        for t, col in [("accounts", "ACCT_NBR"), ("risk_scores", "ACCT_NBR"), ("members", "CIF_KEY"),
                       ("consents", "CIF_KEY"), ("collections_queue_history", "ACCT_NBR"), ("outcomes", "ACCT_NBR")]:
            con.execute(f"CREATE INDEX idx_{t}_{col} ON {t}({col})")

    return {
        "as_of": as_of.isoformat(), "members": n_m, "accounts": n_a, "delinquent_today": int(live.sum()),
        "queue_rows": len(queue), "matured_outcomes": len(outcomes),
        "segments_today": pd.Series(segment[live]).value_counts().to_dict(),
    }


def _ensure_action_columns(con: sqlite3.Connection) -> None:
    """Banks seeded before day-by-day simulation lack the execution columns."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(collection_actions)")}
    for name, typ in [("EXECUTED_DT", "TEXT"), ("EXECUTION_STATUS", "TEXT"), ("RPC_IND", "INTEGER")]:
        if name not in cols:
            con.execute(f"ALTER TABLE collection_actions ADD COLUMN {name} {typ}")
    con.execute("""CREATE TABLE IF NOT EXISTS day_log (
        BUSINESS_DT TEXT PRIMARY KEY, CLOSED_AT TEXT, ENGINE_ACTIONS INTEGER, EXECUTED INTEGER,
        NEW_DELINQUENT INTEGER, QUEUE_ROWS INTEGER, MATURED_OUTCOMES INTEGER)""")


def advance_day(out_dir: Path) -> dict:
    """Close today's business day and open tomorrow.

    Released engine actions are executed on their accounts; the rest of the queue follows the legacy
    process; a few cured accounts fall behind again (steady inflow); outcomes that have now matured
    are published. The date moves forward by one day.
    """
    st, rng, meta = _load_state(out_dir)
    as_of = date.fromisoformat(meta["as_of"])
    d = int(meta["history_days"])
    db_path = out_dir / "bank.db"
    index_of = {a: i for i, a in enumerate(st["acct"].tolist())}

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        _ensure_action_columns(con)
        pending = [dict(r) for r in con.execute(
            "SELECT ACTION_ID, ACCT_NBR, ACTION_CD FROM collection_actions WHERE EXECUTED_DT IS NULL ORDER BY ACTION_ID")]
    forced: dict[int, str] = {}
    action_ids: dict[int, list[int]] = {}
    for p in pending:  # one action per account per day: the latest approval wins
        i = index_of.get(p["ACCT_NBR"])
        if i is not None:
            forced[i] = p["ACTION_CD"]
            action_ids.setdefault(i, []).append(p["ACTION_ID"])

    # steady inflow: some cured accounts fall behind again, at the rate the history window had
    candidates = np.where(st["status"] == "CURED")[0]
    n_new = min(len(candidates), int(rng.poisson(len(st["acct"]) / (meta["initial_history_days"] + 90))))
    newly = rng.choice(candidates, n_new, replace=False) if n_new else np.array([], dtype=int)
    st["status"][newly] = "DELINQUENT"
    st["dpd"][newly] = 30.0
    st["resolve_day"][newly] = -1

    queue_rows, outcome_rows, hist_truth, executed = [], [], [], []
    style = {"score_style": meta.get("score_style", "grade"), "score_horizon_days": meta.get("score_horizon_days", 365)}
    _simulate_day(st, d, as_of, rng, queue_rows, outcome_rows, hist_truth, forced=forced, executed=executed, **style)
    new_as_of = as_of + timedelta(days=1)
    history_days = d + 1

    sealed = out_dir / "sealed"
    pending_out = pd.concat([pd.read_parquet(sealed / "pending_outcomes.parquet"), pd.DataFrame(outcome_rows)],
                            ignore_index=True)
    matured = pd.to_datetime(pending_out["QUEUE_DT"]) <= pd.Timestamp(new_as_of - timedelta(days=30))
    pending_out[~matured].to_parquet(sealed / "pending_outcomes.parquet", index=False)
    pd.DataFrame(hist_truth).to_csv(sealed / "truth_history.csv", mode="a", header=False, index=False)

    dynamic, scores, truth_today, live = _views(st, history_days, new_as_of, rng, **style)
    truth_today.to_csv(sealed / "truth_today.csv", index=False)
    truth_today.to_csv(sealed / f"truth_{new_as_of.isoformat()}.csv", index=False)

    closed_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as con:
        pd.DataFrame(queue_rows).to_sql("collections_queue_history", con, index=False, if_exists="append")
        pending_out[matured].to_sql("outcomes", con, index=False, if_exists="append")
        accounts = pd.read_sql("SELECT * FROM accounts ORDER BY ACCT_NBR", con)
        for col in dynamic.columns:
            accounts[col] = dynamic[col].to_numpy()
        consents = pd.read_sql("SELECT * FROM consents ORDER BY CIF_KEY", con).drop(columns=["CONTACTS_7D", "LAST_RPC_DT"])
        queue = pd.read_sql("SELECT QUEUE_DT, CIF_KEY, ACTION_CD, RPC_IND FROM collections_queue_history", con)
        consents = _contacts(consents, queue, new_as_of)
        for name, frame in [("accounts", accounts), ("consents", consents),
                            ("risk_scores", scores)]:
            con.execute(f"DELETE FROM {name}")
            frame.to_sql(name, con, index=False, if_exists="append")
        for e in executed:
            for aid in action_ids.get(e["idx"], []):
                con.execute("UPDATE collection_actions SET EXECUTED_DT = ?, EXECUTION_STATUS = ?, RPC_IND = ? WHERE ACTION_ID = ?",
                            (as_of.isoformat(), e["outcome"], e["rpc"], aid))
        con.execute("INSERT OR REPLACE INTO day_log VALUES (?,?,?,?,?,?,?)",
                    (as_of.isoformat(), closed_at, len(pending), sum(e["outcome"] == "executed" for e in executed),
                     int(n_new), len(queue_rows), int(matured.sum())))
        con.execute("UPDATE meta SET value = ? WHERE key = 'as_of'", (new_as_of.isoformat(),))

    _save_state(out_dir, st, rng, {**meta, "as_of": new_as_of.isoformat(), "history_days": history_days})
    return {"closed_business_date": as_of.isoformat(), "as_of": new_as_of.isoformat(),
            "engine_actions_received": len(pending), "engine_actions_executed": sum(e["outcome"] == "executed" for e in executed),
            "not_executed": [e["outcome"] for e in executed if e["outcome"] != "executed"],
            "new_delinquent_accounts": int(n_new), "queue_rows_added": len(queue_rows),
            "outcomes_matured": int(matured.sum()), "delinquent_today": int(live.sum())}
