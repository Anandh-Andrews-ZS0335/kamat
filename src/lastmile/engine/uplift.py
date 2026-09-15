"""T-learner uplift model.

One outcome model per action arm and one for control; uplift(x, a) = P(cure | x, a) - P(cure | x, none).
Each arm is a bag of heavily regularised boosted trees fitted on bootstrap resamples: the point estimate is
the bag mean and the interval comes from the spread across the bag. Bagging matters here - differencing two
noisy classifiers inflates variance, and an optimiser then selects the noise (the winner's curse). Measured
against the synthetic bank's sealed truth, bagging roughly doubled correlation with true uplift. The engine never needs EconML for this - a T-learner is
two classifiers and a subtraction - and it keeps the dependency tree small.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier


@dataclass
class UpliftEstimate:
    point: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    std: np.ndarray


def _learner(seed: int) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=80, learning_rate=0.05, max_leaf_nodes=8,
                                          min_samples_leaf=150, l2_regularization=1.0, random_state=seed)


class TLearner:
    def __init__(self, actions: list[str], control: str, features: list[str], bootstrap_rounds: int = 6, seed: int = 0):
        self.actions, self.control, self.features = actions, control, features
        self.bootstrap_rounds, self.seed = bootstrap_rounds, seed
        self.models: dict[str, list[HistGradientBoostingClassifier]] = {}
        self.arm_rows: dict[str, int] = {}

    def fit(self, X: pd.DataFrame, T: pd.Series, Y: pd.Series) -> TLearner:
        rng = np.random.default_rng(self.seed)
        Xv = X[self.features].astype(float)
        for arm in [self.control, *self.actions]:
            m = (T == arm).to_numpy()
            xa, ya = Xv[m].to_numpy(), Y[m].astype(int).to_numpy()
            self.arm_rows[arm] = int(m.sum())
            if len(np.unique(ya)) < 2:
                raise ValueError(f"arm {arm} has a single outcome class; cannot fit")
            fits = []
            for b in range(max(2, self.bootstrap_rounds)):
                idx = rng.integers(0, len(xa), len(xa))
                fits.append(_learner(self.seed + b + 1).fit(xa[idx], ya[idx]))
            self.models[arm] = fits
        return self

    def _p(self, arm: str, X: np.ndarray) -> np.ndarray:
        return np.vstack([f.predict_proba(X)[:, 1] for f in self.models[arm]])  # (B, n)

    def p0(self, X: pd.DataFrame) -> np.ndarray:
        return self._p(self.control, X[self.features].astype(float).to_numpy()).mean(axis=0)

    def effect(self, X: pd.DataFrame, action: str) -> UpliftEstimate:
        xv = X[self.features].astype(float).to_numpy()
        diff = self._p(action, xv) - self._p(self.control, xv)
        point = diff.mean(axis=0)
        std = diff.std(axis=0, ddof=1)
        return UpliftEstimate(point=point, lower=point - 1.645 * std, upper=point + 1.645 * std, std=std)

    def save(self, path: Path) -> None:
        path.write_bytes(pickle.dumps(self))

    @staticmethod
    def load(path: Path) -> TLearner:
        return pickle.loads(path.read_bytes())
