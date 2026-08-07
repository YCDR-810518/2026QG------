# -*- coding: utf-8 -*-
"""demo_platoon_edge.py —— 折线 CAV 编队一致性收敛演示（成员 C，参考 test/code/cav.py）

在**真实园区地图**的一条折线路径（默认链式从 cross_zh_south 出发，6 条边）上跑 4 车编队：
- leader（CAV_L1）恒速 2.5 m/s；3 辆跟随车（CAV_F1..F3）用一致性控制
      dV = −(L+K)·X_e − (β·L + γ·K)·V_e
  （X_e = X − xL − R 位置误差，V_e = V − vL 速度误差，A 链式邻接，K 对 leader 权重）
- **控制跑在"展开成直线的弧长坐标"上**（折线首尾拼接成一条直线，编队间距/速度
  不受转角影响），到达终点前收敛为 30m 等距编队并保持稳定；
- **显示时按弧长参数化映射回真实折线**——leader 拐过节点后显示在下一段，
  跟随车仍正确显示在上一段；
- leader 到终点节点停车，跟随车收敛堆叠停车，全部停稳后再记 2s 结束；
- 每整秒产出一帧（schema 兼容 animate_fleet.py），并自动调用 animate_fleet.py
  （--topology graph_data.yaml 画完整园区路网）生成 platoon_animation.html。

最短路径对接：A 侧最短路径输出为**节点序列**（如 union_pack.json 的
vehicle_paths[].path，不含边），本模块用 graph_data.yaml 将相邻节点对**映射为边**
（含边编号 edge_ids）。可用 --path-json 直接读该文件。

用法：
    cd 项目目录
    python demo_platoon_edge.py
        # 默认链式：cross_zh_south → cross_zh_mid → cross_zh_north →
        # teacher_apt → hospital → supermarket → canteen_1（1~6 边，动画页内下拉选边数）
    python demo_platoon_edge.py --path-json data/json/union_pack.json [--src X --dst Y]
        # 直接读最短路径输出的节点序列跑编队
    python demo_platoon_edge.py --path "cross_zh_south,cross_zh_mid,cross_zh_north"  # 单路径
    python demo_platoon_edge.py --src canteen_1 --dst gate_east     # 单条真实边
    python demo_platoon_edge.py --synthetic                         # 旧合成 A→B 迷你模式

产出：
    链式（默认）：data/platoon_multi.json（1~K 段各自 frames+telemetry+edge_ids）
                  + platoon_animation.html
    单路径   ：data/platoon_frames.json + data/platoon_telemetry.csv + platoon_animation.html
    data/platoon_topo.yaml   仅 --synthetic 模式写 2 节点迷你拓扑
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
_DEFAULT_PATH = ("cross_zh_south", "cross_zh_mid")
# 默认链式路径（1~6 边，供动画页下拉选择）：cross_zh_south → cross_zh_mid →
# cross_zh_north → teacher_apt → hospital → supermarket → canteen_1
# 首边 462.7m，spacing=30 / v=2.5 下每个 k 都满足"终点前收敛"
_DEFAULT_CHAIN = ("cross_zh_south", "cross_zh_mid", "cross_zh_north",
                  "teacher_apt", "hospital", "supermarket", "canteen_1")


def _laplacian(A):
    return np.diag(A.sum(axis=1)) - A


def _load_graph(path=_GRAPH_PATH):
    """读 graph_data.yaml → (node_pos, edges)。

    node_pos : dict {id:(x,y)}
    edges    : dict {frozenset(节点对): (length_m, edgeId)}
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    node_pos = {nid: (float(attrs["x"]), float(attrs["y"]))
                for nid, attrs in data.get("nodes", {}).items()}
    edges = {}
    for e in data.get("edges", []):
        ns = e.get("nodes", [])
        if len(ns) == 2:
            edges[frozenset(ns)] = (float(e.get("length", 0.0)),
                                    str(e.get("edgeId", "")))
    return node_pos, edges


def _resolve_single_edge(graph, src, dst):
    """真实拓扑里的单条边 → (pA, pB, length, edgeId)。"""
    node_pos, edges = graph
    key = frozenset((src, dst))
    if key not in edges:
        raise ValueError(f"拓扑中不存在边 {src}→{dst}，请检查是否相邻")
    length, edge_id = edges[key]
    return node_pos[src], node_pos[dst], float(length), edge_id


