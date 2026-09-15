"""Objective formulas from config, evaluated safely with numexpr over named columns."""

from __future__ import annotations

import numexpr as ne
import numpy as np
import pandas as pd

from lastmile.config.schema import formula_names


def evaluate(formula: str, frame: pd.DataFrame) -> np.ndarray:
    names = formula_names(formula)
    missing = [n for n in names if n not in frame.columns]
    if missing:
        raise KeyError(f"formula '{formula}' references columns not in frame: {missing}")
    local = {n: frame[n].astype(float).to_numpy() for n in names}
    return np.asarray(ne.evaluate(formula, local_dict=local), dtype=float)
