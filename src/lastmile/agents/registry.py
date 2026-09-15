"""Tool registry with ownership.

Each tool names the module that implements it and the agent(s) accountable for it. An agent calling a
tool it does not own is refused and the refusal is traced - responsibility is enforced, not just drawn.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lastmile.agents.base import Agent


@dataclass
class Tool:
    name: str
    module: str
    owners: tuple[str, ...]
    kind: str               # tool | api | llm
    description: str
    fn: Callable = field(repr=False)


class ToolDenied(PermissionError):
    pass


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, name: str, module: str, owners: str | tuple[str, ...], description: str, kind: str = "tool"):
        owners = (owners,) if isinstance(owners, str) else owners

        def wrap(fn):
            self.tools[name] = Tool(name, module, owners, kind, description, fn)
            return fn
        return wrap

    def call(self, agent: Agent, name: str, input_summary=None, **kwargs):
        tool = self.tools[name]
        tracer = agent.ctx.tracer
        if agent.name not in tool.owners:
            tracer.event("denied", agent.name, tool=name, module=tool.module, status="denied",
                         message=f"{agent.name} is not accountable for {name}; owners: {', '.join(tool.owners)}")
            raise ToolDenied(f"{agent.name} may not call {name}")
        t0 = time.perf_counter()
        try:
            value, summary = tool.fn(agent.ctx, **kwargs)
        except Exception as e:
            tracer.event(tool.kind, agent.name, tool=name, module=tool.module, status="error",
                         ms=int((time.perf_counter() - t0) * 1000), input=input_summary,
                         message=f"{type(e).__name__}: {e}", output={"traceback": traceback.format_exc()[-800:]})
            raise
        tracer.event(tool.kind, agent.name, tool=name, module=tool.module, status="ok",
                     ms=int((time.perf_counter() - t0) * 1000), input=input_summary, output=summary,
                     message=summary.get("_message") if isinstance(summary, dict) else None)
        return value

    def describe(self) -> list[dict]:
        return [{"name": t.name, "module": t.module, "owners": list(t.owners), "kind": t.kind,
                 "description": t.description} for t in self.tools.values()]
