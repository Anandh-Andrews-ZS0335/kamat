"""Discrete-event model of the collections floor (SimPy): does each collector's queue fit their shift?

Each collector works their own queue in order, one task at a time, until their shift ends. Handling
times vary around each action's standard minutes, so a queue planned to fit can still run over.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import simpy


def simulate_floor(plan: pd.DataFrame, shifts: dict[str, int], replications: int, seed: int) -> dict:
    """plan needs collector_id; shifts maps collector id -> shift minutes (the whole shift, no buffer).

    A voice task with no collector is never worked, so it counts as not completed.
    """
    order = ["collector_id", "queue_position"] if "queue_position" in plan else ["collector_id", "rank"]
    voice = plan[(plan["minutes"] > 0)].sort_values(order)
    if voice.empty:
        return {"voice_tasks": 0, "replications": replications, "completion_rate_mean": 1.0, "completion_rate_p10": 1.0,
                "utilisation_mean": 0.0, "overflow_tasks_mean": 0.0, "value_at_risk_share": 0.0, "per_collector": {}}
    rng = np.random.default_rng(seed)
    total_value = float(voice["value"].sum())
    total_shift = sum(shifts.values()) or 1
    comp, util, overflow, value_lost = [], [], [], []
    per: dict[str, list[float]] = {cid: [] for cid in shifts}

    for _ in range(replications):
        env = simpy.Environment()
        done: list[int] = []
        busy = [0.0]
        handling = pd.Series(rng.lognormal(np.log(voice["minutes"].to_numpy(float)), 0.35), index=voice.index)

        def collector(env, tasks, shift, done=done, busy=busy, handling=handling):
            for idx in tasks:
                if env.now >= shift:
                    return
                need = float(handling[idx])
                work = min(need, shift - env.now)
                yield env.timeout(work)
                busy[0] += work
                if work >= need:
                    done.append(idx)

        for cid, shift in shifts.items():
            tasks = voice.index[voice["collector_id"] == cid].tolist()
            env.process(collector(env, tasks, shift))
        env.run(until=max(shifts.values(), default=0) + 1)

        completed = voice.loc[done]
        comp.append(len(completed) / len(voice))
        util.append(busy[0] / total_shift)
        overflow.append(len(voice) - len(completed))
        value_lost.append(1 - (completed["value"].sum() / total_value if total_value else 1))
        for cid in shifts:
            n = int((voice["collector_id"] == cid).sum())
            if n:
                per[cid].append(float((completed["collector_id"] == cid).sum()) / n)

    return {"voice_tasks": int(len(voice)), "replications": replications,
            "completion_rate_mean": float(np.mean(comp)), "completion_rate_p10": float(np.percentile(comp, 10)),
            "utilisation_mean": float(np.mean(util)), "overflow_tasks_mean": float(np.mean(overflow)),
            "value_at_risk_share": float(np.mean(value_lost)),
            "per_collector": {cid: round(float(np.mean(v)), 3) for cid, v in per.items() if v}}
