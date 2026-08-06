# -*- coding: utf-8 -*-
"""main.py —— 60° 夹角双路汇入单车道(CAV+MAS)对比实验主流程

职责:组装拓扑/参数 → 生成双车道车辆计划 → 跑 IDM 对照组(主路优先)
与 CAV 实验组(协调器时隙,默认 gap 策略)→ 汇总通用+汇合专项指标 →
控制台输出 → 保存对比图与动画 GIF(风格仿照 cav_mas_animation.gif)。

用法:
    python run_merge.py      # 入口脚本
    python -m cav_mas_merge.main
"""
import os
import sys
from pathlib import Path

import numpy as np

from .config import DEFAULT_IDM, HORIZON, N_VEHICLES, SEED, SPAWN_INTERVAL, CavParams
from .engine import MergeTickEngine
from .metrics import merge_metrics, summarize
from .topology import MergeTopology
from .visualization import (make_animation, plot_queue_throughput,
                            plot_space_time, plot_speed_delay_bar,
                            plot_travel_time_box)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def build_plan(rng, n_vehicles, spawn_interval, idm_base, lane_ratio=0.5):
    """生成双车道车辆计划(A/B 随机分配,保证两股车流公平对抗)。"""
    return [
        {
            "id": f"car_{i:02d}",
            "birth_tick": int(i * spawn_interval),
            "lane": "A" if rng.random() < lane_ratio else "B",
            "idm_params": dict(idm_base,
                               v0=float(np.clip(rng.normal(5.0, 0.5), 4.0, 6.0))),
        }
        for i in range(n_vehicles)
    ]


def main(out_dir=None):
    if out_dir is None:
        out_dir = Path(__file__).resolve().parents[1] / "figures_merge"
    os.makedirs(out_dir, exist_ok=True)

    topo = MergeTopology()
    cav_params = CavParams()

    rng = np.random.default_rng(SEED)
    plan = build_plan(rng, N_VEHICLES, SPAWN_INTERVAL, DEFAULT_IDM)
    n_a = sum(1 for row in plan if row["lane"] == "A")

    eng_idm = MergeTickEngine(topo, plan, has_cav=False, cav_params=cav_params,
                              horizon=HORIZON)
    logs_idm = eng_idm.run()
    eng_cav = MergeTickEngine(topo, plan, has_cav=True, strategy="gap",
                              cav_params=cav_params, horizon=HORIZON)
    logs_cav = eng_cav.run()

    stats_idm = summarize(logs_idm)
    stats_cav = summarize(logs_cav)
    m_idm = merge_metrics(logs_idm, eng_idm.crossings, eng_idm.queue_series)
    m_cav = merge_metrics(logs_cav, eng_cav.crossings, eng_cav.queue_series)
    gain = (stats_idm["tt_mean"] - stats_cav["tt_mean"]) / stats_idm["tt_mean"] * 100
    gain_spd = (stats_cav["speed_kmh"] - stats_idm["speed_kmh"]) / stats_idm["speed_kmh"] * 100
    gain_q = (m_idm["queue_mean"] - m_cav["queue_mean"]) / max(m_idm["queue_mean"], 1e-9) * 100

    print("=" * 70)
    print("  60° 夹角双路 → 单车道 协同汇合(CAV+MAS)对比实验")
    print(f"  场景:{N_VEHICLES} 辆车(A 车道 {n_a} 辆 / B 车道 {N_VEHICLES - n_a} 辆),"
          f"两路夹角 {topo.angle_deg:.0f}°,平行段 {topo.len_lane:.0f}m → "
          f"汇合点 s_merge={topo.s_merge:.0f}m → 单车道 {topo.len_single:.0f}m")
    print("  对照组:IDM 独立决策 + 主路优先让行  实验组:CAV 协调器时隙分配(gap)")
    print("=" * 70)
    head = f"  {'':10s} {'到达数':>6s} {'平均通行时间':>10s} {'平均速度':>8s} {'平均滞留':>8s}"
    print(head)
    print(f"  {'IDM(无CAV)':10s} {stats_idm['n']:6d} {stats_idm['tt_mean']:10.1f}s "
          f"{stats_idm['speed_kmh']:7.2f}km/h {stats_idm['delay_mean']:8.1f}s")
    print(f"  {'CAV(有CAV)':10s} {stats_cav['n']:6d} {stats_cav['tt_mean']:10.1f}s "
          f"{stats_cav['speed_kmh']:7.2f}km/h {stats_cav['delay_mean']:8.1f}s")
    print("-" * 70)
    print(f"  汇合点吞吐量   IDM {m_idm['throughput_per_min']:.2f} 辆/min"
          f"  |  CAV {m_cav['throughput_per_min']:.2f} 辆/min")
    print(f"  汇合区平均排队 IDM {m_idm['queue_mean']:.2f} 辆(峰值 {m_idm['queue_max']})"
          f"  |  CAV {m_cav['queue_mean']:.2f} 辆(峰值 {m_cav['queue_max']})")
    print(f"  CAV 时隙偏差均值 {m_cav['slot_dev_mean']:.2f}s"
          f"(实际到达 vs 分配时刻,越接近 0 越准时)")
    print("=" * 70)
    print(f"  量化结论:应用 CAV+MAS 汇合协同后,平均通行时间缩短 {gain:.1f}%,"
          f"平均行程速度提升 {gain_spd:.1f}%,")
    print(f"  汇合区排队长度下降 {gain_q:.1f}%,滞留时间减少 "
          f"{100 * (1 - stats_cav['delay_mean'] / max(stats_idm['delay_mean'], 1e-9)):.1f}%。")
    print("  原因:IDM 主路优先让行使汇入车在 60° 汇合口排队停车,形成停车波;")
    print("  CAV 协调器为每辆汇入车分配目标时隙,车辆按 '剩余里程/剩余时间' 调整速度,")
    print("  到点即插、全程不停车,汇合点通过率与整体通行效率显著提升。")
    print("=" * 70)

    plot_travel_time_box(logs_idm, logs_cav, out_dir)
    plot_speed_delay_bar(stats_idm, stats_cav, out_dir)
    plot_space_time(topo, eng_idm.frames, eng_cav.frames, out_dir)
    plot_queue_throughput(topo, eng_idm.queue_series, eng_cav.queue_series,
                          m_idm, m_cav, out_dir)
    gif_path = os.path.join(out_dir, "merge_animation.gif")
    make_animation(topo, eng_idm.frames, eng_cav.frames, gif_path)
    print(f"图表与动画已保存至: {out_dir}")


if __name__ == "__main__":
    main()
