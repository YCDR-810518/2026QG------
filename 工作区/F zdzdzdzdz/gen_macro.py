# -*- coding: utf-8 -*-
"""gen_macro.py —— 协作3 宏观时序数据包生成（成员 F，8.5 与 A 宏观数据对接）

产出（相对本文件所在目录的 data/macro/）：
- density_series.csv        : 60s 抽稀密度时序（2026-08-03 06:00 ~ 21:59，960×61 行）
- gate_snapshot_HHMM.json   : 高峰时刻门闸完整快照（含 gateStatus，对齐门闸.json）
- signal_snapshot_HHMM.json : 高峰时刻红绿灯完整快照（含 signalStatus，对齐红绿灯.json）
- gate_states_HHMM.json     : A 精简版 {node_id: "open"/"restricted"/"closed"}
- signal_states_HHMM.json   : A 精简版 {node_id: {"phase": ...}}
- 宏观数据对接说明.md         : 字段表/时间口径/状态码表/对齐规则/A 侧用法

依赖：numpy, pandas
用法：cd 项目目录 && python gen_macro.py
"""
import json
import logging
import sys
from pathlib import Path

import numpy as np

logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("topology").setLevel(logging.WARNING)
logging.getLogger("simulation.topology").setLevel(logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent / "simulation"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flow_data_generator import FlowDataGenerator  # noqa: E402
from simulation import (  # noqa: E402
    GatePolicyController,
    JointRegulator,
    TickEngine,
    Topology,
)

N_PEOPLE = 4000
N_VEHICLES = 300
DENSITY_LEVEL = "peak"
RANDOM_STATE = 42
START_DATE = "2026-08-03"
START_HOUR = 6
N_DAYS = 7
N_TICKS = N_DAYS * 16 * 3600  # 7天

# 三个高峰时刻：tick(秒) → HHMM 命名
PEAK_TICKS = {7200: "0800", 21600: "1200", 43200: "1800"}

GATE_STATUS = {"open": 1, "restricted": 2, "closed": 0}

_ROOT = Path(__file__).resolve().parent
OUT = _ROOT / "data" / "macro"

SNAKE_TO_CAMEL_SIGNAL = {
    "signal_id": "signalId",
    "node_id": "nodeId",
    "phase": "phase",
    "mode": "mode",
    "cycle_time": "cycleTime",
    "green_time": "greenTime",
    "yellow_time": "yellowTime",
    "red_time": "redTime",
    "offset": "offset",
    "throughput_cap": "throughputCap",
    "n_phases": "nPhases",
    "signal_status": "signalStatus",
    "signal_flow_rate": "signalFlowRate",
}


def _dump_peak(eng, snap, tick):
    """采集单个高峰时刻的门闸/红绿灯快照（完整格式 + A 精简版）。"""
    topo = eng.topology

    gates_full, gate_states = [], {}
    for g, pol in snap["gates"].items():
        node_id = pol["gate_id"]
        mode = pol["mode"]
        density = float(eng.people_density[g])
        flow = round(45.0 * float(np.clip(1.0 - density, 0.0, 1.0)), 1)
        gates_full.append({
            "gateId": pol["gate_id"],
            "nodeId": node_id,
            "mode": mode,
            "throughputCap": int(pol["throughput_cap"]),
            "nLanes": int(pol["n_lanes"]),
            "gateStatus": GATE_STATUS[mode],
            "gateFlowRate": flow,
        })
        gate_states[node_id] = mode

    signals_full, signal_states = [], {}
    for node_idx, info in snap["signals"].items():
        signals_full.append({SNAKE_TO_CAMEL_SIGNAL[k]: v for k, v in info.items()
                             if k in SNAKE_TO_CAMEL_SIGNAL})
        signal_states[info["node_id"]] = {"phase": info["phase"]}

    gates_full.sort(key=lambda x: x["gateId"])
    signals_full.sort(key=lambda x: x["signalId"])
    return {
        "gates_full": gates_full,
        "signals_full": signals_full,
        "gate_states": gate_states,
        "signal_states": signal_states,
    }


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"  write -> {path}")


def main():
    print("== [1/4] 构建拓扑 + 生成器 + 引擎 ==")
    topo = Topology(yaml_path=str(_ROOT / "graph_data.yaml"))
    gen = FlowDataGenerator(n_people=N_PEOPLE, n_vehicles=N_VEHICLES,
                            density_level=DENSITY_LEVEL, random_state=RANDOM_STATE,
                            n_days=N_DAYS, start_date=START_DATE)
    gen.generate()
    eng = TickEngine(topo, gen,
                     gate_policy=GatePolicyController(),
                     joint_regulator=JointRegulator(),
                     start_date=START_DATE, start_hour=START_HOUR)

    print(f"== [2/4] 仿真 {N_TICKS} tick（4000 人/300 车/peak）==")
    peaks = {}
    for t in range(N_TICKS):
        snap = eng.step(t)
        if t in PEAK_TICKS:
            peaks[PEAK_TICKS[t]] = _dump_peak(eng, snap, t)
            print(f"  collected snapshot @ tick={t} ({PEAK_TICKS[t]})")

    print("== [3/4] 导出密度时序 + 60s 抽稀 ==")
    full_csv = OUT / "density_series_1s.csv"
    eng.export_snapshot_csv(str(full_csv))

    import pandas as pd

    df = pd.read_csv(full_csv, dtype={"node_id": str})
    df60 = df[df["tick"] % 60 == 0].reset_index(drop=True)
    out_csv = OUT / "density_series.csv"
    df60.to_csv(out_csv, index=False, encoding="utf-8")
    full_csv.unlink()
    print(f"  write -> {out_csv}  ({len(df60)} rows)")

    print("== [4/4] 落盘高峰快照 ==")
    for hhmm, pk in peaks.items():
        _write_json(OUT / f"gate_snapshot_{hhmm}.json", pk["gates_full"])
        _write_json(OUT / f"signal_snapshot_{hhmm}.json", pk["signals_full"])
        _write_json(OUT / f"gate_states_{hhmm}.json", pk["gate_states"])
        _write_json(OUT / f"signal_states_{hhmm}.json", pk["signal_states"])

    print("== 校验 ==")
    n_nodes = topo.n_nodes
    rows = len(df60)
    print(f"  行数: {rows}  (期望 {N_DAYS * 960 * n_nodes})")
    df60["ts"] = pd.to_datetime(df60["timestamp"])
    print(f"  时间范围: {df60['ts'].min()} ~ {df60['ts'].max()}")
    print(f"  节点数: {df60['node_id'].nunique()}  (期望 {n_nodes})")
    dup = df60.duplicated(["tick", "node_id"]).sum()
    print(f"  (tick, node_id) 重复: {dup}  (期望 0)")
    ok = (rows == N_DAYS * 960 * n_nodes and df60["node_id"].nunique() == n_nodes and dup == 0)
    print("  校验:", "PASS" if ok else "FAIL")
    print("done ->", OUT)


if __name__ == "__main__":
    main()
