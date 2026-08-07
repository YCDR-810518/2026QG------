# -*- coding: utf-8 -*-
"""micro_fleet.py —— 微观编队实时帧生产（成员 C · FR-12/FR-27）

对齐《CAV小车编队接口定义文档.md》：演示窗口（config micro_fleet.enabled=true）启用时，
由 F 侧每 interval tick（1s）调用本模块，从**真实引擎当前状态**挑选
"车多的大门 → 固定终点（config micro_fleet.dst_node_id）"的最多 fleet_size 辆在途车，
组装成前端契约帧；随后经 sender/main 每 tick 发射至 B 后端
（POST /api/v1/sim/micro-fleet，只存最新帧），E 每秒轮询渲染 4 车编队 + routeNodes 高亮。

帧内字段（snake_case，B 的 View 层转 camelCase）：
    fleet[i] = {car_id, role, position:{x,y}, speed, acceleration, distance_to_front}

依赖：仅 numpy + 引擎现有稳定接口（pool.data / pool.paths / topology.xy / edge_length）。
**不依赖** trip 钩子（F-1）与 on_tick（F-3）——本模块可独立单测（见 __main__）。

用法：
    from micro_fleet import collect_micro_fleet
    frame = collect_micro_fleet(eng, fleet_size=4, dst_node_id="canteen_1")
"""
import numpy as np

_DT = 1.0  # 1 tick = 1 s（与引擎 dt 一致）


def _route_mileage(topo, path, cur_node, edge_pos):
    """车辆沿路径已行驶里程（m）：到达 cur_node 之前的边累计 + 当前边内偏移。

    path : np.ndarray（节点序号序列，含 src 与 dst）
    cur_node : int 当前所在节点序号（WAIT_SRC/DWELL=节点上；TRAVEL=当前边起点；
               WAIT_SIGNAL=排队节点，edge_pos=上一边长 → 里程正确）。
    """
    pos = int(np.nonzero(path == cur_node)[0][0]) if np.any(path == cur_node) else 0
    total = 0.0
    for i in range(pos):
        l = topo.edge_length[path[i], path[i + 1]]
        total += float(l) if l and l > 0 else 0.0
    return total + float(edge_pos)


def _position(topo, cur_node, edge_target, edge_pos, edge_length):
    """沿当前边在节点坐标（0-100 画布系，与前端 topology.json 一致）间线性插值。"""
    if edge_target < 0:
        return {"x": round(float(topo.xy[cur_node, 0]), 2),
                "y": round(float(topo.xy[cur_node, 1]), 2)}
    length = float(edge_length) if edge_length and edge_length > 0 else 1.0
    frac = float(min(max(edge_pos, 0.0), length)) / length
    xa, ya = topo.xy[cur_node]
    xb, yb = topo.xy[edge_target]
    return {"x": round(float(xa + (xb - xa) * frac), 2),
            "y": round(float(ya + (yb - ya) * frac), 2)}


def collect_micro_fleet(eng, fleet_size=4, dst_node_id="canteen_1",
                        prev_speeds=None):
    """从真实引擎当前状态组装一帧编队数据。

    Parameters
    ----------
    eng : TickEngine
        运行中的引擎（读 pool.data / pool.paths / topology）。
    fleet_size : int
        目标编队车辆数（实际在途不足时按 1~fleet_size 输出）。
    dst_node_id : str
        固定 O-D 终点（真实节点编码；zone_canteen 不存在）。
    prev_speeds : dict, optional
        车辆 id → 上一帧速度（用于加速度差分）；缺省用模块级缓存。
        传 dict() 可隔离状态（单测用）。

    Returns
    -------
    dict
        {tick, gate_id, path:{start_node_id, end_node_id, route_nodes},
         fleet:[{car_id, role, position:{x,y}, speed, acceleration,
                 distance_to_front}]}；无候选时 fleet=[]、gate_id=None。
    """
    if prev_speeds is None:
        prev_speeds = _prev_speeds
    topo = eng.topology
    data = eng.pool.data
    dst_idx = topo.node(dst_node_id)
    if dst_idx < 0:
        return {"tick": eng._tick, "gate_id": None, "path": None, "fleet": []}

    gate_idx = set(int(g) for g in topo.gate_nodes)
    cand_mask = (data["active"] & (data["kind"] == 1)
                 & (data["dst_node"] == dst_idx))
    cand_slots = np.nonzero(cand_mask)[0]
    if cand_slots.size == 0:
        return {"tick": eng._tick, "gate_id": None, "path": None, "fleet": []}

    # 挑"车多的大门"：按 src_node 分组取候选最多的门
    srcs = data["src_node"][cand_slots]
    gate_counts = {}
    for g in gate_idx:
        gate_counts[g] = int(np.sum(srcs == g))
    best_gate = max(gate_counts, key=gate_counts.get) if gate_counts else None
    if best_gate is None or gate_counts[best_gate] == 0:
        return {"tick": eng._tick, "gate_id": None, "path": None, "fleet": []}

    pool_mask = cand_mask & (data["src_node"] == best_gate)
    slots = np.nonzero(pool_mask)[0]

    # 按沿路里程从前往后排，取前 fleet_size 辆
    rows = []
    for s in slots:
        path = eng.pool.paths[int(s)]
        if path is None or path.size < 2:
            continue
        mileage = _route_mileage(topo, path, int(data["cur_node"][s]),
                                 float(data["edge_pos"][s]))
        rows.append((mileage, int(s), path))
    rows.sort(key=lambda r: -r[0])
    rows = rows[:int(fleet_size)]

    # 组装 fleet（leader 在最前）
    fleet = []
    for i, (mileage, s, path) in enumerate(rows):
        vid = int(data["id"][s])
        speed = float(data["speed"][s])
        prev_v = prev_speeds.get(vid, speed)
        acc = (speed - prev_v) / _DT
        prev_speeds[vid] = speed
        fleet.append({
            "car_id": "CAV_L1" if i == 0 else f"CAV_F{i}",
            "role": "leader" if i == 0 else "follower",
            "position": _position(topo, int(data["cur_node"][s]),
                                  int(data["edge_target"][s]),
                                  float(data["edge_pos"][s]),
                                  float(topo.edge_length[data["cur_node"][s],
                                                          data["edge_target"][s]])
                                  if data["edge_target"][s] >= 0 else 1.0),
            "speed": round(speed, 3),
            "acceleration": round(acc, 3),
            "distance_to_front": round(0.0 if i == 0 else rows[i - 1][0] - mileage, 3),
        })

    route = eng.pool.paths[rows[0][1]]
    return {
        "tick": int(eng._tick),
        "gate_id": topo.node_ids[best_gate],
        "path": {
            "start_node_id": topo.node_ids[best_gate],
            "end_node_id": dst_node_id,
            "route_nodes": [topo.node_ids[int(n)] for n in route],
        },
        "fleet": fleet,
    }


