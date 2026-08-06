# -*- coding: utf-8 -*-
"""main.py —— CAV+MAS 对比实验主流程（拆分自 CAV+MAS.py 第 5 节）

职责：组装拓扑/参数 → 生成车辆计划 → 跑 IDM 对照组与 CAV 实验组 →
汇总指标 → 控制台输出 → 路径数据差分隐私脱敏导出 →
保存对比图与动画 GIF。

用法：
    python run_cav_mas.py    # 入口脚本
    python -m cav_mas.main   # 或以模块方式运行
"""
import os
import sys
from pathlib import Path

import numpy as np

from .config import (DEFAULT_IDM, DP_EPSILON, HORIZON, N_VEHICLES, SEED,
                     SPAWN_INTERVAL, CavParams)
from .engine import TickEngine
from .metrics import summarize
from .privacy import GRID, apply_differential_privacy, export_private_paths
from .topology import LShapeTopology
from .visualization import (make_animation, plot_speed_delay_bar,
                            plot_space_time, plot_travel_time_box)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def build_plan(rng, n_vehicles: int, spawn_interval: float, idm_base: dict) -> list:
    """生成车辆计划（出生 tick 与 IDM 参数）。"""
    return [
        {
            "id": f"car_{i:02d}",
            "birth_tick": int(i * spawn_interval),
            "idm_params": dict(idm_base,
                               v0=float(np.clip(rng.normal(5.0, 0.5), 4.0, 6.0))),
        }
        for i in range(n_vehicles)
    ]


def main(out_dir=None):
    if out_dir is None:
        out_dir = Path(__file__).resolve().parents[1] / "figures"
    os.makedirs(out_dir, exist_ok=True)

    topo = LShapeTopology()
    cav_params = CavParams()

    rng = np.random.default_rng(SEED)
    plan = build_plan(rng, N_VEHICLES, SPAWN_INTERVAL, DEFAULT_IDM)

    eng_idm = TickEngine(topo, plan, has_cav=False, cav_params=cav_params, horizon=HORIZON)
    logs_idm = eng_idm.run()
    eng_cav = TickEngine(topo, plan, has_cav=True, cav_params=cav_params, horizon=HORIZON)
    logs_cav = eng_cav.run()

    stats_idm = summarize(logs_idm)
    stats_cav = summarize(logs_cav)
    gain = (stats_idm["tt_mean"] - stats_cav["tt_mean"]) / stats_idm["tt_mean"] * 100
    gain_spd = (stats_cav["speed_kmh"] - stats_idm["speed_kmh"]) / stats_idm["speed_kmh"] * 100

    print("=" * 64)
    print("  L 型局部拓扑（直道+拐弯）CAV+MAS 对比实验")
    print(f"  场景：{N_VEHICLES} 辆车 {SPAWN_INTERVAL}s 间隔涌入，弯道限速 {topo.v_corner} m/s"
          f"（限速区 {topo.limit_lo:.0f}~{topo.limit_hi:.0f}m）")
    print("=" * 64)
    print(f"  {'':8s} {'到达数':>6s} {'平均通行时间':>10s} {'平均速度':>8s} {'平均滞留':>8s}")
    print(f"  {'IDM':8s} {stats_idm['n']:6d} {stats_idm['tt_mean']:10.1f}s {stats_idm['speed_kmh']:7.2f}km/h {stats_idm['delay_mean']:8.1f}s")
    print(f"  {'CAV':8s} {stats_cav['n']:6d} {stats_cav['tt_mean']:10.1f}s {stats_cav['speed_kmh']:7.2f}km/h {stats_cav['delay_mean']:8.1f}s")
    print("=" * 64)
    print(f"  量化结论：应用 CAV 车联网协同后，平均通行时间缩短 {gain:.1f}%，")
    print(f"  平均行程速度提升 {gain_spd:.1f}%，滞留时间减少 {100 * (1 - stats_cav['delay_mean'] / max(stats_idm['delay_mean'], 1e-9)):.1f}%。")
    print("  原因：IDM 每车独立决策（驾驶员期望速度各异），弯道限速前逐辆急刹形成停车波；")
    print("  CAV 通过车联网按恒定车头时距编队行驶并提前 25m 前视限速平滑减速，")
    print("  车辆不需完全停车即可通过瓶颈，通行效率显著提升。")
    print("=" * 64)

    # ---- 敏感数据差分隐私保护：仅对对外发布的路径坐标加噪（Laplace 机制） ----
    protected = apply_differential_privacy(
        {"idm": eng_idm.frames, "cav": eng_cav.frames},
        topo, epsilon=DP_EPSILON, rng=rng,
    )
    csv_path = export_private_paths(protected, out_dir)
    print("  敏感数据保护：车辆行驶路径差分隐私（Laplace 机制）")
    print(f"  坐标离散化 {GRID:.0f}m 网格缩减敏感度：Δ = 2×{GRID:.0f} = "
          f"{protected['sensitivity']:.0f} m（路径总长 360m → 10m）")
    print(f"  隐私预算 ε = {protected['epsilon']:.1f}，噪声尺度 b = Δ/ε = "
          f"{protected['scale']:.1f} m")
    print(f"  平均单点位置扰动 ≈ {protected['mean_abs_error']:.2f} m"
          f"（指标计算仍用真实值，仅导出数据脱敏）")
    print(f"  脱敏路径数据已保存至: {csv_path}")
    print("=" * 64)

    plot_travel_time_box(logs_idm, logs_cav, out_dir)
    plot_speed_delay_bar(stats_idm, stats_cav, out_dir)
    plot_space_time(topo, eng_idm.frames, eng_cav.frames, out_dir)
    gif_path = os.path.join(out_dir, "cav_mas_animation.gif")
    make_animation(topo, eng_idm.frames, eng_cav.frames, gif_path)
    print(f"图表与动画已保存至: {out_dir}")


if __name__ == "__main__":
    main()
