"""Model-level data.

Two shapes:
  * training set - one row per decision moment (account, t), features strictly before t, label after t
  * online set   - one row per account today, the same features computed as of today

Both go through the same contact-history function, so the model sees identical definitions.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from lastmile.config.schema import ResolvedConfig
from lastmile.features.calibration import calibrate
from lastmile.features.lgd import lookup_lgd


def _days(s: pd.Series) -> np.ndarray:
    return (pd.to_datetime(s).values.astype("datetime64[D]").astype("int64"))


def contact_history_features(queries: pd.DataFrame, history: pd.DataFrame, control_value: str) -> pd.DataFrame:
    """For each (account_token, t): contacts in [t-30, t), RPC rate in [t-90, t), days since last contact.

    Strictly before t - an action on day t never counts towards its own features.
    """
    contacts = history[history["action"] != control_value]
    by_acct = {k: (g["_d"].to_numpy(), g["rpc"].to_numpy(float))
               for k, g in contacts.assign(_d=_days(contacts["queue_date"])).sort_values("_d").groupby("account_token")}
    q_days = _days(queries["t"])
    c30 = np.zeros(len(queries))
    rpc90 = np.zeros(len(queries))
    since = np.full(len(queries), 999.0)
    for i, (tok, t) in enumerate(zip(queries["account_token"].to_numpy(), q_days, strict=True)):
        if tok not in by_acct:
            continue
        d, rpc = by_acct[tok]
        hi = np.searchsorted(d, t, side="left")            # strictly before t
        lo30 = np.searchsorted(d, t - 30, side="left")
        lo90 = np.searchsorted(d, t - 90, side="left")
        c30[i] = hi - lo30
        if hi > lo90:
            rpc90[i] = rpc[lo90:hi].mean()
        if hi > 0:
            since[i] = t - d[hi - 1]
    return pd.DataFrame({"contacts_30d": c30, "rpc_rate_90d": rpc90, "days_since_last_contact": since},
                        index=queries.index)


def _static(canon: dict[str, pd.DataFrame]) -> pd.DataFrame:
    acc = canon["accounts"]
    mem = canon["members"][["member_token", "tenure_mos", "direct_deposit", "deceased", "bankruptcy", "cease_desist"]]
    member_accounts = acc[acc["status"] == "DELINQUENT"].groupby("member_token").size().rename("member_accounts")
    s = acc.merge(mem, on="member_token", how="left").merge(member_accounts, on="member_token", how="left")
    s["member_accounts"] = s["member_accounts"].fillna(1).astype(int)
    s["product_auto"] = (s["product"] == "AUTO").astype(int)
    s["product_card"] = (s["product"] == "CARD").astype(int)
    return s


def join_portfolio(canon: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    """Today's decision population: delinquent, scored accounts joined with member, consent and suppression data."""
    s = _static(canon)
    live = s[s["status"] == "DELINQUENT"]
    scored = live.merge(canon["scores"][["account_token", "risk_grade", "score_date", "model_version", "pd_horizon_days"]],
                        on="account_token", how="inner")
    unscored = int(len(live) - len(scored))
    port = scored.merge(canon["consents"][["member_token", "phone_consent", "sms_consent", "email_consent", "dnc",
                                           "contacts_7d", "last_rpc_date"]], on="member_token", how="left")
    for c in ["phone_consent", "sms_consent", "dnc", "contacts_7d"]:
        port[c] = port[c].fillna(0).astype(int)
    port["exposure"] = port["curr_bal"].astype(float)
    return port.reset_index(drop=True), {"portfolio_rows": int(len(port)), "unscored_delinquent": unscored,
                                         "members": int(port["member_token"].nunique()),
                                         "multi_account_members": int((port.groupby("member_token").size() > 1).sum())}


def attach_pd(port: pd.DataFrame, cfg: ResolvedConfig) -> tuple[pd.DataFrame, dict]:
    cal = calibrate(port["risk_grade"], cfg.institution.pd_calibration, cfg.scenario.objective.horizon_days)
    out = port.copy()
    out["pd_12m"] = cal["pd_source"].to_numpy()
    out["pd_h"] = cal["pd_h"].to_numpy()
    by_grade = out.groupby("risk_grade").agg(accounts=("account_token", "size"), pd_12m=("pd_12m", "first"),
                                             pd_h=("pd_h", "first")).reset_index()
    return out, {"horizon": f"{cfg.institution.pd_calibration.source_horizon_days}d -> "
                            f"{cfg.scenario.objective.horizon_days}d (constant hazard)",
                 "by_grade": by_grade.round(4).to_dict("records")}


