"""Institution field map: bank column names -> canonical names. The engine never sees bank names."""

from __future__ import annotations

import pandas as pd


class MappingError(ValueError):
    pass


def map_feed(feed: str, raw: pd.DataFrame, field_map: dict[str, str]) -> tuple[pd.DataFrame, dict]:
    missing = [src for src in field_map.values() if src not in raw.columns]
    if missing:
        raise MappingError(f"feed '{feed}' is missing mapped source columns {missing}")
    canonical = raw[list(field_map.values())].rename(columns={v: k for k, v in field_map.items()})
    report = {
        "feed": feed,
        "mapped": [{"canonical": k, "source": v} for k, v in field_map.items()],
        "unmapped_source_columns": [c for c in raw.columns if c not in field_map.values()],
        "rows": int(len(canonical)),
    }
    return canonical, report
