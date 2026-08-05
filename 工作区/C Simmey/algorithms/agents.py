# -*- coding: utf-8 -*-
"""agents.py —— MAS 多智能体系统（成员 C · FR-11）

组成：
- VehicleAgent  车辆智能体（IDM 对照组 / CAV 实验组）
- GateAgent     门闸智能体
- TickEngine    tick 推演引擎（仿真本体，不含 sklearn 接口）

对外约定：
- 速度单位统一 m/s（与 F 仿真引擎口径一致），指标换算由上层模块负责；
- 拓扑来源：F 的 Topology（自动适配）或内置 _MiniTopo（A/F 未就绪时的兜底）；
- 车辆计划来源：F 的 vehicles.csv 行（id / birth_tick / src_node / dst_node / is_internal）。

用法：
    engine = TickEngine(topo, flow_config, vehicles_plan)
    trip_logs = engine.run(horizon)
"""
import heapq
import logging
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# IDM 默认参数（构造参数与模块常量全大写+下划线）
DEFAULT_IDM_PARAMS = {
    "v0": 5.0,      # 期望速度 m/s（园区巡航，与 F 引擎 _VEH_SPEED 一致）
    "a_max": 1.5,   # 最大加速度 m/s^2
    "b": 2.0,       # 舒适减速度 m/s^2
    "s0": 2.0,      # 最小安全间距 m
    "t_head": 1.5,  # 跟车时距 s
}
CAV_CTH = 0.8                # CAV 恒定车头时距 s（编队跟驰）
DELAY_SPEED_THRESHOLD = 1.39  # 5 km/h 以下视为滞留


class VehicleAgent:
    """车辆智能体（FR-11）。

    Parameters
    ----------
    vehicle_id : str
        车辆唯一标识。
    birth_tick : int
        出生（进入路网）时刻，1 tick = 1 s。
    target_route : list of str
        目标路线（节点 ID 有序列表，含起点与终点，来自宏观最短路径）。
    is_internal : bool, default=False
        是否园区内部车（vehicles.csv 的 is_internal，映射 guest/staff）。
    is_emergency : bool, default=False
        应急车，不受门闸关闭限制。
    idm_params : dict, optional
        IDM/巡航参数（键：v0 / a_max / b / s0 / t_head），缺省用模块默认值。

    Attributes
    ----------
    speed : float
        当前车速（m/s）。
    route_index : int
        当前所在节点序号（target_route[route_index] 为所在节点）。
    edge_pos : float
        当前路段内已行驶距离（m）。
    delay_time : float
        滞留累计时长（s）。
    node_delay_ : dict
        节点 ID → 滞留秒数。
    node_speed_sum_ / node_time_ : dict
        节点 ID → 时间加权速度累计（Σspeed×dt）与累计时长（Σdt）。
    crossed_nodes_ : list of dict
        过点记录 [{node_id, tick, speed}]。
    arrived_ : bool
        是否已到达终点。
    finish_tick_ : int or None
        到达时刻。
    """

    def __init__(
        self,
        vehicle_id: str,
        birth_tick: int,
        target_route: List[str],
        is_internal: bool = False,
        is_emergency: bool = False,
        idm_params: Optional[Dict[str, float]] = None,
    ):
        self.vehicle_id = vehicle_id
        self.birth_tick = birth_tick
        self.target_route = list(target_route)
        self.is_internal = is_internal
        self.is_emergency = is_emergency
        self.idm_params = dict(DEFAULT_IDM_PARAMS) if idm_params is None else dict(idm_params)

        self.speed = 0.0
        self.route_index = 0
        self.edge_pos = 0.0
        self.delay_time = 0.0
        self.arrived_ = False
        self.finish_tick_ = None
        self.crossed_nodes_ = []
        self.node_delay_ = {}
        self.node_speed_sum_ = {}
        self.node_time_ = {}

    @property
    def active(self) -> bool:
        return not self.arrived_ and self.route_index < len(self.target_route) - 1

    def update_idm(self, leader: Optional["VehicleAgent"], dt: float):
        """经典 IDM 跟驰（对照组）：每车独立决策，易减速排队。"""
        v = self.speed
        p = self.idm_params
        if leader is None:
            gap, dv = np.inf, 0.0
        else:
            gap = leader.edge_pos - self.edge_pos
            dv = v - leader.speed
        s_star = p["s0"] + max(0.0, v * p["t_head"] + v * dv / (2 * np.sqrt(p["a_max"] * p["b"])))
        acc = p["a_max"] * (1 - (v / p["v0"]) ** 4 - (s_star / max(gap, 1e-6)) ** 2)
        self.speed = max(0.0, v + acc * dt)

    def update_cav(self, leader: Optional["VehicleAgent"], dt: float, cth: float = CAV_CTH):
        """CAV 车联网协同（实验组）：编队跟 leader + CTH 恒定车头时距，不自由急刹。"""
        v = self.speed
        p = self.idm_params
        if leader is None:
            acc = p["a_max"] * (1 - (v / p["v0"]) ** 4)
        else:
            gap = leader.edge_pos - self.edge_pos
            target_gap = v * cth + p["s0"]
            acc = 0.6 * (leader.speed - v) + 0.4 * (gap - target_gap)
            acc = np.clip(acc, -p["b"], p["a_max"])
        self.speed = max(0.0, v + acc * dt)

    def advance(self, edge_length: float, dt: float) -> bool:
        """沿当前路段推进；到达路段末端返回 True，等待引擎裁决过节点/过门闸。"""
        if not self.active:
            return False
        self.edge_pos += self.speed * dt
        if self.edge_pos >= edge_length:
            self.edge_pos = edge_length
            return True
        return False

    def cross_node(self, tick: int):
        """跨过当前节点（引擎放行后调用），记录过点数据。"""
        node = self.target_route[self.route_index + 1]
        self.crossed_nodes_.append({"node_id": node, "tick": tick, "speed": self.speed})
        self.route_index += 1
        self.edge_pos = 0.0
        if self.route_index >= len(self.target_route) - 1:
            self.arrived_ = True
            self.finish_tick_ = tick
            self.speed = 0.0

    def accumulate_delay(self, dt: float):
        """车速低于阈值（5 km/h）时累计滞留时长，归属到当前所在节点。"""
        if self.speed < DELAY_SPEED_THRESHOLD:
            self.delay_time += dt
            node = self.target_route[self.route_index]
            self.node_delay_[node] = self.node_delay_.get(node, 0.0) + dt

    def record_speed(self, node: str, speed: float, dt: float):
        """累计时间加权速度样本（当前所在节点的平均速度统计）。"""
        self.node_speed_sum_[node] = self.node_speed_sum_.get(node, 0.0) + speed * dt
        self.node_time_[node] = self.node_time_.get(node, 0.0) + dt


