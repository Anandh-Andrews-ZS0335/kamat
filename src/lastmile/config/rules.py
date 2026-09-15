"""Structured policy rules: {field, op, value}. Validated by Pydantic, evaluated as vectorised masks.

No string parsing and no eval(). A missing value never satisfies a rule, so gaps in the data
fail closed rather than silently allowing an action.
"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

Op = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"]
_SYMBOL = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "in": "in", "not_in": "not in"}


class Rule(BaseModel):
    field: str
    op: Op
    value: Any = None

    def mask(self, df: pd.DataFrame) -> pd.Series:
        if self.field not in df.columns:
            raise KeyError(f"Rule references unknown field '{self.field}'")
        col = df[self.field]
        present = col.notna()
        if self.op == "eq":
            m = col == self.value
        elif self.op == "ne":
            m = col != self.value
        elif self.op == "gt":
            m = col > self.value
        elif self.op == "gte":
            m = col >= self.value
        elif self.op == "lt":
            m = col < self.value
        elif self.op == "lte":
            m = col <= self.value
        elif self.op == "in":
            m = col.isin(list(self.value))
        else:
            m = ~col.isin(list(self.value))
        return (m.fillna(False).astype(bool) & present).rename(self.describe())

    def describe(self) -> str:
        return f"{self.field} {_SYMBOL[self.op]} {self.value}"


class RuleSet(BaseModel):
    all: list[Rule] = []
    any: list[Rule] = []

    def rule_masks(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {r.describe(): r.mask(df) for r in [*self.all, *self.any]}

    def mask(self, df: pd.DataFrame) -> pd.Series:
        m = pd.Series(True, index=df.index)
        for r in self.all:
            m &= r.mask(df)
        if self.any:
            anym = pd.Series(False, index=df.index)
            for r in self.any:
                anym |= r.mask(df)
            m &= anym
        return m

    def failing(self, df: pd.DataFrame) -> pd.Series:
        """Per row, the list of rule descriptions that did not pass."""
        masks = [(r.describe(), r.mask(df)) for r in self.all]
        out = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
        for desc, m in masks:
            for idx in df.index[~m]:
                out.at[idx] = [*out.at[idx], desc]
        if self.any:
            anym = pd.Series(False, index=df.index)
            for r in self.any:
                anym |= r.mask(df)
            desc = "any of: " + "; ".join(r.describe() for r in self.any)
            for idx in df.index[~anym]:
                out.at[idx] = [*out.at[idx], desc]
        return out
