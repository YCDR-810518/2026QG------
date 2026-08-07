# -*- coding: utf-8 -*-
"""demo_platoon_edge.py —— 单边 CAV 编队一致性收敛演示（成员 C，参考 test/code/cav.py）

默认在**真实园区地图**的某一条边上跑 4 车编队（缺省 cross_zh_south→cross_zh_mid，
可用 --src/--dst 指定任意相邻节点对）：
- leader（CAV_L1）恒速 1.5 m/s；3 辆跟随车（CAV_F1..F3）用一致性控制
      dV = −(L+K)·X_e − (β·L + γ·K)·V_e
  （X_e = X − xL − R 位置误差，V_e = V − vL 速度误差，A 链式邻接，K 对 leader 权重）
  在到达终点前收敛为 12m 等距编队并保持稳定；
- leader 到终点节点停车，跟随车收敛堆叠停车，全部停稳后再记 2s 结束；
- 每整秒产出一帧（schema 兼容 animate_fleet.py）与一行遥测 CSV，
  并自动调用 animate_fleet.py（--topology graph_data.yaml 画完整园区路网，
  黄线高亮该单边）生成 platoon_animation.html。

用法：
    cd 项目目录
    python demo_platoon_edge.py                                   # 默认 cross_zh_south→cross_zh_mid
    python demo_platoon_edge.py --src canteen_1 --dst gate_east   # 指定真实边
    python demo_platoon_edge.py --synthetic                       # 旧合成 A→B 迷你模式
    python demo_platoon_edge.py --v-lead 1.0 --spacing 15

产出：
    data/platoon_frames.json     逐秒帧（position 严格在所选边上）
    data/platoon_telemetry.csv   逐秒遥测（tick,car_id,role,mileage,speed,
                                  gap_to_front,front_distance_to_target）
    platoon_animation.html       交互动画（自动生成，完整园区路网）
    data/platoon_topo.yaml       仅 --synthetic 模式写 2 节点迷你拓扑
"""
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml

_NODE_A, _NODE_B = "A", "B"
_CANVAS_A = (10.0, 50.0)
_CANVAS_B = (90.0, 50.0)
_GRAPH_PATH = "graph_data.yaml"
_DEFAULT_SRC, _DEFAULT_DST = "cross_zh_south", "cross_zh_mid"


def _s_to_xy(s, edge_len, pA, pB):
    """里程 s（m）→ 画布坐标：在 A、B 两端点画布坐标间线性插值（车严格在边上）。"""
    frac = float(np.clip(s, 0.0, edge_len)) / edge_len
    x = pA[0] + frac * (pB[0] - pA[0])
    y = pA[1] + frac * (pB[1] - pA[1])
    return round(x, 2), round(y, 2)


def _laplacian(A):
    return np.diag(A.sum(axis=1)) - A


def _load_graph(path=_GRAPH_PATH):
    """读 graph_data.yaml → (node_pos {id:(x,y)}, edges [(a,b,length),...])。"""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    node_pos = {nid: (float(attrs["x"]), float(attrs["y"]))
                for nid, attrs in data.get("nodes", {}).items()}
    edges = []
    for e in data.get("edges", []):
        ns = e.get("nodes", [])
        if len(ns) == 2:
            edges.append((ns[0], ns[1], float(e.get("length", 0.0))))
    return node_pos, edges


def _resolve_edge(graph, src, dst):
    """在真实拓扑里找 src↔dst 边，返回 (pA, pB, length)；不存在抛错。"""
    node_pos, edges = graph
    for a, b, length in edges:
        if (a == src and b == dst) or (a == dst and b == src):
            if src not in node_pos or dst not in node_pos:
                raise ValueError(f"节点不在拓扑中: {src} / {dst}")
            pA = node_pos[src]
            pB = node_pos[dst]
            return pA, pB, float(length)
    raise ValueError(f"拓扑中不存在边 {src}→{dst}，请检查是否相邻")


