"""Onboarding a new bank: what Last Mile needs, what the bank's data looks like, and whether a proposal fits.

The Onboarding Agent's LLM decides how a bank's API and columns map onto Last Mile. This module is everything
around that decision that must not be left to judgement:
  * CANONICAL - the fields the engine needs from each feed, with their meaning and expected kind
  * profile   - a privacy-safe description of sampled columns: types, ranges, value shapes. Names, phones,
                emails and ids are never passed on - only their shape (e.g. "Aaaaa", "+9-999-9999-9999")
  * check     - validates a proposed mapping against the columns that really exist and the values they hold
  * build     - turns an accepted proposal into an Institution Pack, using a reference institution for the
                business rules (LGD table, capacity, eligibility) that API data cannot reveal
"""

from __future__ import annotations

import re
from copy import deepcopy

import pandas as pd

from lastmile.config.schema import InstitutionPack

FEED_ROLES = {
    "scores": "The bank's own risk prediction per account (the predictive model's output).",
    "accounts": "Loan / card accounts: balance, days overdue, product, status and risk-relevant history.",
    "members": "Customer master: identity, tenure and do-not-contact flags. Contains personal data.",
    "consents": "Contact permissions per customer and recent contact counts.",
    "queue_history": "Past collections work per account per day: the account state then and the action taken.",
    "outcomes": "Whether each past worked account caught up within 30 days.",
}

# feed -> canonical field -> (meaning, kind, required). kind: id | text | code | number | integer | flag | ratio | date | score
CANONICAL: dict[str, dict[str, tuple[str, str, bool]]] = {
    "scores": {
        "account_id": ("account identifier, same as in accounts", "id", True),
        "risk_grade": ("the risk model's output for the account: a grade letter or a default probability", "score", True),
        "score_date": ("date the score was produced", "date", True),
        "model_version": ("name or version of the risk model", "text", False),
        "pd_horizon_days": ("horizon of the score in days (e.g. 365 or 180)", "integer", False),
    },
    "accounts": {
        "account_id": ("account identifier", "id", True),
        "member_id": ("customer identifier of the account holder", "id", True),
        "product": ("product code: AUTO, PERS or CARD", "code", True),
        "secured": ("1 if the loan is secured (e.g. by a vehicle), else 0", "flag", True),
        "orig_amt": ("original loan amount or credit limit", "number", True),
        "curr_bal": ("current balance owed", "number", True),
        "dpd": ("days past due today", "integer", True),
        "min_payment": ("minimum payment due", "number", True),
        "status": ("account status: DELINQUENT, CURED or CHARGED_OFF", "code", True),
        "litigation": ("1 if the account is in litigation", "flag", True),
        "fraud_hold": ("1 if the account is on fraud hold", "flag", True),
        "hardship_active": ("1 if a hardship plan is active now", "flag", True),
        "hardship_plans_12m": ("number of hardship plans in the last 12 months", "integer", True),
        "ptp_kept_rate_12m": ("share of promises to pay that were kept in the last 12 months (0-1)", "ratio", True),
        "complaints_12m": ("number of complaints in the last 12 months", "integer", True),
        "pmts_missed_12m": ("payments missed in the last 12 months", "integer", True),
        "mos_since_last_pmt": ("months since the last payment", "integer", True),
    },
    "members": {
        "member_id": ("customer identifier", "id", True),
        "first_name": ("given name (personal data)", "text", True),
        "last_name": ("family name (personal data)", "text", False),
        "phone": ("phone number (personal data)", "text", False),
        "email": ("email address (personal data)", "text", False),
        "language": ("preferred language code", "code", True),
        "tenure_mos": ("months as a customer", "integer", True),
        "direct_deposit": ("1 if salary or income is paid into the bank", "flag", True),
        "deceased": ("1 if the customer is deceased", "flag", True),
        "bankruptcy": ("1 if the customer is in bankruptcy", "flag", True),
        "cease_desist": ("1 if the customer asked to stop contact (cease and desist)", "flag", True),
    },
    "consents": {
        "member_id": ("customer identifier", "id", True),
        "phone_consent": ("1 if calls are allowed", "flag", True),
        "sms_consent": ("1 if text messages are allowed", "flag", True),
        "email_consent": ("1 if email is allowed", "flag", False),
        "dnc": ("1 if on a do-not-call list", "flag", True),
        "contacts_7d": ("number of contacts in the last 7 days", "integer", True),
        "last_rpc_date": ("date the customer was last reached in person", "date", False),
    },
    "queue_history": {
        "queue_date": ("date the account was worked", "date", True),
        "account_id": ("account identifier", "id", True),
        "member_id": ("customer identifier", "id", True),
        "dpd_at_t": ("days past due on that date", "integer", True),
        "balance_at_t": ("balance on that date", "number", True),
        "risk_grade_at_t": ("the risk model's output on that date (same kind as today's score)", "score", True),
        "pmts_missed_12m_at_t": ("payments missed in the 12 months before that date", "integer", True),
        "mos_since_last_pmt_at_t": ("months since last payment on that date", "integer", True),
        "action": ("treatment given: SMS, CALL, PLAN, HARDSHIP or NONE", "code", True),
        "rpc": ("1 if the customer was reached", "flag", True),
    },
    "outcomes": {
        "account_id": ("account identifier", "id", True),
        "queue_date": ("date the account was worked (joins to queue history)", "date", True),
        "cured_30d": ("1 if the account caught up within 30 days", "flag", True),
        "amt_paid_30d": ("amount paid within 30 days", "number", False),
    },
}
PII_CANDIDATES = {"first_name", "last_name", "phone", "email"}


