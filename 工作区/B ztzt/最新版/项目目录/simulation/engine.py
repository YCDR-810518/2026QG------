# -*- coding: utf-8 -*-
"""engine.py —— 核心仿真引擎 TickEngine（成员 F，FR-16）

向量化 tick 主循环 + 预分配实体池，支撑 8000+ 人/车同屏：
- 四态状态机（src 等待 / 沿边移动 / dst 停留 / 信号排队）；
- 大门闸（gateId）只控制车辆入园吞吐（FR-19）+ 园内门闸（doorId）只控制人流
  （影响行人 Dijkstra 边权，不限制车辆）+ 红绿灯完整信号周期（SignalPolicyController）；
- 每 tick 输出冻结密度快照（对齐 8.2 接口：tick/timestamp/node_id/people/vehicles/density/level）。

依赖：numpy；依赖方向：生成器 → 引擎 → 调控器（单向，不反向依赖）。
用法：
    gen = FlowDataGenerator(n_people=2000, n_vehicles=150, random_state=42)
    gen.generate()
    eng = TickEngine(topo, gen, gate_policy=HysteresisPolicyController(role="gate"),
                     door_policy=HysteresisPolicyController(role="door"),
                     joint_regulator=JointRegulator())
    snap = eng.step(0)
    result = eng.run(3600)
"""
import csv
import datetime
import time
from dataclasses import dataclass, field

import numpy as np

import config  # noqa: F401  （副作用：注入 F 工作区目录到 sys.path）

from entities import (
    EntityPool,
    STATE_DWELL_DST,
    STATE_TRAVEL,
    STATE_WAIT_SIGNAL,
    STATE_WAIT_SRC,
)
from flow_data_generator import DWELL_MAX, DWELL_MIN, WAIT_MAX, WAIT_MIN
from metrics import EngineMetrics, PerfTimer, level_of
from movement import BaseMovement, ConstantSpeedMovement
from controller import SignalPolicyController

_SEC_PER_HOUR = 3600
_SEC_PER_MIN = 60

SNAPSHOT_CSV_FIELDS = [
    "tick", "timestamp", "node_id", "people", "vehicles",
    "density", "level",
    "gate_status", "gate_flow_rate",
    "door_status", "door_flow_rate",
    "signal_status", "signal_flow_rate",
]

_DELAY_SPEED_THRESHOLD = 1.39   # 5 km/h 以下视为滞留（与 cav_pack/agents 口径一致）


@dataclass
class SimulationResult:
    """run() 的汇总结果。

    Attributes
    ----------
    n_ticks : int
        已运行 tick 数。
    report : dict
        性能指标汇总（metrics.report()）。
    density_series : list of np.ndarray
        每 tick 全节点密度快照。
    final_density : np.ndarray
        末 tick 全节点密度 (n_nodes, 2)。
    last_snapshot : dict
        末 tick 冻结快照。
    """

    n_ticks: int = 0
    report: dict = field(default_factory=dict)
    density_series: list = field(default_factory=list)
    final_density: np.ndarray = None
    last_snapshot: dict = field(default_factory=dict)