def simulate(fleet_size=4, v_lead=1.5, edge_len=300.0, spacing=12.0,
             dt=0.1, amax=2.0, vmax=3.0, k_leader=0.6, beta=1.2, gamma=0.8,
             leader_start=40.0, stop_eps=0.05, settle_s=2.0, max_t=600.0,
             pA=_CANVAS_A, pB=_CANVAS_B, src_id=_NODE_A, dst_id=_NODE_B):
    """跑一遍单边编队，返回 (frames, rows)。

    Parameters
    ----------
    pA, pB : tuple
        边两端点在画布上的坐标（车在其连线插值）。
    src_id, dst_id : str
        帧 path 的起点/终点节点 id。

    Returns
    -------
    frames : dict
        {meta, frames:[{tick, gate_id, total_path_length, path, fleet:[...]}]}
    rows : list of dict
        遥测行 {tick, car_id, role, mileage, speed, gap_to_front,
               front_distance_to_target}
    """
    n = int(fleet_size) - 1                      # 跟随车数
    if n <= 0:
        raise ValueError("fleet_size 至少为 2")
    R = np.array([-spacing * i for i in range(1, n + 1)], dtype=float)
    A = np.zeros((n, n))
    for i in range(1, n):
        A[i, i - 1] = 1.0                        # 链式：后车感知前车
    K = np.eye(n) * k_leader
    L = _laplacian(A)

    xL = float(leader_start)
    X = xL + R + np.array([2.0, -3.0, 1.0][:n], dtype=float)  # 贴近期望编队 + 小扰动
    X = np.clip(X, 0.0, edge_len)
    V = np.full(n, min(v_lead * 0.86, vmax))
    car_ids = ["CAV_L1"] + [f"CAV_F{i}" for i in range(1, n + 1)]

    frames, rows = [], []
    tick = 0
    t = 0.0
    acc = np.zeros(n)
    vL = float(v_lead)
    leader_stopped = False
    all_stopped_t = None

    def emit():
        nonlocal tick
        s_all = [xL] + list(X)
        d_target = [edge_len - s for s in s_all]
        pos = [_s_to_xy(s, edge_len, pA, pB) for s in s_all]
        fleet_items = []
        for i, cid in enumerate(car_ids):
            role = "leader" if i == 0 else "follower"
            gap = 0.0 if i == 0 else s_all[i - 1] - s_all[i]
            fleet_items.append({
                "car_id": cid,
                "role": role,
                "position": {"x": pos[i][0], "y": pos[i][1]},
                "speed": round(vL if i == 0 else V[i - 1], 3),
                "acceleration": round(0.0 if i == 0 else acc[i - 1], 3),
                "distance_to_front": round(gap, 3),
                "mileage": round(s_all[i], 3),
                "distance_to_target": round(d_target[i], 3),
            })
        frames.append({
            "tick": tick,
            "gate_id": src_id,
            "total_path_length": round(edge_len, 3),
            "path": {"start_node_id": src_id, "end_node_id": dst_id,
                     "route_nodes": [src_id, dst_id]},
            "fleet": fleet_items,
        })
        for i, cid in enumerate(car_ids):
            front = 0 if i == 0 else i - 1      # 前车下标
            rows.append({
                "tick": tick,
                "car_id": cid,
                "role": "leader" if i == 0 else "follower",
                "mileage": round(s_all[i], 3),
                "speed": round(vL if i == 0 else V[i - 1], 3),
                "gap_to_front": round(0.0 if i == 0 else s_all[i - 1] - s_all[i], 3),
                "front_distance_to_target": round(d_target[front], 3),
            })
        tick += 1

    emit()                                       # tick 0

    while t <= max_t:
        if xL >= edge_len:
            leader_stopped = True
        vL = 0.0 if leader_stopped else v_lead
        X_e = X - xL - R
        V_e = V - vL
        acc = -(L + K) @ X_e - (beta * L + gamma * K) @ V_e
        acc = np.clip(acc, -amax, amax)
        X = X + V * dt
        V = np.clip(V + acc * dt, 0.0, vmax)
        X = np.clip(X, 0.0, edge_len)
        xL = min(xL + vL * dt, edge_len)
        t += dt

        if abs(t - round(t)) < 1e-9:
            emit()

        if leader_stopped and float(np.max(V)) < stop_eps:
            if all_stopped_t is None:
                all_stopped_t = t
            elif t - all_stopped_t >= settle_s:
                break

    meta = {
        "source": "demo_platoon_edge.py",
        "fleet_size": int(fleet_size),
        "edge": f"{src_id}→{dst_id}",
        "src_node": src_id,
        "dst_node": dst_id,
        "edge_length": edge_len,
        "v_lead": v_lead,
        "spacing": spacing,
        "dt": dt,
        "n_ticks": tick,
        "n_records": len(rows),
    }
    return {"meta": meta, "frames": frames}, rows