def attach_lgd(port: pd.DataFrame, cfg: ResolvedConfig) -> tuple[pd.DataFrame, dict]:
    out = port.copy()
    out["lgd"], defaulted = lookup_lgd(out, cfg.institution.lgd_table)
    return out, {"default_lgd_applied": defaulted, "mean_lgd": round(float(out["lgd"].mean()), 3),
                 "by_product": out.groupby("product")["lgd"].mean().round(3).to_dict()}


def build_portfolio(canon: dict[str, pd.DataFrame], cfg: ResolvedConfig) -> tuple[pd.DataFrame, dict]:
    port, info = join_portfolio(canon)
    port, _ = attach_pd(port, cfg)
    port, lgd_info = attach_lgd(port, cfg)
    return port, {**info, "lgd_default_applied": lgd_info["default_lgd_applied"]}


def build_training_set(canon: dict[str, pd.DataFrame], cfg: ResolvedConfig, as_of: date) -> tuple[pd.DataFrame, dict]:
    c = cfg.scenario.causal
    hist = canon["queue_history"]
    matured_cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=c.label_maturity_days)
    rows = hist.merge(canon["outcomes"][["account_token", "queue_date", c.outcome_column]],
                      on=["account_token", "queue_date"], how="inner")
    rows = rows[pd.to_datetime(rows["queue_date"]) <= matured_cutoff].copy()
    immature = int(len(hist) - len(rows))
    rows["t"] = rows["queue_date"]
    rows = rows.merge(_static(canon)[["account_token", "product_auto", "product_card", "orig_amt", "ptp_kept_rate_12m",
                                      "complaints_12m", "tenure_mos", "direct_deposit", "member_accounts"]],
                      on="account_token", how="left")
    cal = calibrate(rows["risk_grade_at_t"], cfg.institution.pd_calibration, cfg.scenario.objective.horizon_days)
    rows["risk_pd_at_t"] = cal["pd_h"].to_numpy()
    rows = pd.concat([rows, contact_history_features(rows, hist, c.control_value)], axis=1)
    rows = rows.rename(columns={c.treatment_column: "T", c.outcome_column: "Y"})
    keep = ["account_token", "t", *c.features, "T", "Y"]
    out = rows[keep].reset_index(drop=True)
    return out, {"training_rows": int(len(out)), "excluded_immature_labels": immature,
                 "label_maturity_cutoff": str(matured_cutoff.date()),
                 "treatment_counts": out["T"].value_counts().to_dict()}


def build_online_features(portfolio: pd.DataFrame, canon: dict[str, pd.DataFrame], cfg: ResolvedConfig,
                          as_of: date) -> pd.DataFrame:
    c = cfg.scenario.causal
    q = portfolio[["account_token"]].copy()
    q["t"] = pd.Timestamp(as_of)
    hist_feats = contact_history_features(q, canon["queue_history"], c.control_value)
    x = pd.DataFrame({
        "account_token": portfolio["account_token"],
        "dpd_at_t": portfolio["dpd"].astype(float),
        "balance_at_t": portfolio["curr_bal"].astype(float),
        "risk_pd_at_t": portfolio["pd_h"].astype(float),
        "pmts_missed_12m_at_t": portfolio["pmts_missed_12m"].astype(float),
        "mos_since_last_pmt_at_t": portfolio["mos_since_last_pmt"].astype(float),
        "product_auto": portfolio["product_auto"], "product_card": portfolio["product_card"],
        "orig_amt": portfolio["orig_amt"].astype(float), "ptp_kept_rate_12m": portfolio["ptp_kept_rate_12m"],
        "complaints_12m": portfolio["complaints_12m"],
        "tenure_mos": portfolio["tenure_mos"], "direct_deposit": portfolio["direct_deposit"],
        "member_accounts": portfolio["member_accounts"],
    })
    x = pd.concat([x, hist_feats], axis=1)
    return x[["account_token", *c.features]]
