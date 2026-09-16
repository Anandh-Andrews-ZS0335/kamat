"""The bank's risk output -> a 12-month default probability and a decision-horizon probability.

Banks send this in different shapes, depending on their own predictive model:
  band        a grade from a scorecard (A-E), mapped through the institution's table
  probability a default probability straight from a logistic / gradient-boosted / forest model
Whatever arrives, everything downstream sees the same two numbers: pd_12m and pd_h.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lastmile.config.schema import PdCalibration


def band_to_pd(grades: pd.Series, cal: PdCalibration) -> pd.Series:
    unknown = set(grades.dropna().unique()) - set(cal.band_to_pd)
    if unknown:
        raise ValueError(f"grades without calibration: {sorted(unknown)}")
    return grades.map(cal.band_to_pd).astype(float)


def convert_horizon(pd_source: pd.Series | np.ndarray, source_days: int, target_days: int) -> np.ndarray:
    """Constant-hazard conversion: PD(t) = 1 - (1 - PD(T))^(t/T). A stated simplification."""
    p = np.clip(np.asarray(pd_source, dtype=float), 0.0, 0.9999)
    return 1.0 - np.power(1.0 - p, target_days / source_days)


def source_probability(values: pd.Series, cal: PdCalibration) -> pd.Series:
    if cal.input_type == "band":
        return band_to_pd(values, cal)
    p = pd.to_numeric(values, errors="coerce")
    bad = p.notna() & ((p < 0) | (p > 1))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} risk score(s) outside 0-1; the bank does not send probabilities")
    return p.astype(float)


def display_label(values: pd.Series, cal: PdCalibration) -> pd.Series:
    """What people see as 'the bank's risk rating': the grade itself, or the probability with its horizon."""
    if cal.input_type == "band":
        return values.astype(str)
    p = pd.to_numeric(values, errors="coerce")
    return p.map(lambda v: "n/a" if pd.isna(v) else f"{v * 100:.1f}% in {cal.source_horizon_days}d")


def calibrate(values: pd.Series, cal: PdCalibration, horizon_days: int) -> pd.DataFrame:
    src = source_probability(values, cal)
    return pd.DataFrame({"pd_source": src,
                         "pd_12m": convert_horizon(src, cal.source_horizon_days, 365),
                         "pd_h": convert_horizon(src, cal.source_horizon_days, horizon_days),
                         "label": display_label(values, cal)}, index=values.index)
