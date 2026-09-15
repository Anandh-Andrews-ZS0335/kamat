"""Persuadable / sure thing / lost cause / sleeping dog - from estimated baseline and uplift."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lastmile.config.schema import SegmentThresholds

LABELS = {"persuadable": "Persuadable", "sure_thing": "Sure thing", "lost_cause": "Lost cause",
          "sleeping_dog": "Sleeping dog", "uncertain": "Uncertain"}


def assign(p0: np.ndarray, best_uplift: np.ndarray, best_lower: np.ndarray, worst_contact_uplift: np.ndarray,
           th: SegmentThresholds) -> pd.Series:
    seg = np.full(len(p0), "uncertain", dtype=object)
    seg[(p0 >= th.sure_thing_min_p0) & (best_uplift < th.persuadable_min_uplift)] = "sure_thing"
    seg[(p0 <= th.lost_cause_max_p0) & (best_uplift < th.persuadable_min_uplift)] = "lost_cause"
    seg[(best_uplift >= th.persuadable_min_uplift) & (best_lower > 0)] = "persuadable"
    seg[(best_uplift < th.persuadable_min_uplift) & (worst_contact_uplift <= th.sleeping_dog_max_uplift)] = "sleeping_dog"
    return pd.Series(seg)
