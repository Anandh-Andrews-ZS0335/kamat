"""Risk grade -> probability, and 12-month probability -> decision-horizon probability."""

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


def calibrate(grades: pd.Series, cal: PdCalibration, horizon_days: int) -> pd.DataFrame:
    pd12 = band_to_pd(grades, cal)
    return pd.DataFrame({"pd_source": pd12, "pd_h": convert_horizon(pd12, cal.source_horizon_days, horizon_days)},
                        index=grades.index)