# ============================================================================
# 内置自检：收敛早于到达 / 收敛后 gap≈spacing、speed≈v_lead / 终点停车堆叠 /
#            坐标严格在所选边上 / CSV 行数 = fleet_size × n_ticks
# ============================================================================
def self_check(frames, rows, spacing, v_lead, edge_len, pA=_CANVAS_A, pB=_CANVAS_B):
    meta = frames["meta"]
    fleet_size = int(meta["fleet_size"])
    n_ticks = int(meta["n_ticks"])

    assert len(frames["frames"]) == n_ticks, (len(frames["frames"]), n_ticks)
    assert len(rows) == fleet_size * n_ticks, (len(rows), fleet_size * n_ticks)
    assert meta["edge_length"] == edge_len

    # 1) 坐标严格在边 (pA→pB) 线段上：垂直距离≈0（画布单位）且在段内
    vx, vy = pB[0] - pA[0], pB[1] - pA[1]
    seg_len = np.hypot(vx, vy)
    seg2 = vx * vx + vy * vy
    for f in frames["frames"]:
        for c in f["fleet"]:
            x, y = c["position"]["x"], c["position"]["y"]
            cross = abs((x - pA[0]) * vy - (y - pA[1]) * vx)
            perp = cross / seg_len                      # 到线段所在直线的垂直距离（画布单位）
            tproj = ((x - pA[0]) * vx + (y - pA[1]) * vy) / seg2
            assert perp < 2e-2, (c["position"], round(perp, 4))
            assert -1e-6 <= tproj <= 1.0 + 1e-6, (c["position"], tproj)

    # 2) 收敛检测：所有车车间距≈spacing 且速度≈v_lead（leader 未停时）
    conv_tick = None
    for f in frames["frames"]:
        fl = f["fleet"]
        if fl[0]["speed"] < 1e-6:
            continue
        gaps_ok = all(abs(c["distance_to_front"] - spacing) < 0.5 for c in fl[1:])
        speeds_ok = all(abs(c["speed"] - v_lead) < 0.2 for c in fl)
        if gaps_ok and speeds_ok:
            conv_tick = f["tick"]
            break
    arrival_tick = int(np.ceil((edge_len - frames["frames"][0]["fleet"][0]["mileage"]) / v_lead))
    assert conv_tick is not None, "未检测到编队收敛"
    assert conv_tick < arrival_tick, (conv_tick, arrival_tick)
    print(f"  [自检] 收敛于 tick={conv_tick}，早于到达 tick≈{arrival_tick} ✓")

    # 3) 收敛后维持稳定：凡 leader 仍按巡航行驶的帧，车间距≈spacing、速度≈v_lead
    stable = 0
    for f in frames["frames"]:
        fl = f["fleet"]
        if f["tick"] < conv_tick or fl[0]["speed"] < v_lead - 0.05:
            continue
        for c in fl[1:]:
            assert abs(c["distance_to_front"] - spacing) < 0.5, c
            assert abs(c["speed"] - v_lead) < 0.2, c
        stable += 1
    assert stable > 0, "收敛后无稳定巡航帧"

    # 4) 终点停车堆叠：leader 到终点、速度≈0，跟随车停在其后
    last = frames["frames"][-1]
    assert abs(last["fleet"][0]["mileage"] - edge_len) < 1e-6, last["fleet"][0]["mileage"]
    assert last["fleet"][0]["speed"] < 0.01
    for c in last["fleet"][1:]:
        assert c["speed"] < 0.05, c
        assert c["mileage"] < last["fleet"][0]["mileage"], c

    # 5) leader 到终点距离单调不减至 0
    prev = None
    for f in frames["frames"]:
        d0 = f["fleet"][0]["distance_to_target"]
        if prev is not None:
            assert d0 <= prev + 1e-6, (d0, prev)
        prev = d0
    assert prev is not None and abs(prev) < 1e-6, prev
    print("  [自检] 稳定保持 / 终点停车堆叠 / 坐标在所选边上 / CSV 行数 全部通过 ✓")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="demo_platoon_edge",
                                description="单边 CAV 编队一致性收敛演示（成员 C）")
    p.add_argument("--fleet-size", type=int, default=4)
    p.add_argument("--v-lead", type=float, default=1.5)
    p.add_argument("--spacing", type=float, default=12.0)
    p.add_argument("--src", default=None, help="真实起点节点 id（缺省 cross_zh_south）")
    p.add_argument("--dst", default=None, help="真实终点节点 id（缺省 cross_zh_mid）")
    p.add_argument("--edge-len", type=float, default=None,
                   help="覆盖边物理长度 m（缺省取 graph_data.yaml）")
    p.add_argument("--synthetic", action="store_true",
                   help="用合成 A→B 迷你拓扑（不读真实地图）")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--html", default="platoon_animation.html")
    p.add_argument("--no-html", action="store_true", help="不自动生成 HTML")
    args = p.parse_args(argv)

    if args.synthetic:
        src_id, dst_id = _NODE_A, _NODE_B
        pA, pB = _CANVAS_A, _CANVAS_B
        edge_len = args.edge_len if args.edge_len else 300.0
        topo_for_html = None                       # 后面写 platoon_topo.yaml
    else:
        src_id = args.src or _DEFAULT_SRC
        dst_id = args.dst or _DEFAULT_DST
        graph = _load_graph()
        pA, pB, edge_len = _resolve_edge(graph, src_id, dst_id)
        if args.edge_len:
            edge_len = args.edge_len
        topo_for_html = _GRAPH_PATH

    print(f"== demo_platoon_edge: {args.fleet_size} 车 / 边 {src_id}→{dst_id} "
          f"({edge_len:.1f}m) / 巡航 {args.v_lead} m/s / 期望间距 {args.spacing}m ==")
    frames, rows = simulate(fleet_size=args.fleet_size, v_lead=args.v_lead,
                            edge_len=edge_len, spacing=args.spacing,
                            pA=pA, pB=pB, src_id=src_id, dst_id=dst_id)

    print("-- 内置自检 --")
    self_check(frames, rows, spacing=args.spacing, v_lead=args.v_lead,
               edge_len=edge_len, pA=pA, pB=pB)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_path = out_dir / "platoon_frames.json"
    csv_path = out_dir / "platoon_telemetry.csv"

    frames_path.write_text(json.dumps(frames, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["tick", "car_id", "role", "mileage",
                                          "speed", "gap_to_front",
                                          "front_distance_to_target"])
        w.writeheader()
        w.writerows(rows)

    print(f"  帧       -> {frames_path} ({frames_path.stat().st_size} bytes)")
    print(f"  遥测 CSV -> {csv_path} ({csv_path.stat().st_size} bytes)")
    print(f"  汇总: {frames['meta']['n_ticks']} 帧 / {len(rows)} 行遥测")

    if not args.no_html:
        if topo_for_html is None:
            topo_for_html = out_dir / "platoon_topo.yaml"
            _write_topo(topo_for_html, edge_len, args.fleet_size)
            print(f"  迷你拓扑 -> {topo_for_html}")
        else:
            print(f"  动画路网 -> {topo_for_html}（完整园区地图，高亮 {src_id}→{dst_id}）")
        print("-- 生成动画 --")
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "animate_fleet.py"),
             "--topology", str(topo_for_html),
             "--input", str(frames_path),
             "--telemetry", str(csv_path),
             "--out", args.html],
            check=True,
        )
    return 0


def _write_topo(path, edge_len, capacity):
    path.write_text(
        f"""nodes:
  {_NODE_A}:
    name: {_NODE_A}
    type: road
    x: {_CANVAS_A[0]}
    y: {_CANVAS_A[1]}
    has_traffic_light: false
    has_gate: false
    doorId: T1
  {_NODE_B}:
    name: {_NODE_B}
    type: road
    x: {_CANVAS_B[0]}
    y: {_CANVAS_B[1]}
    has_traffic_light: false
    has_gate: false
    doorId: T2
edges:
- edgeId: E1
  nodes:
  - {_NODE_A}
  - {_NODE_B}
  length: {edge_len}
  weight: 1.0
  capacity: {capacity}
""",
        encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