class GateAgent:
    """门闸智能体（FR-11）。

    Parameters
    ----------
    gate_id : str
        门闸节点 ID。

    Attributes
    ----------
    is_open : bool
        当前是否放行。
    passed_count_ : int
        累计放行车辆数。
    """

    def __init__(self, gate_id: str):
        self.gate_id = gate_id
        self.is_open = True
        self.passed_count_ = 0

    def set_status(self, is_open: bool):
        """切换开/闭状态（供上层 GatePolicyController 联动）。"""
        self.is_open = bool(is_open)
        logger.info("[agents.GateAgent.set_status] gate=%s is_open=%s", self.gate_id, self.is_open)

    def try_pass(self, vehicle: VehicleAgent) -> bool:
        """尝试放行；关闭时拒绝（应急车除外），返回是否通过。"""
        if vehicle.is_emergency or self.is_open:
            self.passed_count_ += 1
            return True
        return False


class _MiniTopo:
    """内置兜底拓扑（A/F 的拓扑未就绪时使用，Dijkstra 最短路径）。"""

    def __init__(self, nodes: List[str], edges: List[Any]):
        self.node_ids = list(nodes)
        self._edges: Dict[tuple, float] = {}
        if isinstance(edges, dict):
            for (a, b), length in edges.items():
                self._edges[(a, b)] = float(length)
                self._edges[(b, a)] = float(length)
        else:
            for a, b, length in edges:
                self._edges[(a, b)] = float(length)
                self._edges[(b, a)] = float(length)
        self._adj = {n: [] for n in self.node_ids}
        for a, b in self._edges:
            self._adj[a].append(b)
        self.gate_nodes = [n for n in self.node_ids if str(n).startswith("gate")]

    def edge_len(self, a: str, b: str) -> float:
        return self._edges.get((a, b), -1.0)

    def path(self, src: str, dst: str) -> List[str]:
        if src == dst:
            return [src]
        dist = {n: np.inf for n in self.node_ids}
        prev = {}
        dist[src] = 0.0
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if u == dst:
                break
            for w in self._adj.get(u, []):
                nd = d + self._edges[(u, w)]
                if nd < dist[w]:
                    dist[w] = nd
                    prev[w] = u
                    heapq.heappush(pq, (nd, w))
        if not np.isfinite(dist[dst]):
            raise ValueError(f"无路径: {src} → {dst}")
        route = [dst]
        while route[-1] != src:
            route.append(prev[route[-1]])
        route.reverse()
        return route