def _resolve_path(graph, nodes):
    """真实拓扑里的折线路径（节点序列）→ (points, segs, edge_ids)。

    points   : list[(x,y)]   各节点画布坐标
    segs     : list[float]   相邻节点间的物理长度 m
    edge_ids : list[str]     相邻节点对应的边编号（节点→边映射）
    """
    node_pos, edges = graph
    for a, b in zip(nodes[:-1], nodes[1:]):
        if a not in node_pos or b not in node_pos:
            raise ValueError(f"节点不在拓扑中: {a} / {b}")
        if frozenset((a, b)) not in edges:
            raise ValueError(f"拓扑中不存在边 {a}→{b}，请检查是否相邻")
    points = [node_pos[n] for n in nodes]
    segs, edge_ids = [], []
    for a, b in zip(nodes[:-1], nodes[1:]):
        length, eid = edges[frozenset((a, b))]
        segs.append(float(length))
        edge_ids.append(eid)
    return points, segs, edge_ids


def _telemetry_from_rows(rows):
    """遥测行 → {car_id: {tick:[], speed:[], gap:[], dist:[]}}（供动画曲线面板）。"""
    tele = {}
    for r in rows:
        item = tele.setdefault(r["car_id"], {"tick": [], "speed": [], "gap": [], "dist": []})
        item["tick"].append(float(r["tick"]))
        item["speed"].append(float(r["speed"]))
        item["gap"].append(float(r["gap_to_front"]))
        item["dist"].append(float(r["front_distance_to_target"]))
    return tele


def _converged_tick(frames, spacing, v_lead):
    """首个满足"车间距≈spacing 且速度≈v_lead"的 tick；无则 None（与自检同判据）。"""
    for f in frames["frames"]:
        fl = f["fleet"]
        if fl[0]["speed"] < 1e-6:
            continue
        gaps_ok = all(abs(c["distance_to_front"] - spacing) < 0.5 for c in fl[1:])
        speeds_ok = all(abs(c["speed"] - v_lead) < 0.2 for c in fl)
        if gaps_ok and speeds_ok:
            return f["tick"]
    return None


_SHOW_AFTER_CONV_M = 30.0   # 底图 x 轴：收敛后再显示 30m 即停


def _x_max(conv, v_lead, n_ticks):
    """底图 x 轴上限：conv_tick + ceil(30m/速度)；无收敛则全范围。"""
    if conv is None:
        return int(n_ticks)
    return int(conv + np.ceil(_SHOW_AFTER_CONV_M / max(v_lead, 1e-6)))


def build_chain_bundle(graph, route_ids, fleet_size=4, v_lead=2.5, spacing=30.0,
                       vmax=6.0, k_leader=0.6, beta=1.2, gamma=0.8,
                       leader_start=60.0):
    """对同一链式路径的 1~K 段分别跑编队，打包成动画页多边数据。

    Returns
    -------
    dict
        {meta:{chain, n_edges, fleet_size, v_lead, spacing, source},
         paths:{"k": {route_nodes, total_path_length, segment_lengths,
                      frames:[...], telemetry:{car:{...}}}}}
    """
    points_all, segs_all, edge_ids_all = _resolve_path(graph, route_ids)
    paths = {}
    for k in range(1, len(segs_all) + 1):
        nodes = list(route_ids[:k + 1])
        points = points_all[:k + 1]
        segs = segs_all[:k]
        edge_ids = edge_ids_all[:k]
        frames, rows = simulate(fleet_size=fleet_size, v_lead=v_lead,
                                spacing=spacing, vmax=vmax, k_leader=k_leader,
                                beta=beta, gamma=gamma, leader_start=leader_start,
                                points=points, segs=segs, route_ids=nodes)
        print(f"  [链式 {k}/{len(segs_all)}] {k} 条边 "
              f"{'→'.join(nodes)} ({sum(segs):.1f}m) 边号 {edge_ids}")
        self_check(frames, rows, spacing=spacing, v_lead=v_lead,
                   points=points, segs=segs)
        conv = _converged_tick(frames, spacing, v_lead)
        x_max = _x_max(conv, v_lead, frames["meta"]["n_ticks"])
        paths[str(k)] = {
            "route_nodes": nodes,
            "total_path_length": round(float(sum(segs)), 3),
            "segment_lengths": [round(float(s), 3) for s in segs],
            "edge_ids": edge_ids,
            "converged_tick": conv,
            "x_max": x_max,
            "frames": frames["frames"],
            "telemetry": _telemetry_from_rows(rows),
        }
    meta = {
        "source": "demo_platoon_edge.py",
        "chain": list(route_ids),
        "n_edges": len(segs_all),
        "fleet_size": int(fleet_size),
        "v_lead": v_lead,
        "spacing": spacing,
    }
    return {"meta": meta, "paths": paths}


