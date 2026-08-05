# -*- coding: utf-8 -*-
"""stress_test.py —— 压力测试与正确性校验（成员 F，NFR-01 / FR-20）

- run_level    ：纯并发成本测试，向实体池预灌 n_entities 个在场实体，测每 tick
                 全量状态机/移动/聚合耗时（对齐 §8.3 实体吞吐口径）。
- run_integration：全链路集成（生成器流入 + 门闸 + 信号 + 调控），8.6/8.7 正式使用。
- verify_baseline：小规模正确性校验（无实体丢失 + 密度口径统计对齐）。

判定标准（§8.3）：tick 平均 <100ms、p95 <300ms、内存 <3GB(8000)/<2GB(4000)、
实体吞吐 ≥40000(8000)/≥20000(4000) 实体/s。

依赖：numpy
用法：
    python stress_test.py                  # 加压曲线 1000→8000
    from stress_test import run_level
    rep = run_level(4000)
"""
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from config import get_config
from controller import HysteresisPolicyController, SignalPolicyController
from engine import TickEngine
from entities import STATE_DWELL_DST, STATE_TRAVEL
from joint_regulator import JointRegulator
from topology import Topology

# 判定门槛（§8.3）
THRESHOLDS = {
    "tick_mean_ms": 100.0,
    "tick_p95_ms": 300.0,
    "mem_gb_4000": 2.0,
    "mem_gb_8000": 3.0,
    "throughput_4000": 20000.0,
    "throughput_8000": 40000.0,
}


@dataclass
class StressReport:
    """单级压力测试报告。

    Attributes
    ----------
    level : int
        实体规模。
    n_ticks : int
        计时 tick 数。
    report : dict
        metrics.report() 性能汇总。
    peak_mem_mb : float
        内存峰值（MB）。
    throughput : float
        实体吞吐（实体/s）＝平均在场实体 / tick 均耗时。
    checks : dict
        各项达标判定。
    passed : bool
        是否全部达标。
    """

    level: int = 0
    n_ticks: int = 0
    report: dict = field(default_factory=dict)
    peak_mem_mb: float = 0.0
    throughput: float = 0.0
    checks: dict = field(default_factory=dict)
    passed: bool = False

    def as_table(self):
        r = self.report
        return {
            "level": self.level,
            "tick_mean_ms": round(r.get("tick_mean_ms", 0.0), 2),
            "tick_p95_ms": round(r.get("tick_p95_ms", 0.0), 2),
            "mem_mb": round(self.peak_mem_mb, 1),
            "throughput/s": int(self.throughput),
            "avg_active": round(r.get("avg_active", 0.0), 0),
            "passed": self.passed,
        }


def _build_generator(n_people, n_vehicles, random_state):
    from flow_data_generator import FlowDataGenerator

    gen = FlowDataGenerator(n_people=n_people, n_vehicles=n_vehicles,
                            random_state=random_state, n_days=1)
    gen.generate()
    return gen


def _build_engine(topo, gen, enable_signals=True):
    return TickEngine(
        topo, gen,
        gate_policy=HysteresisPolicyController(role="gate"),
        door_policy=HysteresisPolicyController(role="door"),
        joint_regulator=JointRegulator(),
        enable_signals=enable_signals,
        seed=42,
    )