class _FTopoAdapter:
    """把 F 的 Topology 适配成 agents 需要的按节点名称接口。"""

    def __init__(self, topo: Any):
        self._t = topo

    @property
    def node_ids(self) -> List[str]:
        return self._t.node_ids

    @property
    def gate_nodes(self) -> List[str]:
        return [self._t.node_name(i) for i in getattr(self._t, "gate_nodes", [])]

    def edge_len(self, a: str, b: str) -> float:
        return self._t.edge_len(self._t.node(a), self._t.node(b))

    def path(self, src: str, dst: str) -> List[str]:
        i, j = self._t.node(src), self._t.node(dst)
        if i < 0 or j < 0:
            raise ValueError(f"未知节点: {src} / {dst}")
        return [self._t.node_name(k) for k in self._t.path(i, j)]


class TickEngine:
    """tick 推演引擎（FR-11 仿真本体，供 CavSimulator.fit/predict 调用）。

    Parameters
    ----------
    topo : object or None
        拓扑提供方：至少提供 path(src, dst) -> list[str]、edge_len(a, b) -> float、
        node_ids、gate_nodes；F 的 Topology 会自动适配。None 时使用内置兜底图
        （需 flow_config 提供 nodes / edges）。
    flow_config : dict, optional
        车流配置：has_cav / tick_rate / global_density_level / horizon /
        n_vehicles / nodes / edges / random_state / on_tick。
    vehicles_plan : list of dict, optional
        车辆计划（vehicles.csv 行）：{id, birth_tick, src_node, dst_node, is_internal}；
        缺省按 flow_config 随机生成。
    gate_ids : list of str, optional
        门闸节点 ID；缺省从 topo.gate_nodes 推导。

    Attributes
    ----------
    vehicles_ : list of VehicleAgent
        全部车辆智能体。
    gates_ : dict of str -> GateAgent
        门闸智能体（run 前可预置以自定义初始状态）。
    trip_logs_ : list of dict
        全行程记录（车辆级原始数据，供 CavSimulator 汇总 per-node 指标）。

    Examples
    --------
    >>> engine = TickEngine(topo, {"has_cav": True, "tick_rate": 1.0}, plan)
    >>> logs = engine.run(horizon=600)
    """

    def __init__(
        self,
        topo: Any = None,
        flow_config: Optional[Dict[str, Any]] = None,
        vehicles_plan: Optional[List[Dict[str, Any]]] = None,
        gate_ids: Optional[List[str]] = None,
    ):
        self.topo = topo
        self.flow_config = dict(flow_config or {})
        self.vehicles_plan = list(vehicles_plan or [])
        self.gate_ids = list(gate_ids or [])
        self.vehicles_: List[VehicleAgent] = []
        self.gates_: Dict[str, GateAgent] = {}
        self.trip_logs_: List[Dict[str, Any]] = []
        self.ticks_ = 0

    # ------------------------------------------------------------------ 运行
    def run(self, horizon: Optional[int] = None) -> List[Dict[str, Any]]:
        """跑完整个仿真周期，返回 trip_logs_。"""
        cfg = self.flow_config
        horizon = int(horizon if horizon is not None else cfg.get("horizon", 3600))
        tick_rate = float(cfg.get("tick_rate", 1.0))
        has_cav = bool(cfg.get("has_cav", False))
        density_level = float(cfg.get("global_density_level", 0.5))
        rng = np.random.default_rng(int(cfg.get("random_state", 42)))
        on_tick: Optional[Callable[[int, "TickEngine"], None]] = cfg.get("on_tick")

        self._setup_topo()
        if not self.vehicles_plan:
            self.vehicles_plan = self._make_plan(rng)
        self._build_agents(density_level)
        for gid in self._gate_set:
            if gid not in self.gates_:
                self.gates_[gid] = GateAgent(gid)

        by_birth = sorted(self.vehicles_, key=lambda v: v.birth_tick)
        pointer = 0
        on_road: List[VehicleAgent] = []
        logger.info(
            "[agents.TickEngine.run] 仿真开始 horizon=%s tick_rate=%s has_cav=%s density_level=%s vehicles=%s",
            horizon, tick_rate, has_cav, density_level, len(self.vehicles_),
        )

        for t in range(horizon):
            self.ticks_ = t
            while pointer < len(by_birth) and by_birth[pointer].birth_tick <= t:
                v = by_birth[pointer]
                pointer += 1
                if len(v.target_route) <= 1:
                    v.arrived_ = True
                    v.finish_tick_ = v.birth_tick
                    v.crossed_nodes_.append({"node_id": v.target_route[0], "tick": t, "speed": 0.0})
                    continue
                on_road.append(v)

            edges: Dict[tuple, List[VehicleAgent]] = {}
            for v in on_road:
                if v.arrived_:
                    continue
                key = (v.target_route[v.route_index], v.target_route[v.route_index + 1])
                edges.setdefault(key, []).append(v)
            for lst in edges.values():
                lst.sort(key=lambda v: v.edge_pos)

            for lst in edges.values():
                for k, v in enumerate(lst):
                    a = v.target_route[v.route_index]
                    v.record_speed(a, v.speed, tick_rate)
                    gate = self.gates_.get(a)
                    if gate is not None and not gate.is_open and not v.is_emergency:
                        v.speed = 0.0
                        v.accumulate_delay(tick_rate)
                        continue
                    leader = lst[k + 1] if k + 1 < len(lst) else None
                    if has_cav:
                        v.update_cav(leader, tick_rate)
                    else:
                        v.update_idm(leader, tick_rate)
                    v.accumulate_delay(tick_rate)

            for lst in edges.values():
                for v in lst:
                    if v.arrived_:
                        continue
                    a, b = v.target_route[v.route_index], v.target_route[v.route_index + 1]
                    if not v.advance(self._edge_len(a, b), tick_rate):
                        continue
                    gate = self.gates_.get(b)
                    if gate is not None and not gate.try_pass(v):
                        v.speed = 0.0
                        continue
                    v.cross_node(t)

            on_road = [v for v in on_road if not v.arrived_]
            if on_tick is not None:
                on_tick(t, self)

        arrived = sum(1 for v in self.vehicles_ if v.arrived_)
        logger.info(
            "[agents.TickEngine.run] 仿真结束 vehicles=%s arrived=%s",
            len(self.vehicles_), arrived,
        )
        return self._finalize_trip_logs()

    # ------------------------------------------------------------------ 内部
    def _setup_topo(self):
        if self.topo is None:
            nodes = self.flow_config.get("nodes")
            edges = self.flow_config.get("edges")
            if not nodes or not edges:
                raise ValueError("topo=None 时 flow_config 必须提供 nodes 与 edges")
            self._topo_impl = _MiniTopo(list(nodes), list(edges))
        elif hasattr(self.topo, "node_name"):
            self._topo_impl = _FTopoAdapter(self.topo)
        else:
            self._topo_impl = self.topo
        self._gate_set = set(self.gate_ids)
        if not self._gate_set:
            self._gate_set = set(getattr(self._topo_impl, "gate_nodes", []))

    def _make_plan(self, rng: np.random.Generator) -> List[Dict[str, Any]]:
        n = int(self.flow_config.get("n_vehicles", 100))
        horizon = int(self.flow_config.get("horizon", 3600))
        nodes = self._topo_impl.node_ids
        srcs = [nid for nid in nodes if str(nid).startswith("gate")] or nodes
        plan = []
        for i in range(n):
            src = str(rng.choice(srcs))
            dst = str(rng.choice([nd for nd in nodes if nd != src]))
            plan.append({
                "id": f"v{i:04d}",
                "birth_tick": int(rng.integers(0, horizon)),
                "src_node": src,
                "dst_node": dst,
                "is_internal": int(rng.random() < 0.4),
            })
        return plan

    def _build_agents(self, density_level: float):
        idm = dict(DEFAULT_IDM_PARAMS)
        idm["v0"] *= 1.0 - 0.3 * density_level
        idm["s0"] *= 1.0 + density_level
        self.vehicles_ = []
        for row in self.vehicles_plan:
            route = self._route_of(str(row["src_node"]), str(row["dst_node"]))
            self.vehicles_.append(VehicleAgent(
                vehicle_id=str(row["id"]),
                birth_tick=int(row["birth_tick"]),
                target_route=route,
                is_internal=bool(row.get("is_internal", 0)),
                idm_params=idm,
            ))

    def _route_of(self, src: str, dst: str) -> List[str]:
        try:
            return list(self._topo_impl.path(src, dst))
        except Exception as exc:
            logger.warning("[agents.TickEngine._route_of] 路径规划失败 %s→%s: %s", src, dst, exc)
            return [src, dst]

    def _edge_len(self, a: str, b: str) -> float:
        length = self._topo_impl.edge_len(a, b)
        return float(length) if length and length > 0 else 1.0

    def _finalize_trip_logs(self) -> List[Dict[str, Any]]:
        logs = []
        for v in self.vehicles_:
            logs.append({
                "vehicle_id": v.vehicle_id,
                "birth_tick": v.birth_tick,
                "src_node": v.target_route[0],
                "dst_node": v.target_route[-1],
                "is_internal": v.is_internal,
                "arrived": v.arrived_,
                "finish_tick": v.finish_tick_,
                "travel_time": (v.finish_tick_ - v.birth_tick) if v.arrived_ else None,
                "delay_time": v.delay_time,
                "avg_speed": self._avg_speed(v),
                "crossings": v.crossed_nodes_,
                "node_delay": v.node_delay_,
                "node_speed_sum": v.node_speed_sum_,
                "node_time": v.node_time_,
            })
        self.trip_logs_ = logs
        return logs

    @staticmethod
    def _avg_speed(v: VehicleAgent) -> float:
        if not v.crossed_nodes_:
            return 0.0
        return float(np.mean([c["speed"] for c in v.crossed_nodes_]))


