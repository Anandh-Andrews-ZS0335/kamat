"""Agent base and the shared run context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from lastmile.agents.registry import Registry
from lastmile.agents.trace import Tracer
from lastmile.config.schema import ResolvedConfig
from lastmile.ingest.bank_client import BankClient


@dataclass
class RunContext:
    run_id: str
    run_name: str
    tracer: Tracer
    bank: BankClient | None = None
    cfg: ResolvedConfig | None = None
    as_of: date | None = None
    llm: Any = None
    llm_mode: str = "template"
    data_dir: Any = None
    state: dict[str, Any] = field(default_factory=dict)       # frames and results passed between stages
    artifacts: dict[str, list] = field(default_factory=dict)  # stage -> artifact descriptors


class Agent:
    name = "Agent"
    title = ""
    responsibility = ""
    modules: tuple[str, ...] = ()

    def __init__(self, ctx: RunContext, registry: Registry):
        self.ctx, self.registry = ctx, registry

    def use(self, tool: str, input_summary=None, **kwargs):
        return self.registry.call(self, tool, input_summary=input_summary, **kwargs)

    def handoff(self, to: Agent | str, task: str) -> None:
        target = to if isinstance(to, str) else to.name
        self.ctx.tracer.event("handoff", self.name, target=target, message=task)

    def note(self, message: str, **data) -> None:
        """A decision the agent took by rule - recorded so the reasoning is visible, not just the calls."""
        self.ctx.tracer.event("decision", self.name, message=message, output=data or None)

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "title": cls.title, "responsibility": cls.responsibility, "modules": list(cls.modules)}
