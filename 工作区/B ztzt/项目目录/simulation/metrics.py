# -*- coding: utf-8 -*-
"""metrics.py —— 密度/吞吐/性能指标采集（成员 F）

按 tick 记录各模块耗时、实体吞吐、密度快照，汇总生成压测报告。

依赖：numpy、time
"""
import time
from dataclasses import dataclass, field

import numpy as np

LEVEL_THRESHOLDS = (0.3, 0.6, 0.9)  # low / medium / high / critical


def level_of(density):
    """密度等级：<0.3 low / <0.6 medium / <0.9 high / >=0.9 critical。"""
    return np.where(density < 0.3, "low", np.where(density < 0.6, "medium",
                    np.where(density < 0.9, "high", "critical")))


@dataclass
class EngineMetrics:
    """逐 tick 指标容器。

    Attributes
    ----------
    tick_times : list of float
        每 tick 总耗时（秒）。
    module_times : list of dict
        每 tick 分模块耗时（秒）。
    throughput : list of int
        每 tick 新流入实体数。
    n_active : list of int
        每 tick 在场实体数。
    density_series : list of np.ndarray
        每 tick 全节点密度（n_nodes,）。
    """

    tick_times: list = field(default_factory=list)
    module_times: list = field(default_factory=list)
    throughput: list = field(default_factory=list)
    n_active: list = field(default_factory=list)
    density_series: list = field(default_factory=list)
    people_series: list = field(default_factory=list)
    vehicles_series: list = field(default_factory=list)
    gate_series: list = field(default_factory=list)
    door_series: list = field(default_factory=list)
    signal_series: list = field(default_factory=list)

    def record(self, elapsed, modules=None, n_in=0, n_active=0, density=None,
               people=None, vehicles=None, gate_states=None, door_states=None,
               signal_states=None):
        self.tick_times.append(elapsed)
        self.module_times.append(dict(modules or {}))
        self.throughput.append(int(n_in))
        self.n_active.append(int(n_active))
        if density is not None:
            self.density_series.append(np.asarray(density, dtype=np.float64).copy())
        if people is not None:
            self.people_series.append(np.asarray(people, dtype=np.int64).copy())
        if vehicles is not None:
            self.vehicles_series.append(np.asarray(vehicles, dtype=np.int64).copy())
        if gate_states is not None:
            self.gate_series.append(dict(gate_states))
        if door_states is not None:
            self.door_series.append(dict(door_states))
        if signal_states is not None:
            self.signal_series.append(dict(signal_states))

    # ------------------------------------------------------------------ 汇总
    def report(self, warmup=0):
        """生成指标汇总（剔除前 warmup 个 tick）。

        Returns
        -------
        dict
            mean/p95/p99 tick 耗时、总吞吐、平均在场实体、分模块耗时均值。
        """
        t = np.asarray(self.tick_times[warmup:], dtype=np.float64)
        out = {
            "tick_mean_ms": float(t.mean() * 1000) if t.size else 0.0,
            "tick_p95_ms": float(np.percentile(t, 95) * 1000) if t.size else 0.0,
            "tick_p99_ms": float(np.percentile(t, 99) * 1000) if t.size else 0.0,
            "total_in": int(sum(self.throughput[warmup:])),
            "n_ticks": int(t.size),
            "avg_active": float(np.mean(self.n_active[warmup:])) if t.size else 0.0,
        }
        mods = {}
        for m in self.module_times[warmup:]:
            for k, v in m.items():
                mods.setdefault(k, []).append(v)
        out["module_mean_ms"] = {k: float(np.mean(v) * 1000) for k, v in mods.items()}
        return out


class PerfTimer:
    """分模块计时辅助（perf_counter）。"""

    def __init__(self):
        self._mark = None
        self._acc = {}

    def start(self):
        self._mark = time.perf_counter()

    def stop(self, name):
        if self._mark is None:
            return
        self._acc[name] = self._acc.get(name, 0.0) + (time.perf_counter() - self._mark)
        self._mark = None

    def snapshot(self):
        return dict(self._acc)

    def clear(self):
        self._acc = {}
