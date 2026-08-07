# -*- coding: utf-8 -*-
"""compare_cav.py —— IDM 对照组 vs CAV 实验组离线对比（成员 C，FR-12/FR-15）

在真实拓扑（项目目录/graph_data.yaml）上，用**同一份车流**（同一个
FlowDataGenerator）分别跑两轮仿真：
    - mode="idm"：经典 IDM 跟驰（对照组）
    - mode="cav"：CAV 车联网编队跟驰（实验组）
基于引擎 trip 钩子产出的 `eng.trip_logs_`（需 F 合入《F引擎接入改动说明》
中的 engine.py 改动）汇总出 `micro_validation_results.json`，并打印结论文案。

统计范围：仅**从大门(门闸)入园的车辆**（src_node ∈ topology.gate_nodes），
与 union_pack 实时段 cav_stats 同口径。

输出：
    项目目录/data/micro_validation_results.json
    { meta, per_node, od_stats }，字段对齐 8.2 接口文档 C→A 的约定
    （avg_speed_idm / avg_speed_cav / efficiency_gain_pct / avg_delay_time /
     throughput，另补 n_trips / avg_travel_time_idm|_cav；od_stats 为 O-D 对维度）

用法：
    cd 项目目录
    python compare_cav.py                          # 默认 1000人/80车/3600 tick
    python compare_cav.py --n-people 2000 --n-vehicles 300 --n-ticks 7200
    python compare_cav.py --selftest               # 纯逻辑自测（不依赖引擎）

依赖：numpy、项目目录 simulation 包（numpy 随引擎环境）
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_PKG_DIR = Path(__file__).resolve().parent            # 项目目录
_SIM_DIR = _PKG_DIR / "simulation"
for _p in (_PKG_DIR, _SIM_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from simulation import (  # noqa: E402
    CavIdmMovement,
    FlowDataGenerator,
    HysteresisPolicyController,
    JointRegulator,
    TickEngine,
    Topology,
)

_DEFAULT_OUT = _PKG_DIR / "data" / "micro_validation_results.json"


def _build_engine(mode, topo, gen, seed):
    """构造引擎：与 main.py:_build_engine 组件一致，仅 movement 不同。"""
    return TickEngine(
        topo, gen,
        movement=CavIdmMovement(topo, mode=mode),
        gate_policy=HysteresisPolicyController(role="gate"),
        door_policy=HysteresisPolicyController(role="door"),
        joint_regulator=JointRegulator(),
        enable_signals=True,
        seed=seed,
    )


def aggregate_trips(trips_idm, trips_cav, gate_srcs, meta=None):
    """把两轮仿真的行程日志汇总为 micro_validation_results（纯函数，可单测）。

    Parameters
    ----------
    trips_idm / trips_cav : list of dict
        eng.trip_logs_，每行 {src_node, dst_node, birth_tick, finish_tick,
        travel_time, avg_speed_kmh, delay_time}。
    gate_srcs : set of str
        大门节点编码集合；只统计 src_node 在此集合内的车辆。
    meta : dict, optional
        附加元信息（模型/窗口/口径说明等）。

    Returns
    -------
    dict
        {meta, per_node, od_stats}
    """
    def _gate_only(trips):
        return [t for t in trips if t["src_node"] in gate_srcs]

    t_idm = _gate_only(trips_idm)
    t_cav = _gate_only(trips_cav)

    per_node = {}
    for nd in sorted({t["dst_node"] for t in t_idm + t_cav}):
        g_idm = [t for t in t_idm if t["dst_node"] == nd]
        g_cav = [t for t in t_cav if t["dst_node"] == nd]
        avg_idm = float(np.mean([t["avg_speed_kmh"] for t in g_idm])) if g_idm else 0.0
        avg_cav = float(np.mean([t["avg_speed_kmh"] for t in g_cav])) if g_cav else 0.0
        per_node[nd] = {
            "avg_speed_idm": round(avg_idm, 2),
            "avg_speed_cav": round(avg_cav, 2),
            "efficiency_gain_pct": round((avg_cav - avg_idm) / avg_idm, 4) if avg_idm > 0 else 0.0,
            "avg_delay_time": round(
                float(np.mean([t["delay_time"] for t in g_idm + g_cav])), 2)
            if (g_idm + g_cav) else 0.0,
            "throughput": max(len(g_idm), len(g_cav)),
            "n_trips": max(len(g_idm), len(g_cav)),
            "avg_travel_time_idm": round(
                float(np.mean([t["travel_time"] for t in g_idm])), 2) if g_idm else None,
            "avg_travel_time_cav": round(
                float(np.mean([t["travel_time"] for t in g_cav])), 2) if g_cav else None,
        }

    od_stats = {}
    for src, dst in sorted({(t["src_node"], t["dst_node"]) for t in t_idm + t_cav}):
        g_idm = [t for t in t_idm if t["src_node"] == src and t["dst_node"] == dst]
        g_cav = [t for t in t_cav if t["src_node"] == src and t["dst_node"] == dst]
        od_stats[f"{src}|{dst}"] = {
            "trips": max(len(g_idm), len(g_cav)),
            "avg_travel_time_idm": round(
                float(np.mean([t["travel_time"] for t in g_idm])), 2) if g_idm else None,
            "avg_travel_time_cav": round(
                float(np.mean([t["travel_time"] for t in g_cav])), 2) if g_cav else None,
            "avg_speed_idm": round(
                float(np.mean([t["avg_speed_kmh"] for t in g_idm])), 2) if g_idm else None,
            "avg_speed_cav": round(
                float(np.mean([t["avg_speed_kmh"] for t in g_cav])), 2) if g_cav else None,
        }

    return {"meta": meta or {}, "per_node": per_node, "od_stats": od_stats}


def _print_summary(results):
    pn = results["per_node"]
    m = results["meta"]
    print("=" * 72)
    print("  IDM(对照) vs CAV(实验) 微观对比 · 真实园区拓扑")
    print(f"  规模: {m.get('n_people')}人 / {m.get('n_vehicles')}车 / "
          f"{m.get('n_ticks')} tick | seed={m.get('seed')}")
    print(f"  口径: 仅大门入园车辆 | 归集: per_node(按 dst) + od_stats(O-D 对)")
    print("=" * 72)

    keys = sorted(pn, key=lambda k: -pn[k]["throughput"])[:10]
    if not keys:
        print("  （无大门入园车到达记录，请检查车流配置或引擎 trip 钩子）")
        print("=" * 72)
        return

    head = f"  {'节点':>18s} {'吞吐':>5s} {'idm速':>8s} {'cav速':>8s} {'提速%':>7s} {'idm时':>8s} {'cav时':>8s}"
    print(head)
    for k in keys:
        v = pn[k]
        tt_i = f"{v['avg_travel_time_idm']}s" if v["avg_travel_time_idm"] is not None else "-"
        tt_c = f"{v['avg_travel_time_cav']}s" if v["avg_travel_time_cav"] is not None else "-"
        print(f"  {k:>18s} {v['throughput']:>5d} {v['avg_speed_idm']:>8.2f} "
              f"{v['avg_speed_cav']:>8.2f} {v['efficiency_gain_pct']*100:>6.1f}% "
              f"{tt_i:>8s} {tt_c:>8s}")

    # 全局汇总
    def _mean(field):
        vals = [v[field] for v in pn.values() if v.get(field) is not None]
        return float(np.mean(vals)) if vals else 0.0

    gain_tt = (_mean("avg_travel_time_idm") - _mean("avg_travel_time_cav")) \
        / max(_mean("avg_travel_time_idm"), 1e-9) * 100
    print("-" * 72)
    print(f"  全局: 平均通行时间缩短 {gain_tt:.1f}% | "
          f"平均速度 {_mean('avg_speed_idm'):.2f}→{_mean('avg_speed_cav'):.2f} km/h")
    print(f"  结论: CAV 车联网协同相对 IDM 独立决策在真实拓扑上的提速量化见上表")
    print("=" * 72)


def cmd_main(args):
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"== compare_cav: {args.n_people}人 / {args.n_vehicles}车 / "
          f"{args.n_hours}h 投放窗口 × {args.n_ticks} tick (seed={args.seed}) ==")
    topo = Topology()
    gen = FlowDataGenerator(n_people=args.n_people, n_vehicles=args.n_vehicles,
                            random_state=args.seed, n_days=1, n_hours=args.n_hours)
    gen.generate()
    gate_srcs = {topo.node_ids[i] for i in topo.gate_nodes}
    print(f"  拓扑 {topo.n_nodes} 节点 | 大门 {sorted(gate_srcs)}")

    trips = {}
    for mode in ("idm", "cav"):
        eng = _build_engine(mode, topo, gen, args.seed)
        if not hasattr(eng, "trip_logs_"):
            print("\n[阻塞] 引擎尚未合入 trip 钩子（engine.py 改动）。")
            print("请先按《项目目录/包或模块的说明文件/CavIdmMovement 结构及使用说明.md》")
            print("第六节与 F 确认并合入 engine.py 改动后再运行本脚本。")
            return 1
        t0 = time.perf_counter()
        eng.run(args.n_ticks)
        dt = time.perf_counter() - t0
        trips[mode] = eng.trip_logs_
        print(f"  [{mode}] 到达 {len(trips[mode])} 辆 (trip 日志) | "
              f"tick_mean_ms={eng.metrics.report()['tick_mean_ms']:.2f} | "
              f"耗时 {dt:.1f}s")

    meta = {
        "models": {"idm": "CavIdmMovement(mode=idm)", "cav": "CavIdmMovement(mode=cav)"},
        "horizon_ticks": args.n_ticks,
        "n_people": args.n_people,
        "n_vehicles": args.n_vehicles,
        "n_hours": args.n_hours,
        "seed": args.seed,
        "scope": "仅统计 src_node ∈ gate_nodes 的大门入园车辆",
        "per_node_aggregation": "按 dst_node 归集（引擎无节点穿越记录，口径近似）",
        "delay_definition": "信号排队 + 车速<5km/h 低速行驶（不含计划性出发等待）",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    results = aggregate_trips(trips["idm"], trips["cav"], gate_srcs, meta=meta)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n已写出 -> {out}")

    _print_summary(results)
    return 0


def cmd_selftest():
    """纯逻辑自测：合成 trip 数据验证 aggregate_trips。"""
    gate = {"gate_south", "gate_west"}
    mk = lambda i, src, dst, tt, spd, dly: {  # noqa: E731
        "src_node": src, "dst_node": dst, "birth_tick": i, "finish_tick": i + tt,
        "travel_time": tt, "avg_speed_kmh": spd, "delay_time": dly,
    }
    idm = [mk(0, "gate_south", "zone_canteen", 300, 12.0, 40.0),
           mk(1, "gate_south", "zone_canteen", 340, 10.0, 60.0),
           mk(2, "zone_canteen", "gate_west", 200, 14.0, 5.0),   # 非大门入园 → 应被过滤
           mk(3, "gate_west", "library", 150, 16.0, 8.0)]
    cav = [mk(4, "gate_south", "zone_canteen", 210, 22.0, 5.0),
           mk(5, "gate_south", "zone_canteen", 220, 23.0, 6.0),
           mk(6, "zone_canteen", "gate_west", 180, 18.0, 3.0),   # 非大门入园 → 应被过滤
           mk(7, "gate_west", "library", 120, 24.0, 2.0)]
    r = aggregate_trips(idm, cav, gate, {"selftest": True})
    assert set(r["per_node"]) == {"zone_canteen", "library"}, r["per_node"]
    c = r["per_node"]["zone_canteen"]
    assert c["throughput"] == 2 and c["n_trips"] == 2, c
    assert c["avg_speed_idm"] == 11.0 and c["avg_speed_cav"] == 22.5, c
    assert c["efficiency_gain_pct"] == round((22.5 - 11.0) / 11.0, 4), c
    assert c["avg_travel_time_idm"] == 320.0 and c["avg_travel_time_cav"] == 215.0, c
    assert "zone_canteen|gate_west" not in r["od_stats"], r["od_stats"]
    assert r["od_stats"]["gate_south|zone_canteen"]["trips"] == 2, r["od_stats"]
    print("selftest OK: 大门入园过滤 / per_node 归集 / od_stats / 指标公式 全部通过 ✓")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="compare_cav",
                                description="IDM vs CAV 离线对比（成员 C，FR-12/15）")
    p.add_argument("--n-people", type=int, default=1000)
    p.add_argument("--n-vehicles", type=int, default=80)
    p.add_argument("--n-ticks", type=int, default=3600)
    p.add_argument("--n-hours", type=float, default=1.0,
                   help="投放窗口小时数（越小车流越集中，越能体现跟驰/编队差异；缺省 1h）")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=str(_DEFAULT_OUT), help="micro_validation_results.json 输出路径")
    p.add_argument("--selftest", action="store_true", help="仅跑纯逻辑自测")
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return cmd_selftest() if args.selftest else cmd_main(args)


if __name__ == "__main__":
    sys.exit(main())