# ===========================================================================
# 本地兜底测试：同一拓扑/同一车辆计划，IDM 对照组 vs CAV 实验组
# ===========================================================================
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s][%(name)s] %(message)s")

    mock_nodes = ["gate_south", "node_15", "node_22", "zone_canteen"]
    mock_edges = [
        ("gate_south", "node_15", 100.0),
        ("node_15", "node_22", 120.0),
        ("node_22", "zone_canteen", 80.0),
    ]
    # 40 辆车间隔 1s 密集流入 + 南门先关 60s 制造排队，放大 CAV 对比效果
    mock_plan = [
        {"id": f"car_{i}", "birth_tick": i, "src_node": "gate_south",
         "dst_node": "zone_canteen", "is_internal": 0}
        for i in range(40)
    ]

    def close_gate_from(t, tick_open):
        """演示用：tick_open 之前南门关闭，车辆在门口排队（状态变化才打日志）。"""
        gate = engine_ref.gates_["gate_south"]
        if t < tick_open:
            if gate.is_open:
                gate.set_status(False)
        elif t == tick_open and not gate.is_open:
            gate.set_status(True)

    def run_case(has_cav, tick_open):
        global engine_ref
        cfg = {
            "has_cav": has_cav,
            "tick_rate": 1.0,
            "horizon": 900,
            "global_density_level": 0.8,
            "nodes": mock_nodes,
            "edges": mock_edges,
            "random_state": 42,
            "on_tick": lambda t, e: close_gate_from(t, tick_open),
        }
        engine_ref = TickEngine(topo=None, flow_config=cfg, vehicles_plan=mock_plan)
        logs = engine_ref.run()
        arrived = [log for log in logs if log["arrived"]]
        tt = np.mean([log["travel_time"] for log in arrived])
        dl = np.mean([log["delay_time"] for log in logs])
        return tt, dl, len(arrived)

    print("\n=== 对照组（IDM，无车联网） ===")
    idm_tt, idm_dl, idm_n = run_case(has_cav=False, tick_open=60)
    print(f"到达 {idm_n}/{len(mock_plan)} 辆 | 平均通行时间 {idm_tt:.1f}s | 平均滞留 {idm_dl:.1f}s")

    print("\n=== 实验组（CAV，CTH 编队） ===")
    cav_tt, cav_dl, cav_n = run_case(has_cav=True, tick_open=60)
    print(f"到达 {cav_n}/{len(mock_plan)} 辆 | 平均通行时间 {cav_tt:.1f}s | 平均滞留 {cav_dl:.1f}s")

    gain = (idm_tt - cav_tt) / idm_tt * 100 if idm_tt > 0 else 0.0
    print(f"\nCAV 平均通行时间缩短 {gain:.1f}%")