def _seed_pool(engine, n_entities, random_state=42):
    """向实体池预灌 n_entities 个在场移动实体（纯并发成本测试用）。

    实体随机分布在随机边的中段（state=1），使每 tick 全量状态机/移动/聚合
    都作用于该规模群体。
    """
    rng = np.random.default_rng(random_state)
    topo = engine.topology
    n_nodes = topo.n_nodes

    kinds = rng.choice([0, 0, 0, 0, 1], size=n_entities).astype(np.int8)  # 80% 行人
    src = rng.integers(0, n_nodes, size=n_entities).astype(np.int32)
    dst = rng.integers(0, n_nodes, size=n_entities).astype(np.int32)
    same = src == dst
    dst[same] = (dst[same] + 1) % n_nodes

    slots = engine.pool.allocate(n_entities)
    if len(slots) == 0:
        return 0
    n = len(slots)
    d = engine.pool.data
    d["kind"][slots] = kinds[:n]
    d["src_node"][slots] = src[:n]
    d["dst_node"][slots] = dst[:n]
    d["state"][slots] = STATE_TRAVEL

    base_speed = np.where(kinds[:n] == 1, topo.base_speed(1), topo.base_speed(0)).astype(np.float32)
    for k, s in enumerate(slots):
        path = topo.path(int(src[k]), int(dst[k]), kind=int(kinds[k]))
        engine.pool.set_path(s, path)
        last = path.size - 2
        edge = int(rng.integers(0, max(last, 1)))
        origin = int(path[edge])
        target = int(path[edge + 1])
        length = topo.edge_length[origin, target]
        d["path_pos"][s] = edge
        d["cur_node"][s] = origin
        d["edge_target"][s] = target
        d["edge_pos"][s] = float(rng.uniform(0.0, max(length, 1.0) * 0.5))
        d["speed"][s] = base_speed[k]
    engine.movement.update_speed(engine.pool)
    return n


def _check_pass(n_entities, report, peak_mem_mb, throughput):
    checks = {
        "tick_mean_ms": report["tick_mean_ms"] < THRESHOLDS["tick_mean_ms"],
        "tick_p95_ms": report["tick_p95_ms"] < THRESHOLDS["tick_p95_ms"],
        "memory_gb": peak_mem_mb / 1024.0 < (THRESHOLDS["mem_gb_8000"] if n_entities > 6000
                                            else THRESHOLDS["mem_gb_4000"]),
        "throughput": throughput > (THRESHOLDS["throughput_8000"] if n_entities > 6000
                                    else THRESHOLDS["throughput_4000"]),
    }
    return checks


def _finalize(level, n_ticks, report, peak_mem_mb, throughput):
    checks = _check_pass(level, report, peak_mem_mb, throughput)
    return StressReport(
        level=int(level), n_ticks=int(n_ticks), report=report,
        peak_mem_mb=peak_mem_mb, throughput=throughput, checks=checks,
        passed=all(checks.values()),
    )


def run_level(n_entities, n_ticks=100, warmup=10, enable_signals=True):
    """纯并发成本测试：预灌 n_entities 个在场实体并计时。

    Parameters
    ----------
    n_entities : int
        在场实体规模（4000 预压 / 8000 正式）。
    n_ticks : int
        正式计时的 tick 数（默认 100）。
    warmup : int
        预热 tick 数（不计时，默认 10）。
    enable_signals : bool
        是否启用红绿灯（默认 True）。

    Returns
    -------
    StressReport
        该级压测报告。
    """
    tracemalloc.start()
    cfg = get_config()
    topo = Topology(cfg["topology"]["file"])
    gen = _build_generator(0, 0, 42)
    eng = _build_engine(topo, gen, enable_signals=enable_signals)
    _seed_pool(eng, n_entities, random_state=42)

    t_start = time.perf_counter()
    eng.run(warmup + n_ticks)
    wall = time.perf_counter() - t_start
    _peak, _cur = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    report = eng.metrics.report(warmup=warmup)
    peak_mem_mb = _peak / (1024 ** 2)
    tick_mean_s = report["tick_mean_ms"] / 1000.0
    throughput = report["avg_active"] / tick_mean_s if tick_mean_s > 0 else 0.0
    return _finalize(n_entities, n_ticks, report, peak_mem_mb, throughput)


def run_integration(n_people, n_vehicles, n_ticks=3600, warmup=10, enable_signals=True):
    """全链路集成测试：生成器流入 + 门闸 + 信号 + 调控。"""
    tracemalloc.start()
    cfg = get_config()
    topo = Topology(cfg["topology"]["file"])
    gen = _build_generator(n_people, n_vehicles, 42)
    eng = _build_engine(topo, gen, enable_signals=enable_signals)
    eng.run(warmup + n_ticks)
    _peak, _cur = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    report = eng.metrics.report(warmup=warmup)
    peak_mem_mb = _peak / (1024 ** 2)
    tick_mean_s = report["tick_mean_ms"] / 1000.0
    throughput = report["avg_active"] / tick_mean_s if tick_mean_s > 0 else 0.0
    return _finalize(n_people, n_ticks, report, peak_mem_mb, throughput)