# ------------------------------------------------------------------------------------------ profile
def _shape(v: str) -> str:
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", v)))[:40]


def profile_column(s: pd.Series, max_categories: int = 12) -> dict:
    """Describe a column without revealing any individual's value."""
    non_null = s.dropna()
    out: dict = {"null_share": round(float(s.isna().mean()), 3) if len(s) else 0.0,
                 "distinct": int(non_null.nunique())}
    num = pd.to_numeric(non_null, errors="coerce")
    if len(non_null) and num.notna().mean() > 0.98:
        num = num.dropna()
        ints = bool((num == num.round()).all())
        out.update({"kind": "integer" if ints else "number", "min": round(float(num.min()), 4),
                    "max": round(float(num.max()), 4), "mean": round(float(num.mean()), 4),
                    "share_between_0_and_1": round(float(((num >= 0) & (num <= 1)).mean()), 3)})
        if out["distinct"] <= max_categories:
            out["values"] = sorted(num.unique().tolist())[:max_categories]
        return out
    text = non_null.astype(str)
    dates = pd.to_datetime(text, errors="coerce", format="%Y-%m-%d")
    if len(text) and dates.notna().mean() > 0.98:
        out.update({"kind": "date", "min": str(dates.min().date()), "max": str(dates.max().date())})
        return out
    out.update({"kind": "text", "length": [int(text.str.len().min()), int(text.str.len().max())] if len(text) else [0, 0]})
    personal_looking = text.str.contains(r"@|\+?\d[\d\- ]{6,}", regex=True).mean() > 0.2 if len(text) else False
    if out["distinct"] <= max_categories and not personal_looking:
        out["values"] = sorted(text.unique().tolist())[:max_categories]     # codes: products, statuses, languages
    else:
        out["shapes"] = sorted(text.map(_shape).value_counts().head(3).index.tolist())
    return out


def profile_frame(df: pd.DataFrame) -> dict:
    return {"rows_sampled": int(len(df)), "columns": {str(c): profile_column(df[c]) for c in df.columns}}


# -------------------------------------------------------------------------------------------- check
KIND_OK = {
    "id": {"text", "integer"}, "text": {"text"}, "code": {"text"}, "number": {"number", "integer"},
    "integer": {"integer"}, "flag": {"integer"}, "ratio": {"number", "integer"}, "date": {"date"},
    "score": {"text", "number"},
}


