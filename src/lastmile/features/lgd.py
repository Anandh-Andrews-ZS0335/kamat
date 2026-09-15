"""Loss given default from the institution's recovery table: (product, secured, dpd band) -> LGD."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lastmile.config.schema import LgdTable


def lookup_lgd(df: pd.DataFrame, table: LgdTable) -> tuple[np.ndarray, int]:
    lgd = np.full(len(df), np.nan)
    product = df["product"].to_numpy()
    secured = df["secured"].astype(int).to_numpy()
    dpd = df["dpd"].astype(float).to_numpy()
    for row in table.rows:
        m = (product == row.product) & (secured == row.secured) & (dpd >= row.dpd_min) & (dpd <= row.dpd_max)
        lgd[m & np.isnan(lgd)] = row.lgd
    defaulted = int(np.isnan(lgd).sum())
    lgd[np.isnan(lgd)] = table.default
    return lgd, defaulted