_prev_speeds = {}


def clear_speed_cache():
    """清空加速度差分缓存（新仿真会话开始时调用）。"""
    _prev_speeds.clear()


# ============================================================================
# 本地自测：真实 EntityPool + 真实拓扑，纯函数逻辑验证
# ============================================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from entities import EntityPool
    from topology import Topology

    topo = Topology()
    pool = EntityPool(capacity=12)

    class _Eng:
        pass

    eng = _Eng()
    eng.topology = topo
    eng.pool = pool
    eng._tick = 500

    dst = topo.node("canteen_1")
    gs = topo.node("gate_south")
    gw = topo.node("gate_west")
    route = topo.path(gs, dst, kind=1)

    # 4 辆车从 gate_south → canteen_1（同一路径，不同进度）
    slots = pool.allocate(5)
    for i, s in enumerate(slots[:4]):
        pool.set_path(s, route)
        pool.data["kind"][s] = 1
        pool.data["state"][s] = 1
        pool.data["src_node"][s] = gs
        pool.data["dst_node"][s] = dst
        pool.data["cur_node"][s] = gs
        pool.data["edge_target"][s] = int(route[1])
        pool.data["edge_pos"][s] = 10.0 + 15.0 * i
        pool.data["speed"][s] = 3.0 + 0.5 * i
    # 第 5 辆从 gate_west → canteen_1（应被"车多大门"逻辑排除在外）
    pool.set_path(slots[4], topo.path(gw, dst, kind=1))
    pool.data["kind"][slots[4]] = 1
    pool.data["state"][slots[4]] = 1
    pool.data["src_node"][slots[4]] = gw
    pool.data["dst_node"][slots[4]] = dst
    pool.data["cur_node"][slots[4]] = gw
    pool.data["edge_target"][slots[4]] = int(topo.path(gw, dst, kind=1)[1])
    pool.data["edge_pos"][slots[4]] = 5.0
    pool.data["speed"][slots[4]] = 4.0

    state = {}
    frame = collect_micro_fleet(eng, fleet_size=4, dst_node_id="canteen_1",
                                prev_speeds=state)
    assert frame["gate_id"] == "gate_south", frame["gate_id"]
    assert len(frame["fleet"]) == 4, frame["fleet"]
    assert frame["path"]["end_node_id"] == "canteen_1"
    assert frame["path"]["route_nodes"][0] == "gate_south"
    assert frame["path"]["route_nodes"][-1] == "canteen_1"
    assert frame["fleet"][0]["role"] == "leader"
    assert frame["fleet"][0]["car_id"] == "CAV_L1"
    assert all(f["car_id"].startswith("CAV_F") for f in frame["fleet"][1:])
    # leader 与最后一辆车位置不同（沿路进度不同；x 不一定单调，路径会拐弯）
    assert frame["fleet"][0]["position"] != frame["fleet"][-1]["position"]
    assert frame["fleet"][0]["distance_to_front"] == 0.0
    assert all(f["distance_to_front"] > 0 for f in frame["fleet"][1:])
    # 坐标在 0-100 画布范围
    for f in frame["fleet"]:
        assert 0 <= f["position"]["x"] <= 100 and 0 <= f["position"]["y"] <= 100, f

    # 加速度差分：第二帧应反映速度变化
    frame2 = collect_micro_fleet(eng, fleet_size=4, dst_node_id="canteen_1",
                                 prev_speeds=state)
    assert frame2["fleet"][0]["acceleration"] == 0.0  # 速度未变 → 0

    # 空场景
    pool2 = EntityPool(capacity=4)
    eng2 = _Eng()
    eng2.topology = topo
    eng2.pool = pool2
    eng2._tick = 600
    empty = collect_micro_fleet(eng2, fleet_size=4, dst_node_id="canteen_1",
                                prev_speeds={})
    assert empty["fleet"] == [] and empty["gate_id"] is None

    # 非法终点
    bad = collect_micro_fleet(eng, fleet_size=4, dst_node_id="zone_canteen",
                              prev_speeds={})
    assert bad["fleet"] == [] and bad["path"] is None

    print("帧示例:", frame["gate_id"], "| 4辆 | 路径",
          frame["path"]["route_nodes"][0], "→", frame["path"]["route_nodes"][-1])
    for f in frame["fleet"]:
        print(f"  {f['car_id']:8s} {f['role']:8s} pos={f['position']} "
              f"v={f['speed']} a={f['acceleration']} gap={f['distance_to_front']}")
    print("selftest OK: 挑车多大门 / 同路径编队 / leader排序 / 坐标插值 / "
          "加速度差分 / 空与非法终点兜底 全部通过 ✓")