def check_proposal(proposal: dict, profiles: dict[str, dict]) -> list[str]:
    """Problems with a proposed mapping, judged against the columns and values actually sampled. Empty means it fits."""
    problems: list[str] = []
    fmap = proposal.get("field_map") or {}
    for feed, fields in CANONICAL.items():
        cols = (profiles.get(feed) or {}).get("columns", {})
        if feed not in profiles:
            problems.append(f"no endpoint was chosen for the '{feed}' feed")
            continue
        mapped = fmap.get(feed) or {}
        used: dict[str, str] = {}
        for canon, (_, kind, required) in fields.items():
            src = mapped.get(canon)
            if not src:
                if required:
                    problems.append(f"{feed}.{canon} is required but not mapped")
                continue
            if src not in cols:
                problems.append(f"{feed}.{canon} -> '{src}' does not exist; columns are {sorted(cols)}")
                continue
            if src in used:
                problems.append(f"{feed}: column '{src}' is mapped to both {used[src]} and {canon}")
            used[src] = canon
            col = cols[src]
            if col["kind"] not in KIND_OK[kind]:
                problems.append(f"{feed}.{canon} expects {kind} values but '{src}' holds {col['kind']}")
            elif kind == "flag" and not set(col.get("values", [0, 1])) <= {0, 1}:
                problems.append(f"{feed}.{canon} expects 0/1 but '{src}' holds {col.get('values')}")
            elif kind == "ratio" and col.get("share_between_0_and_1", 1) < 0.99:
                problems.append(f"{feed}.{canon} expects a share between 0 and 1 but '{src}' ranges {col.get('min')}..{col.get('max')}")
        for extra in set(mapped) - set(fields):
            problems.append(f"{feed}.{extra} is not a field Last Mile uses")

    score = proposal.get("score") or {}
    kind = score.get("input_type")
    if kind not in ("band", "probability"):
        problems.append("score.input_type must be 'band' or 'probability'")
    for feed, canon in (("scores", "risk_grade"), ("queue_history", "risk_grade_at_t")):
        src = (fmap.get(feed) or {}).get(canon)
        col = (profiles.get(feed) or {}).get("columns", {}).get(src) if src else None
        if not col:
            continue
        if kind == "probability" and (col["kind"] not in ("number",) or col.get("share_between_0_and_1", 0) < 0.999):
            problems.append(f"score is proposed as a probability but {feed}.{src} is {col['kind']} "
                            f"ranging {col.get('min')}..{col.get('max')}")
        if kind == "band" and col["kind"] != "text":
            problems.append(f"score is proposed as grades but {feed}.{src} holds {col['kind']} values")
    horizon = score.get("source_horizon_days")
    if not isinstance(horizon, int) or horizon <= 0:
        problems.append("score.source_horizon_days must be a positive whole number of days")
    hsrc = (fmap.get("scores") or {}).get("pd_horizon_days")
    hcol = (profiles.get("scores") or {}).get("columns", {}).get(hsrc) if hsrc else None
    if hcol and hcol.get("values") and isinstance(horizon, int) and horizon not in hcol["values"]:
        problems.append(f"score.source_horizon_days is {horizon} but the bank's own horizon column says {hcol['values']}")
    if kind == "band" and not score.get("band_to_pd"):
        problems.append("grade scores need band_to_pd: the default probability each grade stands for")

    pii = set(proposal.get("pii_fields") or [])
    if "first_name" not in pii:
        problems.append("pii_fields must include first_name (scripts use it; it must stay in the identity vault)")
    if pii - PII_CANDIDATES:
        problems.append(f"pii_fields may only name personal fields {sorted(PII_CANDIDATES)}; got {sorted(pii - PII_CANDIDATES)}")

    for key in ("health_endpoint", "catalogue_endpoint", "release_endpoint"):
        if not str((proposal.get("endpoints") or {}).get(key) or "").startswith("/"):
            problems.append(f"endpoints.{key} must be a path starting with /")
    return problems


# -------------------------------------------------------------------------------------------- build
def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "new_bank"


def build_institution(proposal: dict, reference: InstitutionPack, institution_name: str, base_url: str) -> dict:
    """An Institution Pack for the new bank: the proposal for everything about its data, the reference institution
    for business rules the data cannot tell us (they are listed for review in the proposal)."""
    ref = reference.model_dump(mode="json")
    inst_id = slug(institution_name)
    ep = proposal["endpoints"]
    feeds = proposal["feeds"]
    score = proposal["score"]
    pack = deepcopy(ref)
    pack["institution"] = {**ref["institution"], "id": inst_id, "name": institution_name,
                           "type": proposal.get("institution_type") or ref["institution"]["type"],
                           "customer_noun": proposal.get("customer_noun") or "customer"}
    pack["source"] = {"base_url_env": f"{inst_id.upper()}_API_URL", "default_base_url": base_url.rstrip("/"),
                      "page_size": ref["source"]["page_size"], "feeds": {f: feeds[f] for f in CANONICAL},
                      "release_endpoint": ep["release_endpoint"], "health_endpoint": ep["health_endpoint"],
                      "catalogue_endpoint": ep["catalogue_endpoint"], "simulation_endpoint": ep.get("simulation_endpoint")}
    pack["field_map"] = {f: {k: v for k, v in (proposal["field_map"].get(f) or {}).items() if v} for f in CANONICAL}
    pack["pii_fields"] = sorted(set(proposal["pii_fields"]))
    pack["pd_calibration"] = {"input_type": score["input_type"], "band_to_pd": score.get("band_to_pd") or {},
                              "source_horizon_days": int(score["source_horizon_days"]), "horizon_method": "constant_hazard"}
    dq = pack["data_quality"]
    dq["valid_grades"] = sorted(score.get("band_to_pd") or {}) if score["input_type"] == "band" else []
    dq["required_fields"] = {f: [c for c in cols if c in pack["field_map"].get(f, {})] for f, cols in dq["required_fields"].items()}
    return pack


# Sections of an Institution Pack that API data cannot reveal: taken from the reference institution, to confirm with the bank.
REFERENCE_SECTIONS = {
    "lgd_table": "Share of the balance lost on default, by product and days past due",
    "capacity": "Default team size, shift length, daily text and hardship limits",
    "contact_policy": "How often a customer may be contacted",
    "suppression": "Flags that block all contact",
    "eligibility": "Who may receive each action",
    "data_quality": "Freshness, volume and completeness thresholds",
    "escalation": "What needs a senior reviewer",
    "approval": "Reason codes for changing or rejecting a recommendation",
}
