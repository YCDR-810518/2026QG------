# -*- coding: utf-8 -*-
"""cav_pack.py —— CAV/微观数据联合打包采集（成员 C，供 F 引擎 union_pack 调用）

两个对外函数，均为纯函数式接口、无状态、不依赖引擎内部实现细节之外的约定：

    collect_cav_stats(eng) -> dict
        union_pack 实时段：每 interval=10 tick 调用一次，只统计**从大门(门闸)
        入园的车辆**（src_node ∈ topology.gate_nodes），返回当前微观状态摘要。

    pack_micro_results(path=None) -> dict
        union_pack 离线段：读取 compare_cav.py 跑 IDM/CAV 两轮后落盘的
        micro_validation_results.json，随包附带；文件缺失/损坏时返回 {}，
        保证打包链路不中断。

指标口径（与 F 引擎改动说明 & 8.2 接口文档对齐）：
    avg_speed_kmh  : 移动中(state=TRAVEL)车辆瞬时速度均值，m/s × 3.6
    low_speed_ratio: 全在场门入车辆中 speed < 5km/h 的占比
    delay          : 信号排队 + 低速行驶（trip 钩子口径，见 compare_cav.py）

依赖：numpy（引擎自带）
"""
import json
from pathlib import Path

import numpy as np

_MS_TO_KMH = 3.6
_DELAY_SPEED_THRESHOLD = 1.39  # 5 km/h 以下视为滞留（与 agents.py / engine 改动口径一致）
_MICRO_RESULTS_PATH = Path(__file__).resolve().parents[1] / "data" / "micro_validation_results.json"


def _gate_src_mask(eng):
    """只保留从大门(门闸)入园的车辆掩码；拓扑无大门时返回 None（不筛选）。"""
    gate_idx = sorted(set(eng.topology.gate_nodes))
    if not gate_idx:
        return None
    return np.isin(eng.pool.data["src_node"], gate_idx)


def collect_cav_stats(eng) -> dict:
    """实时微观统计（仅大门入园车辆）。

    Parameters
    ----------
    eng : TickEngine
        运行中的引擎实例（任意时刻可调用）。

    Returns
    -------
    dict
        {avg_speed_kmh, low_speed_ratio, n_vehicles, n_low_speed}；空车流时全 0。
    """
    data = eng.pool.data
    gate_mask = _gate_src_mask(eng)
    veh = data["active"] & (data["kind"] == 1)
    if gate_mask is not None:
        veh = veh & gate_mask
    n_vehicles = int(veh.sum())
    if n_vehicles == 0:
        return {"avg_speed_kmh": 0.0, "low_speed_ratio": 0.0,
                "n_vehicles": 0, "n_low_speed": 0}

    moving = veh & (data["state"] == 1)
    speeds = data["speed"][moving]
    avg = float(np.mean(speeds) * _MS_TO_KMH) if moving.any() else 0.0

    low = veh & (data["speed"] < _DELAY_SPEED_THRESHOLD)
    n_low = int(low.sum())
    return {
        "avg_speed_kmh": round(avg, 2),
        "low_speed_ratio": round(n_low / n_vehicles, 4),
        "n_vehicles": n_vehicles,
        "n_low_speed": n_low,
    }


def pack_micro_results(path=None) -> dict:
    """读取离线对比结果（compare_cav.py 产出）供 union_pack 附带。

    Parameters
    ----------
    path : str or Path, optional
        覆盖默认路径（缺省 项目目录/data/micro_validation_results.json）。

    Returns
    -------
    dict
        micro_validation_results 内容；文件缺失或 JSON 损坏时返回 {}。
    """
    p = Path(path) if path else _MICRO_RESULTS_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    # 本地自测：仅验证纯函数逻辑（不依赖引擎运行态）
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    assert pack_micro_results(Path("C:/nonexistent/x.json")) == {}
    print("pack_micro_results 缺失文件 → {}  ✓")
    print("OK: cav_pack 模块语法与纯函数自测通过")