def _path_point(s, points, cum):
    """弧长 s → 折线画布坐标（分段线性，严格在折线上）。"""
    for i in range(len(points) - 1):
        if s <= cum[i + 1]:
            seg = cum[i + 1] - cum[i]
            t = (s - cum[i]) / seg if seg > 0 else 0.0
            x = points[i][0] + t * (points[i + 1][0] - points[i][0])
            y = points[i][1] + t * (points[i + 1][1] - points[i][1])
            return x, y
    return points[-1]


def simulate(fleet_size=4, v_lead=2.5, spacing=30.0,
             dt=0.1, amax=2.0, vmax=6.0, k_leader=0.6, beta=1.2, gamma=0.8,
             leader_start=60.0, stop_eps=0.05, settle_s=2.0, max_t=1200.0,
             points=None, segs=None, route_ids=None):
    """跑一遍折线编队，返回 (frames, rows)。

    Parameters
    ----------
    points : list[(x,y)]
        折线各节点画布坐标。
    segs : list[float]
        各段物理长度 m。
    route_ids : list[str]
        帧 path 的节点 id 序列。

    Returns
    -------
    frames : dict
        {meta, frames:[{tick, gate_id, total_path_length, path, fleet:[...]}]}
    rows : list of dict
        遥测行 {tick, car_id, role, mileage, speed, gap_to_front,
               front_distance_to_target}
    """
    if points is None or segs is None or route_ids is None:
        points, segs = [list(_CANVAS_A), list(_CANVAS_B)], [300.0]
        route_ids = [_NODE_A, _NODE_B]
    total_len = float(sum(segs))
    cum = np.concatenate(([0.0], np.cumsum(segs)))

    n = int(fleet_size) - 1                      # 跟随车数
    if n <= 0:
        raise ValueError("fleet_size 至少为 2")
    if v_lead > vmax:
        raise ValueError(f"v_lead({v_lead}) 不能超过 vmax({vmax})")
    # leader 起步须 ≥ 整支编队长度（间距×跟随车数），否则跟随车期望位置落空/为负
    leader_start = max(float(leader_start), spacing * n)
    R = np.array([-spacing * i for i in range(1, n + 1)], dtype=float)
    A = np.zeros((n, n))
    for i in range(1, n):
        A[i, i - 1] = 1.0                        # 链式：后车感知前车
    K = np.eye(n) * k_leader
    L = _laplacian(A)

    xL = float(leader_start)
    X = xL + R + np.linspace(2.0, -3.0, n)      # 贴近期望编队 + 小扰动（n 通用）
    X = np.clip(X, 0.0, total_len)
    V = np.full(n, min(v_lead, vmax))           # 初始速度 = 默认巡航 5 m/s
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
        d_target = [total_len - s for s in s_all]
        pos = [_path_point(s, points, cum) for s in s_all]
        fleet_items = []
        for i, cid in enumerate(car_ids):
            role = "leader" if i == 0 else "follower"
            gap = 0.0 if i == 0 else s_all[i - 1] - s_all[i]
            fleet_items.append({
                "car_id": cid,
                "role": role,
                "position": {"x": round(pos[i][0], 2), "y": round(pos[i][1], 2)},
                "speed": round(vL if i == 0 else V[i - 1], 3),
                "acceleration": round(0.0 if i == 0 else acc[i - 1], 3),
                "distance_to_front": round(gap, 3),
                "mileage": round(s_all[i], 3),
                "distance_to_target": round(d_target[i], 3),
            })
        frames.append({
            "tick": tick,
            "gate_id": route_ids[0],
            "total_path_length": round(total_len, 3),
            "path": {"start_node_id": route_ids[0], "end_node_id": route_ids[-1],
                     "route_nodes": list(route_ids)},
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
        if xL >= total_len:
            leader_stopped = True
        vL = 0.0 if leader_stopped else v_lead
        X_e = X - xL - R
        V_e = V - vL
        acc = -(L + K) @ X_e - (beta * L + gamma * K) @ V_e
        acc = np.clip(acc, -amax, amax)
        X = X + V * dt
        V = np.clip(V + acc * dt, 0.0, vmax)
        X = np.clip(X, 0.0, total_len)
        xL = min(xL + vL * dt, total_len)
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
        "path_nodes": list(route_ids),
        "segment_lengths": [round(s, 3) for s in segs],
        "total_path_length": round(total_len, 3),
        "v_lead": v_lead,
        "spacing": spacing,
        "dt": dt,
        "n_ticks": tick,
        "n_records": len(rows),
    }
    return {"meta": meta, "frames": frames}, rows


# ============================================================================
# 内置自检：坐标严格在折线上（含反推弧长一致） / 收敛早于到达 / 稳定保持 /
#            终点停车堆叠 / 跨节点帧存在 / CSV 行数 = fleet_size × n_ticks
# ============================================================================
def _min_dist_to_seg(x, y, p1, p2):
    """点到线段的最小垂直距离（画布单位）。"""
    vx, vy = p2[0] - p1[0], p2[1] - p1[1]
    seg2 = vx * vx + vy * vy
    if seg2 == 0:
        return np.hypot(x - p1[0], y - p1[1])
    t = ((x - p1[0]) * vx + (y - p1[1]) * vy) / seg2
    t = max(0.0, min(1.0, t))
    px, py = p1[0] + t * vx, p1[1] + t * vy
    return np.hypot(x - px, y - py)


def _arc_length(x, y, points, cum):
    """画布坐标 → 弧长（m）：投影到最近线段，取其弧长。"""
    best, best_s = None, None
    for i in range(len(points) - 1):
        vx = points[i + 1][0] - points[i][0]
        vy = points[i + 1][1] - points[i][1]
        seg2 = vx * vx + vy * vy
        if seg2 == 0:
            continue
        t = ((x - points[i][0]) * vx + (y - points[i][1]) * vy) / seg2
        t = max(0.0, min(1.0, t))
        px, py = points[i][0] + t * vx, points[i][1] + t * vy
        d = np.hypot(x - px, y - py)
        s = cum[i] + t * (cum[i + 1] - cum[i])
        if best is None or d < best:
            best, best_s = d, s
    return best_s, best


def self_check(frames, rows, spacing, v_lead, points, segs):
    meta = frames["meta"]
    fleet_size = int(meta["fleet_size"])
    n_ticks = int(meta["n_ticks"])
    total_len = float(sum(segs))
    cum = np.concatenate(([0.0], np.cumsum(segs)))

    assert len(frames["frames"]) == n_ticks, (len(frames["frames"]), n_ticks)
    assert len(rows) == fleet_size * n_ticks, (len(rows), fleet_size * n_ticks)
    assert abs(meta["total_path_length"] - total_len) < 1e-6

    # 1) 坐标严格在折线上（到折线最小距离 < 容差）且反推弧长 ≈ mileage
    for f in frames["frames"]:
        for c in f["fleet"]:
            x, y = c["position"]["x"], c["position"]["y"]
            dmin = min(_min_dist_to_seg(x, y, points[i], points[i + 1])
                       for i in range(len(points) - 1))
            assert dmin < 0.05, (c["position"], round(float(dmin), 4))
            s_recon, _ = _arc_length(x, y, points, cum)
            assert abs(s_recon - c["mileage"]) < 0.6, (c["position"], c["mileage"], round(s_recon, 2))

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
    arrival_tick = int(np.ceil((total_len - frames["frames"][0]["fleet"][0]["mileage"]) / v_lead))
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
    assert abs(last["fleet"][0]["mileage"] - total_len) < 1e-6, last["fleet"][0]["mileage"]
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

    # 6) 跨节点帧存在（仅多段路径）：leader 已到下一段，部分跟随车仍在本段
    if len(segs) >= 2:
        l1 = cum[1]
        cross = [f for f in frames["frames"]
                 if f["fleet"][0]["mileage"] > l1
                 and any(c["mileage"] < l1 for c in f["fleet"][1:])]
        assert cross, "未捕获 leader 在第2段 / 跟随车在第1段的帧"
        print(f"  [自检] 跨节点帧 {len(cross)} 个（leader 第2段 / 跟随车第1段）✓")
    else:
        cross = []
    print("  [自检] 折线定位 / 弧长一致 / 稳定保持 / 终点停车堆叠 / CSV 行数 全部通过 ✓")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="demo_platoon_edge",
                                description="折线 CAV 编队一致性收敛演示（成员 C）")
    p.add_argument("--fleet-size", type=int, default=4)
    p.add_argument("--v-lead", type=float, default=2.5)
    p.add_argument("--spacing", type=float, default=30.0)
    p.add_argument("--vmax", type=float, default=6.0)
    p.add_argument("--k-leader", type=float, default=0.6)
    p.add_argument("--beta", type=float, default=1.2)
    p.add_argument("--gamma", type=float, default=0.8)
    p.add_argument("--leader-start", type=float, default=60.0)
    p.add_argument("--path", default=None,
                   help="真实折线节点序列（逗号分隔，相邻节点须成边），"
                        "如 cross_zh_south,cross_zh_mid,cross_zh_north")
    p.add_argument("--chain", default=None,
                   help="链式路径（逗号分隔）：对 1~K 段分别出帧，动画页内下拉选择边数；"
                        "缺省用内置默认链（cross_zh_south→…→canteen_1，1~6 边）")
    p.add_argument("--path-json", default=None,
                   help="读取最短路径输出 JSON（如 data/json/union_pack.json），"
                        "取 vehicle_paths[].path（节点序列）映射成边并跑编队；"
                        "配合 --src/--dst 过滤指定项，缺省取第一条")
    p.add_argument("--src", default=None, help="单条真实边起点（与 --dst 配对），或 path-json 过滤起点")
    p.add_argument("--dst", default=None, help="单条真实边终点（与 --src 配对），或 path-json 过滤终点")
    p.add_argument("--edge-len", type=float, default=None,
                   help="仅 --synthetic 模式生效：迷你边长度 m")
    p.add_argument("--synthetic", action="store_true",
                   help="用合成 A→B 迷你拓扑（不读真实地图）")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--html", default="platoon_animation.html")
    p.add_argument("--no-html", action="store_true", help="不自动生成 HTML")
    args = p.parse_args(argv)

    sim_kw = dict(v_lead=args.v_lead, spacing=args.spacing, vmax=args.vmax,
                  k_leader=args.k_leader, beta=args.beta, gamma=args.gamma,
                  leader_start=args.leader_start)

    # ---- 链式模式（默认）：多路径打包，动画页内下拉选边数 ----
    use_chain = (not args.synthetic and not args.path and not args.path_json
                 and not args.src and not args.dst)
    if use_chain:
        chain = ([n.strip() for n in args.chain.split(",") if n.strip()]
                 if args.chain else list(_DEFAULT_CHAIN))
        if len(chain) < 2:
            raise SystemExit("--chain 至少需要 2 个节点")
        graph = _load_graph()
        print(f"== demo_platoon_edge: 链式 {len(chain)-1} 条边 "
              f"{'→'.join(chain)} / {args.fleet_size} 车 / "
              f"巡航 {args.v_lead} m/s / 期望间距 {args.spacing}m ==")
        bundle = build_chain_bundle(graph, chain, fleet_size=args.fleet_size,
                                    **sim_kw)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        multi_path = out_dir / "platoon_multi.json"
        multi_path.write_text(json.dumps(bundle, ensure_ascii=False),
                              encoding="utf-8")
        print(f"  数据包 -> {multi_path} ({multi_path.stat().st_size} bytes, "
              f"{bundle['meta']['n_edges']} 组路径)")
        if not args.no_html:
            print("-- 生成动画 --")
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "animate_fleet.py"),
                 "--topology", _GRAPH_PATH,
                 "--input", str(multi_path),
                 "--out", args.html],
                check=True,
            )
        return 0

    edge_ids = None
    if args.synthetic:
        route_ids = [_NODE_A, _NODE_B]
        points = [list(_CANVAS_A), list(_CANVAS_B)]
        segs = [args.edge_len if args.edge_len else 300.0]
        edge_ids = ["E1"]
        topo_for_html = None                       # 后面写 platoon_topo.yaml
    elif args.path_json:
        route_ids = _path_from_json(args.path_json, src=args.src, dst=args.dst)
        graph = _load_graph()
        points, segs, edge_ids = _resolve_path(graph, route_ids)
        topo_for_html = _GRAPH_PATH
    elif args.path:
        route_ids = [n.strip() for n in args.path.split(",") if n.strip()]
        if len(route_ids) < 2:
            raise SystemExit("--path 至少需要 2 个节点")
        graph = _load_graph()
        points, segs, edge_ids = _resolve_path(graph, route_ids)
        topo_for_html = _GRAPH_PATH
    elif args.src or args.dst:
        src_id = args.src or "cross_zh_south"
        dst_id = args.dst or "cross_zh_mid"
        graph = _load_graph()
        pA, pB, length, edge_id = _resolve_single_edge(graph, src_id, dst_id)
        route_ids = [src_id, dst_id]
        points = [list(pA), list(pB)]
        segs = [length]
        edge_ids = [edge_id]
        topo_for_html = _GRAPH_PATH
    else:
        route_ids = list(_DEFAULT_PATH)
        graph = _load_graph()
        points, segs, edge_ids = _resolve_path(graph, route_ids)
        topo_for_html = _GRAPH_PATH

    total_len = float(sum(segs))
    print(f"== demo_platoon_edge: {args.fleet_size} 车 / 路径 "
          f"{'→'.join(route_ids)} ({total_len:.1f}m, {len(segs)} 段, 边号 {edge_ids}) / "
          f"巡航 {args.v_lead} m/s / 期望间距 {args.spacing}m ==")
    frames, rows = simulate(fleet_size=args.fleet_size,
                            points=points, segs=segs, route_ids=route_ids,
                            **sim_kw)
    frames["meta"]["edge_ids"] = edge_ids

    print("-- 内置自检 --")
    self_check(frames, rows, spacing=args.spacing, v_lead=args.v_lead,
               points=points, segs=segs)
    conv = _converged_tick(frames, args.spacing, args.v_lead)
    frames["meta"]["converged_tick"] = conv
    frames["meta"]["x_max"] = _x_max(conv, args.v_lead, frames["meta"]["n_ticks"])

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
            _write_topo(topo_for_html, segs[0], args.fleet_size)
            print(f"  迷你拓扑 -> {topo_for_html}")
        else:
            print(f"  动画路网 -> {topo_for_html}（完整园区地图，高亮 "
                  f"{'→'.join(route_ids)}）")
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


def _path_from_json(json_path, src=None, dst=None):
    """从最短路径输出 JSON（union_pack 风格）取节点序列。

    结构：{... , "vehicle_paths": [{src, dst, path:[node_id,...], travelTime}]}
    返回 path 节点序列；--src/--dst 过滤匹配项，缺省取第一条。
    """
    import json as _json
    with open(json_path, "r", encoding="utf-8") as f:
        data = _json.load(f)
    rows = data.get("vehicle_paths", []) if isinstance(data, dict) else []
    if not rows:
        raise SystemExit(f"{json_path} 中没有 vehicle_paths 字段")
    for r in rows:
        if src and r.get("src") != src:
            continue
        if dst and r.get("dst") != dst:
            continue
        path = r.get("path") or []
        if len(path) < 2:
            continue
        print(f"  [path-json] 取 {r.get('src')} → {r.get('dst')} "
              f"（{len(path)-1} 条边, travelTime={r.get('travelTime')}）")
        return list(path)
    avail = sorted({f"{r.get('src')}→{r.get('dst')}" for r in rows})
    raise SystemExit(f"未匹配到 src/dst={src}/{dst} 的路径，可用项（前10）: "
                     f"{avail[:10]}")


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
