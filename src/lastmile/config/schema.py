"""Typed models for the three Layer 0 packs. A malformed YAML fails here with the field named."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from lastmile.config.rules import RuleSet

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def formula_names(formula: str) -> set[str]:
    return set(_IDENT.findall(formula))


# ----------------------------------------------------------------------------- scenario
class ScenarioMeta(BaseModel):
    id: str
    version: str
    title: str
    entity: str
    group_by: str
    decision_period: str


class Objective(BaseModel):
    currency: str
    horizon_days: int = Field(gt=0)
    expected_loss: str
    action_value: str

    @field_validator("expected_loss", "action_value")
    @classmethod
    def arithmetic_only(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_\s\.\+\-\*/\(\)]+", v):
            raise ValueError("formulas may contain only names, numbers and + - * / ( )")
        return v


class Validation(BaseModel):
    holdout_fraction: float = Field(gt=0, lt=0.6)
    min_qini: float = 0.0


class Causal(BaseModel):
    treatment_column: str
    control_value: str
    outcome_column: str
    method: str
    bootstrap_rounds: int = Field(ge=0, le=10)
    label_maturity_days: int
    features: list[str]
    exclude_features: list[str]
    validation: Validation

    @model_validator(mode="after")
    def no_excluded_feature_requested(self):
        leaked = set(self.features) & set(self.exclude_features)
        if leaked:
            raise ValueError(f"features also listed in exclude_features: {sorted(leaked)}")
        return self


class Action(BaseModel):
    id: str
    label: str
    channel: str
    cost_minutes: float = Field(ge=0)
    cost_cash: float = Field(ge=0)
    is_contact: bool
    requires: list[str] = []


class SegmentThresholds(BaseModel):
    persuadable_min_uplift: float
    sleeping_dog_max_uplift: float
    sure_thing_min_p0: float
    lost_cause_max_p0: float


class Simulation(BaseModel):
    monte_carlo_runs: int = Field(ge=100)
    des_replications: int = Field(ge=1)
    seed: int


class Explanation(BaseModel):
    must_state: list[str]


class ScenarioPack(BaseModel):
    scenario: ScenarioMeta
    objective: Objective
    causal: Causal
    actions: list[Action]
    segments: SegmentThresholds
    simulation: Simulation
    explanation: Explanation

    def action(self, action_id: str) -> Action:
        for a in self.actions:
            if a.id == action_id:
                return a
        raise KeyError(action_id)


# -------------------------------------------------------------------------- institution
class InstitutionMeta(BaseModel):
    id: str
    name: str
    type: str
    customer_noun: str
    currency: str


class Source(BaseModel):
    base_url_env: str
    default_base_url: str
    page_size: int = Field(ge=100, le=20000)
    feeds: dict[str, str]
    release_endpoint: str
    # Synthetic bank only: closes the business day on request. A real bank's day closes on its own.
    simulation_endpoint: str | None = None


class PdCalibration(BaseModel):
    input_type: str
    band_to_pd: dict[str, float]
    source_horizon_days: int
    horizon_method: str

    @field_validator("band_to_pd")
    @classmethod
    def probabilities(cls, v):
        bad = {k: p for k, p in v.items() if not 0 < p < 1}
        if bad:
            raise ValueError(f"band probabilities must be in (0,1): {bad}")
        return v


class LgdRow(BaseModel):
    product: str
    secured: int
    dpd_min: int
    dpd_max: int
    lgd: float = Field(gt=0, le=1)


class LgdTable(BaseModel):
    default: float = Field(gt=0, le=1)
    rows: list[LgdRow]


class Capacity(BaseModel):
    agents: int = Field(ge=1)
    minutes_per_agent: int = Field(ge=1)
    sms_per_day_max: int = Field(ge=0)
    hardship_slots_per_day: int = Field(ge=0)
    planning_buffer: float = Field(0.10, ge=0, lt=0.5)

    @property
    def total_minutes(self) -> int:
        return self.agents * self.minutes_per_agent

    @property
    def plannable_minutes(self) -> float:
        return self.total_minutes * (1 - self.planning_buffer)


class Collector(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    present: bool = True
    shift_minutes: int = Field(ge=0, le=720)


class Roster(BaseModel):
    """Who is working today and for how long. Entered by the collections manager, not a config pack."""
    roster_date: str
    collectors: list[Collector]
    source: str = "default"          # saved | carried_forward | default
    saved_by: str | None = None
    saved_at: str | None = None
    note: str | None = None

    @field_validator("collectors")
    @classmethod
    def unique_ids(cls, v: list[Collector]):
        ids = [c.id for c in v]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"collector ids must be unique: {dup}")
        return v

    @property
    def working(self) -> list[Collector]:
        return [c for c in self.collectors if c.present and c.shift_minutes > 0]

    @property
    def total_minutes(self) -> int:
        return sum(c.shift_minutes for c in self.working)

    @classmethod
    def default(cls, capacity: Capacity, roster_date: str) -> Roster:
        return cls(roster_date=roster_date, source="default", collectors=[
            Collector(id=f"C{i:02d}", name=f"Collector {i}", shift_minutes=capacity.minutes_per_agent)
            for i in range(1, capacity.agents + 1)])


class ContactPolicy(BaseModel):
    max_attempts_per_7d: int
    max_contacts_per_member_per_day: int


class DataQuality(BaseModel):
    max_score_age_days: int
    max_null_fraction: float
    min_rows: dict[str, int]
    required_fields: dict[str, list[str]]
    valid_grades: list[str]


class Escalation(BaseModel):
    exposure_threshold: float
    max_interval_width: float
    escalate_actions: list[str]


class Approval(BaseModel):
    reason_codes: list[str]


class InstitutionPack(BaseModel):
    institution: InstitutionMeta
    source: Source
    field_map: dict[str, dict[str, str]]
    pii_fields: list[str]
    pd_calibration: PdCalibration
    lgd_table: LgdTable
    capacity: Capacity
    contact_policy: ContactPolicy
    suppression: list[str]
    eligibility: dict[str, RuleSet]
    data_quality: DataQuality
    escalation: Escalation
    approval: Approval

    @model_validator(mode="after")
    def feeds_are_mapped(self):
        missing = set(self.source.feeds) - set(self.field_map)
        if missing:
            raise ValueError(f"feeds without a field_map: {sorted(missing)}")
        return self


# ---------------------------------------------------------------------------------- run
class RunMeta(BaseModel):
    scenario: str
    institution: str
    as_of: str | None = None
    approver_default: str = "manager.demo"


class RunConfig(BaseModel):
    run: RunMeta


class ResolvedConfig(BaseModel):
    scenario: ScenarioPack
    institution: InstitutionPack
    run: RunConfig
    config_hash: str
    files: dict[str, str]
    # Today's roster. Not part of config_hash: the packs define the rules, the roster is the day's input.
    roster: Roster | None = None

    @property
    def team(self) -> Roster:
        return self.roster or Roster.default(self.institution.capacity, self.run.run.as_of or "default")

    @property
    def total_minutes(self) -> int:
        return self.team.total_minutes

    @property
    def plannable_minutes(self) -> float:
        return self.total_minutes * (1 - self.institution.capacity.planning_buffer)

    def plannable_for(self, collector: Collector) -> float:
        return collector.shift_minutes * (1 - self.institution.capacity.planning_buffer)

    @model_validator(mode="after")
    def cross_pack_references(self):
        known = set(self.institution.eligibility)
        for a in self.scenario.actions:
            unknown = set(a.requires) - known
            if unknown:
                raise ValueError(f"action {a.id} requires unknown eligibility rules {sorted(unknown)}")
        return self