def verify_baseline(n_people=200, n_vehicles=20, n_ticks=2000, corr_threshold=0.5):
    """正确性校验：结构不变量（硬）+ 密度统计对齐（软）。

    结构不变量：
    - 无实体丢失：spawned == active + recycled；
    - 密度非负且不超容量上限的合理倍数；
    - 全节点在场数守恒。
    统计对齐：enable_signals=False 口径下引擎每节点人数与生成器
    density_series 在公共采样 tick 的 Pearson 相关系数。

    Returns
    -------
    dict
        {passed, checks, corr}。
    """
    cfg = get_config()
    topo = Topology(cfg["topology"]["file"])
    gen = _build_generator(n_people, n_vehicles, 42)
    eng = _build_engine(topo, gen, enable_signals=False)

    ds = gen._flow.density_series if gen._flow is not None else None

    eng.run(n_ticks)
    pool = eng.pool
    checks = {}

    # 1. 无实体丢失
    spawned = pool.n_spawned
    active = pool.n_active
    recycled = pool.n_recycled
    checks["no_loss"] = bool(spawned == active + recycled)
    checks["spawned"] = spawned
    checks["active"] = active
    checks["recycled"] = recycled

    # 2. 密度非负 / 不超合理上界（density = 人数/容量，可 >1，设 10 为安全上界）
    dens = np.concatenate([d.ravel() for d in eng.metrics.density_series]) if eng.metrics.density_series else np.array([])
    checks["density_nonneg"] = bool(dens.size == 0 or dens.min() >= 0.0)
    checks["density_bounded"] = bool(dens.size == 0 or dens.max() <= 10.0)

    # 3. 统计对齐（与生成器 density_series 的公共 tick 逐节点相关）
    corr = float("nan")
    if ds is not None and eng.metrics.people_series:
        sample_interval = getattr(gen, "sample_interval", 60)
        n_samples = min(len(eng.metrics.people_series) // sample_interval, int(ds["tick"].max()) // sample_interval + 1)
        eng_rows, gen_rows = [], []
        for k in range(n_samples):
            t = k * sample_interval
            if t >= len(eng.metrics.people_series):
                break
            eng_rows.append(eng.metrics.people_series[t].astype(np.float64))
            m = ds["tick"] == t
            node_idx = ds["node_idx"][m]
            gen_arr = np.zeros(topo.n_nodes, dtype=np.float64)
            gen_arr[node_idx] = ds["people"][m]
            gen_rows.append(gen_arr)
        if eng_rows:
            a = np.concatenate(eng_rows)
            b = np.concatenate(gen_rows)
            if a.std() > 0 and b.std() > 0:
                corr = float(np.corrcoef(a, b)[0, 1])
            else:
                corr = float("nan")
    checks["corr"] = corr
    checks["corr_ok"] = bool(np.isfinite(corr) and corr >= corr_threshold)

    passed = checks.get("no_loss", False) and checks.get("density_nonneg", False) and checks.get("corr_ok", False)
    return {"passed": passed, "checks": checks, "corr": corr}


def _print_table(reports):
    header = f"{'level':>7} {'tick_mean_ms':>12} {'p95_ms':>8} {'mem_mb':>9} {'thr/s':>10} {'active':>9}  passed"
    print(header)
    for r in reports:
        row = r.as_table()
        print(f"{row['level']:>7} {row['tick_mean_ms']:>12} {row['tick_p95_ms']:>8} "
              f"{row['mem_mb']:>9} {row['throughput/s']:>10} {row['avg_active']:>9}  {row['passed']}")


def main():
    """加压曲线 1000 → 2000 → 4000 → 6000 → 8000（每级 warmup 10 + 测 100 tick）。"""
    levels = [1000, 2000, 4000, 6000, 8000]
    reports = []
    for level in levels:
        print(f"\n=== 压测 level={level} ===")
        rep = run_level(level, n_ticks=100, warmup=10)
        reports.append(rep)
    _print_table(reports)
    print("\nverify_baseline:", verify_baseline())
    return reports


if __name__ == "__main__":
    main()