class TickEngine:
    """核心仿真引擎（FR-16）。

    Parameters
    ----------
    topology : Topology
        园区拓扑实例。
    flow_generator : FlowDataGenerator
        逐 tick 流入源（须提供 sample(tick)）。
    gate_policy : GatePolicyController, optional
        大门闸控制（FR-19，仅限流入园车辆）；None 表示不设大门限流。
    door_policy : DoorPolicyController, optional
        园内门闸控制（仅控制人流，不影响车辆）；None 表示全部门保持 open。
    joint_regulator : JointRegulator, optional
        宏观-微观联合调控（FR-18）。
    signal_controller : SignalPolicyController, optional
        红绿灯控制器；enable_signals 时缺省自动构建。
    movement : BaseMovement, optional
        微观移动模型（默认 ConstantSpeedMovement，供 C 扩展）。
    dt : float
        每 tick 模拟秒数。
    max_capacity : int
        实体池预分配上限。
    seed : int
        随机种子（等待/停留时长等）。
    enable_signals : bool
        是否启用红绿灯。
    vehicle_compliance / ped_compliance : float
        车/人遵守信号比例。
    check_interval : int
        联合调控触发间隔。
    start_hour : int
        模拟起始时刻的小时（用于 timestamp）。
    start_minute : int
        模拟起始时刻的分钟（用于 timestamp）。
    start_date : str
        模拟起始日期 YYYY-MM-DD（用于 timestamp）。

    Attributes
    ----------
    pool : EntityPool
        预分配实体池。
    topology / flow_generator / gate_policy / joint_regulator / signal_controller
        注入的组件（对外只读状态）。
    """

    def __init__(self, topology, flow_generator, gate_policy=None, door_policy=None,
                 joint_regulator=None, signal_controller=None, movement=None, dt=1.0,
                 max_capacity=10000, seed=42, enable_signals=True, vehicle_compliance=1.0,
                 ped_compliance=0.3, check_interval=10, start_hour=6, start_minute=0,
                 start_date="2026-08-03"):
        self.topology = topology
        self.flow_generator = flow_generator
        self.gate_policy = gate_policy
        self.door_policy = door_policy
        self.joint_regulator = joint_regulator
        self.dt = float(dt)
        self.max_capacity = int(max_capacity)
        self.seed = int(seed)
        self.enable_signals = bool(enable_signals)
        self.vehicle_compliance = float(vehicle_compliance)
        self.ped_compliance = float(ped_compliance)
        self.check_interval = int(check_interval)
        self.start_hour = int(start_hour)
        self.start_minute = int(start_minute)
        self.start_date = datetime.date.fromisoformat(str(start_date))
        self.day_sec = int(getattr(flow_generator, "_day_sec", 86400))

        self.rng = np.random.default_rng(self.seed)
        self.movement = movement if movement is not None else ConstantSpeedMovement(topology)
        if not isinstance(self.movement, BaseMovement):
            raise TypeError("movement 必须是 BaseMovement 实例")

        if self.enable_signals:
            self.signal_controller = signal_controller or SignalPolicyController().fit(topology)
        else:
            self.signal_controller = None

        # 节点级时长表（按节点序号索引，单位 tick = 秒）
        self.wait_min = np.array([WAIT_MIN[t] for t in topology.node_types], dtype=np.float64) * _SEC_PER_MIN
        self.wait_max = np.array([WAIT_MAX[t] for t in topology.node_types], dtype=np.float64) * _SEC_PER_MIN
        self.dwell_min = np.array([DWELL_MIN[t] for t in topology.node_types], dtype=np.float64) * _SEC_PER_MIN
        self.dwell_max = np.array([DWELL_MAX[t] for t in topology.node_types], dtype=np.float64) * _SEC_PER_MIN

        self._check_node_alignment()
        self.metrics = EngineMetrics()
        self.last_timings = {}
        self._timer = PerfTimer()
        self.reset()

    # ------------------------------------------------------------------ 初始化
    def _check_node_alignment(self):
        gen_ids = getattr(self.flow_generator, "node_ids", None)
        if gen_ids is not None and list(gen_ids) != list(self.topology.node_ids):
            raise ValueError(
                "生成器 node_ids 与拓扑 node_ids 不一致，请先统一编码（8.2 钉死项）"
            )

    def reset(self):
        """复位到初始状态。"""
        self.pool = EntityPool(self.max_capacity)
        self._pending = {}           # 大门节点 → 等待放行的 (kind, src, dst) 队列（仅车辆）
        self._gate_caps = {g: 10 ** 9 for g in self.topology.gate_nodes}
        self._gate_policies = {}
        self._door_policies = {}
        self._joint_plan = None
        self._heat = None
        self._tick = -1
        self._people_now = np.zeros(self.topology.n_nodes, dtype=np.int64)
        self._vehicles_now = np.zeros(self.topology.n_nodes, dtype=np.int64)
        # ---- trip 钩子：per-vehicle 行程记录（C/协作8 需要，纯新增） ----
        self._birth = np.zeros(self.max_capacity, dtype=np.int32)      # 槽位 → 出生(过闸入园) tick
        self._spd_sum = np.zeros(self.max_capacity, dtype=np.float64)  # 槽位 → 累计行驶里程 Σ(v·dt) m
        self._delay_sum = np.zeros(self.max_capacity, dtype=np.float64)  # 槽位 → 累计滞留 s
        self.trip_logs_ = []                                            # 行程日志（到达时追加）
        self.metrics = EngineMetrics()
        self.last_timings = {}
        return self

    # ------------------------------------------------------------------ 路径/速度
    def _path_for(self, src_idx, dst_idx, kind=0):
        return self.topology.path(int(src_idx), int(dst_idx), kind=int(kind))

    def vehicle_paths_json(self):
        """实时生成车辆入园（kind=1）最短路径表，供 union_pack 打包发送后端。

        对每个大门闸（gate_south/west/east）→ 所有非大门节点做一次 Dijkstra
        （kind=1，门闸强制 open、只受红绿灯影响），返回 JSON 可序列化列表：
            {src, dst, path:[node_id,...], travelTime}（travelTime 为路径各边
            weight 之和，单位分钟，对齐冻结接口《1-交通网络接口.md》）。
        路径经 topology.path 惰性缓存，红绿灯状态变化（sync_control_states）
        后自动重算绕行；不可达节点对跳过。
        """
        topo = self.topology
        gates = list(topo.gate_nodes)
        gate_set = set(gates)
        dsts = [i for i in range(topo.n_nodes) if i not in gate_set]

        rows = []
        for g in gates:
            src_id = topo.node_ids[g]
            for d in dsts:
                try:
                    path_idx = topo.path(g, d, kind=1)
                except ValueError:
                    continue
                travel = 0.0
                for a, b in zip(path_idx[:-1], path_idx[1:]):
                    w = topo.network.get_edge_weight(topo.node_ids[a], topo.node_ids[b])
                    travel += float(w if w else 0.0)
                rows.append({
                    "src": src_id,
                    "dst": topo.node_ids[d],
                    "path": [topo.node_ids[i] for i in path_idx],
                    "travelTime": round(travel, 2),
                })
        return rows

    def _obey_signal(self, kind):
        comp = self.vehicle_compliance if kind == 1 else self.ped_compliance
        return self.rng.random() < comp

    # ------------------------------------------------------------------ 流入
    def _spawn_batch(self, kinds, srcs, dsts):
        """批量生成实体：预分配槽位 + 路径 + 等待/停留时长。"""
        if not kinds:
            return 0
        kinds = np.asarray(kinds, dtype=np.int8)
        srcs = np.asarray(srcs, dtype=np.int32)
        dsts = np.asarray(dsts, dtype=np.int32)
        slots = self.pool.allocate(len(kinds))
        n = len(slots)
        if n < len(kinds):
            kinds, srcs, dsts = kinds[:n], srcs[:n], dsts[:n]

        # ---- trip 钩子：出生(过闸入园)时刻 = 分配槽位的本 tick（纯新增） ----
        self._birth[slots] = self._tick
        self._spd_sum[slots] = 0.0
        self._delay_sum[slots] = 0.0

        d = self.pool.data
        d["kind"][slots] = kinds
        d["src_node"][slots] = srcs
        d["dst_node"][slots] = dsts
        d["state"][slots] = STATE_WAIT_SRC
        d["cur_node"][slots] = srcs
        d["edge_pos"][slots] = 0.0
        d["speed"][slots] = 0.0
        d["path_pos"][slots] = 0

        same = srcs == dsts
        if (~same).any():
            idx = slots[~same]
            d["wait_ticks"][idx] = np.rint(
                self.rng.uniform(self.wait_min[srcs[~same]], self.wait_max[srcs[~same]])
            ).astype(np.int32)
        if same.any():
            idx = slots[same]
            d["state"][idx] = STATE_DWELL_DST
            d["wait_ticks"][idx] = np.rint(
                self.rng.uniform(self.dwell_min[dsts[same]], self.dwell_max[dsts[same]])
            ).astype(np.int32)

        for k, slot in enumerate(slots):
            self.pool.set_path(slot, self._path_for(srcs[k], dsts[k], kinds[k]))
        return n

    def _ingest(self, tick):
        """本 tick 流入：普通实体直接生成；大门闸实体（仅车辆）按放行上限排队。

        大门（gateId）只控制车入园，不控制人入园：行人无论 src 是否为大门口
        都直接生成，只有 src 在大门的车辆进入 _pending 排队限流。
        """
        arr = self.flow_generator.sample(tick)
        to_kind, to_src, to_dst = [], [], []

        for row in arr:
            kind, src, dst = int(row["kind"]), int(row["src_node"]), int(row["dst_node"])
            if (self.gate_policy is not None and kind == 1
                    and src in self._gate_caps):
                self._pending.setdefault(src, []).append((kind, src, dst))
            else:
                to_kind.append(kind)
                to_src.append(src)
                to_dst.append(dst)

        n_in = self._spawn_batch(to_kind, to_src, to_dst)

        for g in self._gate_caps:
            cap = int(self._gate_caps[g])
            q = self._pending.get(g, [])
            admit, rest = q[:cap], q[cap:]
            self._pending[g] = rest
            if admit:
                n_in += self._spawn_batch(*zip(*admit))
        return n_in

    # ------------------------------------------------------------------ 状态机
    def _state_machine(self, sig_info):
        data = self.pool.data
        active = data["active"]
        if not active.any():
            return
        topo = self.topology
        st = data["state"]

        # ---- state 0：src 等待，到期 → 上第一条边
        m0 = active & (st == STATE_WAIT_SRC)
        if m0.any():
            data["wait_ticks"][m0] -= 1
            done = m0 & (data["wait_ticks"] <= 0)
            if done.any():
                # 路径起点即 src_node，直接向量化取 origin
                data["state"][done] = STATE_TRAVEL
                data["cur_node"][done] = data["src_node"][done]
                data["edge_target"][done] = self._first_edge_target(done)
                data["edge_pos"][done] = 0.0
                data["wait_ticks"][done] = 0

        # ---- state 1：沿边移动
        m1 = active & (st == STATE_TRAVEL)
        if m1.any():
            origin = data["cur_node"][m1]
            target = data["edge_target"][m1]
            lengths = topo.edge_length[origin, target]
            lengths = np.where(lengths <= 0, 1.0, lengths)
            data["edge_pos"][m1] += data["speed"][m1] * self.dt
            moved = data["edge_pos"][m1] >= lengths
            reached = np.zeros(data.shape[0], dtype=np.bool_)
            reached[m1] = moved
            self._on_reach(reached, sig_info)

        # ---- state 2：dst 停留，到期 → 回收
        m2 = active & (st == STATE_DWELL_DST)
        if m2.any():
            data["wait_ticks"][m2] -= 1
            leave = m2 & (data["wait_ticks"] <= 0)
            if leave.any():
                self.pool.recycle(leave)

    def _first_edge_target(self, mask):
        idx = np.nonzero(mask)[0]
        out = np.empty(len(idx), dtype=np.int32)
        for k, s in enumerate(idx):
            out[k] = self.pool.path(s)[1]
        return out

    def _on_reach(self, reached, sig_info):
        data = self.pool.data
        topo = self.topology
        idx = np.nonzero(reached)[0]
        if idx.size == 0:
            return
        comply = np.asarray(
            [self._obey_signal(int(data["kind"][s])) for s in idx], dtype=np.bool_
        )
        for k, s in enumerate(idx):
            path = self.pool.path(s)
            nxt = int(data["path_pos"][s]) + 1
            data["path_pos"][s] = nxt
            if nxt == path.size - 1:
                # 到达终点 → 停留
                data["state"][s] = STATE_DWELL_DST
                data["cur_node"][s] = int(data["dst_node"][s])
                lo = self.dwell_min[int(data["dst_node"][s])]
                hi = self.dwell_max[int(data["dst_node"][s])]
                data["wait_ticks"][s] = int(round(self.rng.uniform(lo, hi)))
                data["edge_pos"][s] = 0.0
                # ---- trip 钩子：到达终点，落行程日志（纯新增，只记车辆） ----
                if int(data["kind"][s]) == 1:
                    self.trip_logs_.append({
                        "src_node": self.topology.node_ids[int(data["src_node"][s])],
                        "dst_node": self.topology.node_ids[int(data["dst_node"][s])],
                        "birth_tick": int(self._birth[s]),
                        "finish_tick": int(self._tick),
                        "travel_time": int(self._tick) - int(self._birth[s]),
                        "avg_speed_kmh": round(self._spd_sum[s] / max(int(self._tick) - int(self._birth[s]), 1) * 3.6, 2),
                        "delay_time": round(self._delay_sum[s], 1),
                    })
                continue
            arr_node = int(path[nxt])
            blocked = (
                sig_info is not None
                and arr_node in sig_info
                and not sig_info[arr_node]["passable"]
                and bool(comply[k])
            )
            if blocked:
                # 信号红灯 → 排队（edge_pos 钳在刚走完的边长处）
                length_val = float(topo.edge_length[data["cur_node"][s], data["edge_target"][s]])
                data["state"][s] = STATE_WAIT_SIGNAL
                data["cur_node"][s] = arr_node
                data["edge_pos"][s] = length_val
                data["wait_ticks"][s] = 0
            else:
                # 继续下一条边
                data["state"][s] = STATE_TRAVEL
                data["cur_node"][s] = arr_node
                data["edge_target"][s] = int(path[nxt + 1])
                data["edge_pos"][s] = 0.0

    # ------------------------------------------------------------------ 信号放行
    def _signal_release(self, sig_info):
        if self.signal_controller is None or sig_info is None:
            return
        data = self.pool.data
        for node_idx, info in sig_info.items():
            if not info["passable"]:
                continue
            waiting = data["active"] & (data["state"] == STATE_WAIT_SIGNAL) & (data["cur_node"] == node_idx)
            slot_ids = np.nonzero(waiting)[0]
            cap = int(info["throughput_cap"])
            for s in slot_ids[:cap]:
                path = self.pool.path(s)
                pos = int(data["path_pos"][s])
                data["state"][s] = STATE_TRAVEL
                data["cur_node"][s] = int(path[pos])
                data["edge_target"][s] = int(path[pos + 1])
                data["edge_pos"][s] = 0.0

    # ------------------------------------------------------------------ 密度
    def _aggregate(self):
        data = self.pool.data
        active = data["active"]
        n_nodes = self.topology.n_nodes
        if not active.any():
            self._people_now = np.zeros(n_nodes, dtype=np.int64)
            self._vehicles_now = np.zeros(n_nodes, dtype=np.int64)
            return
        at_node = active & (data["state"] != STATE_TRAVEL)
        cur = data["cur_node"][at_node]
        kind = data["kind"][at_node]
        self._people_now = np.bincount(cur[kind == 0], minlength=n_nodes).astype(np.int64)
        self._vehicles_now = np.bincount(cur[kind == 1], minlength=n_nodes).astype(np.int64)

    @property
    def density(self):
        """当前全节点密度 (n_nodes, 2)（人/车）。"""
        return np.stack([self._people_now, self._vehicles_now], axis=1)

    @property
    def people_density(self):
        """当前全节点综合密度 = 人数/容量。"""
        return self._people_now / self.topology.capacities

    @property
    def vehicle_density(self):
        """当前全节点车辆密度 = 车数/容量。"""
        return self._vehicles_now / self.topology.capacities

    # ------------------------------------------------------------------ 调控
    def _regulate(self, tick):
        if self.gate_policy is not None:
            for g in self.topology.gate_nodes:
                d = self.vehicle_density[g]
                pol = self.gate_policy.predict(d, gate_id=self.topology.node_ids[g])
                self._gate_caps[g] = pol["throughput_cap"]
                self._gate_policies[g] = pol
        if self.door_policy is not None:
            for i in range(self.topology.n_nodes):
                d = self.people_density[i]
                pol = self.door_policy.predict(d, node_id=self.topology.node_ids[i])
                self._door_policies[self.topology.node_ids[i]] = pol["mode"]
        if self.joint_regulator is not None and tick % self.check_interval == 0:
            state = self.state
            self._joint_plan = self.joint_regulator.predict(state)

    @property
    def state(self):
        """当前引擎状态（供调控器/外部读取）。"""
        return {
            "tick": self._tick,
            "density": self.people_density.copy(),
            "people": self._people_now.copy(),
            "vehicles": self._vehicles_now.copy(),
            "node_ids": self.topology.node_ids,
            "heat": self._heat,
        }

    def set_heat(self, heat):
        """注入 A 的宏观热度指标（n_nodes,），供 JointRegulator 使用。"""
        heat = np.asarray(heat, dtype=np.float64)
        if heat.shape != (self.topology.n_nodes,):
            raise ValueError(f"heat 长度应为 {self.topology.n_nodes}")
        self._heat = heat

    # ------------------------------------------------------------------ 主循环
    def step(self, tick):
        """推进一个 tick，返回本 tick 冻结密度快照。

        Parameters
        ----------
        tick : int
            当前模拟 tick（秒）。

        Returns
        -------
        dict
            冻结快照：{tick, timestamp, density, nodes:[{node_id, people, vehicles,
            density, level}], signals, gates, joint_plan}。
        """
        self._tick = int(tick)
        timer = self._timer
        sig_info = None
        if self.signal_controller is not None:
            raw = self.signal_controller.predict(self._tick)
            sig_info = {k: {**v, "passable": self.signal_controller.passable(k, self._tick)}
                        for k, v in raw.items()}

        t0 = time.perf_counter()
        timer.start()
        n_in = self._ingest(self._tick)
        timer.stop("spawn")

        timer.start()
        self.movement.update_speed(self.pool)
        timer.stop("movement")

        # ---- trip 钩子：车辆速度/滞留累计（向量化，开销 ≈ 2 次数组运算/tick） ----
        _d = self.pool.data
        _veh = _d["active"] & (_d["kind"] == 1)
        _mov = _veh & (_d["state"] == STATE_TRAVEL)
        self._spd_sum[_mov] += _d["speed"][_mov] * self.dt
        _que = _veh & (_d["state"] == STATE_WAIT_SIGNAL)
        self._delay_sum[_que] += self.dt
        _slow = _mov & (_d["speed"] < _DELAY_SPEED_THRESHOLD)
        self._delay_sum[_slow] += self.dt

        timer.start()
        self._state_machine(sig_info)
        timer.stop("state")

        timer.start()
        self._signal_release(sig_info)
        timer.stop("signal")

        timer.start()
        self._aggregate()
        timer.stop("aggregate")

        timer.start()
        self._regulate(self._tick)
        timer.stop("regulate")

        # 把本 tick 大门/园内门闸/红绿灯控制状态写回共享 TrafficNetwork 缓存，
        # 保证一致性并让新生成实体的最短路径随状态动态绕行。
        # 大门/园内门闸词表已统一为 open/restricted/closed（DjShortCut 整合后），直传即可。
        gate_states = {self.topology.node_ids[g]: pol["mode"]
                       for g, pol in self._gate_policies.items()}
        door_states = dict(self._door_policies)
        signal_states = ({info["node_id"]: {"phase": info["phase"]}
                          for info in sig_info.values()}
                         if sig_info else {})
        self.topology.sync_control_states(gate_states, door_states, signal_states)

        elapsed = time.perf_counter() - t0
        mods = timer.snapshot()
        timer.clear()

        self.last_timings = dict(mods)
        self.metrics.record(elapsed, mods, n_in=n_in,
                            n_active=self.pool.n_active,
                            density=self.people_density.copy(),
                            people=self._people_now.copy(),
                            vehicles=self._vehicles_now.copy(),
                            gate_states={self.topology.node_ids[g]: {"mode": pol["mode"], "throughput_cap": pol["throughput_cap"]}
                                         for g, pol in self._gate_policies.items()},
                            door_states=self._door_policies,
                            signal_states=({info["node_id"]: {"phase": info["phase"], "signal_flow_rate": info["signal_flow_rate"]}
                                            for info in sig_info.values()}
                                           if sig_info else {}))

        snap = self._snapshot()
        return snap

    def run(self, n_ticks):
        """连续运行 n 个 tick（自 tick=0 起）。

        Returns
        -------
        SimulationResult
            汇总结果（含性能报告与密度序列）。
        """
        for t in range(int(n_ticks)):
            self.step(t)
        return SimulationResult(
            n_ticks=int(n_ticks),
            report=self.metrics.report(),
            density_series=list(self.metrics.density_series),
            final_density=self.density.copy(),
            last_snapshot=self._snapshot(),
        )

    # ------------------------------------------------------------------ 快照/导出
    def _snapshot(self):
        people = self._people_now
        vehicles = self._vehicles_now
        density = self.people_density
        level = level_of(density)
        nodes = [
            {
                "node_id": self.topology.node_ids[i],
                "people": int(people[i]),
                "vehicles": int(vehicles[i]),
                "density": float(density[i]),
                "level": str(level[i]),
            }
            for i in range(self.topology.n_nodes)
        ]
        return {
            "tick": self._tick,
            "timestamp": self._timestamp(self._tick),
            "density": np.stack([people, vehicles], axis=1).copy(),
            "nodes": nodes,
            "signals": self.signal_controller.predict(self._tick) if self.signal_controller else {},
            "gates": dict(self._gate_policies),
            "doors": dict(self._door_policies),
            "joint_plan": self._joint_plan,
        }

    def _timestamp(self, tick):
        day = int(tick) // self.day_sec
        rem = int(tick) % self.day_sec
        total_min = self.start_minute + rem // 60
        h = (self.start_hour + total_min // 60) % 24
        m = total_min % 60
        s = rem % 60
        day += (self.start_hour + total_min // 60) // 24
        date = self.start_date + datetime.timedelta(days=day)
        return f"{date} {h:02d}:{m:02d}:{s:02d}"

    def _snapshot_rows(self, t):
        """生成某 tick 的 CSV 行（每节点一行，共 n_nodes 行，tick/timestamp 相同）。

        字段：tick, timestamp, node_id, people, vehicles, density, level,
              gate_status, gate_flow_rate, door_status, door_flow_rate,
              signal_status, signal_flow_rate
        """
        topo = self.topology
        n_ticks = len(self.metrics.density_series)
        has_gates = len(self.metrics.gate_series) == n_ticks
        has_doors = len(self.metrics.door_series) == n_ticks
        has_signals = len(self.metrics.signal_series) == n_ticks
        density = self.metrics.density_series[t]
        people = self.metrics.people_series[t]
        vehicles = self.metrics.vehicles_series[t]
        level = level_of(density)
        gates = self.metrics.gate_series[t] if has_gates else {}
        doors = self.metrics.door_series[t] if has_doors else {}
        sigs = self.metrics.signal_series[t] if has_signals else {}
        ts = self._timestamp(t)
        rows = []
        for i in range(topo.n_nodes):
            nid = topo.node_ids[i]
            is_gate_node = bool(topo.is_gate[i] and topo.is_entrance[i])
            is_signal_node = bool(topo.is_signal[i])
            if is_gate_node:
                g = gates.get(nid, {})
                gate_status = g.get("mode", "")
                gate_flow = str(g.get("throughput_cap", "")) if g else ""
            else:
                gate_status, gate_flow = "", ""
            door_status = doors.get(nid, "")
            door_flow = ""
            if is_signal_node:
                s = sigs.get(nid, {})
                signal_status = s.get("phase", "")
                signal_flow = str(s.get("signal_flow_rate", "")) if s else ""
            else:
                signal_status, signal_flow = "", ""
            rows.append([t, ts, nid,
                         int(people[i]), int(vehicles[i]),
                         f"{density[i]:.4f}", str(level[i]),
                         gate_status, gate_flow,
                         door_status, door_flow,
                         signal_status, signal_flow])
        return rows

    def export_snapshot_csv(self, path):
        """把已运行的密度序列落盘 CSV（对接 E 大屏 / D 校验）。

        字段见 SNAPSHOT_CSV_FIELDS；按每 10 tick 降采样（每节点一行）。
        """
        from pathlib import Path

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        n_ticks = len(self.metrics.density_series)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SNAPSHOT_CSV_FIELDS)
            for t in range(n_ticks):
                if t % 10 != 0:
                    continue
                for row in self._snapshot_rows(t):
                    w.writerow(row)
        return out


if __name__ == "__main__":
    from flow_data_generator import FlowDataGenerator
    from controller import HysteresisPolicyController
    from joint_regulator import JointRegulator
    from topology import Topology

    topo = Topology()
    gen = FlowDataGenerator(n_people=1000, n_vehicles=80, random_state=42, n_days=1)
    gen.generate()
    eng = TickEngine(topo, gen, gate_policy=HysteresisPolicyController(role="gate"),
                     door_policy=HysteresisPolicyController(role="door"),
                     joint_regulator=JointRegulator())
    snap = eng.step(0)
    res = eng.run(200)
    print("tick mean ms:", res.report["tick_mean_ms"])
    print("module mean ms:", res.report["module_mean_ms"])
    print("avg active:", res.report["avg_active"])
    print("sample snapshot node:", snap["nodes"][0])
    print("door sample:", dict(list(snap.get("doors", {}).items())[:5]))
    # __main__ demo 结束
