# -*- coding: utf-8 -*-
"""engine.py —— MergeTickEngine:60° 夹角双路→单车道汇合推演引擎

每 tick:
    注入车辆 → 协调器分配时隙 → 分车道查前车(leader) → 判定主路优先让行
    → 全体智能体决策 → 位置推进 → 汇合点穿越记录 → 队列统计 → 快照
"""
import numpy as np

from .config import CavParams, MergeParams
from .entities import MergeCoordinator, MergeVehicleAgent

_YIELD_MOUTH = 6.0     # 汇合口让行探测距离 m(对照组 B 车道在此范围感知主路车流)
_MERGE_GAP = 10.0      # 可插入空档阈值 m(前方空档大于该值视为可汇入)
_OVERLAP_TOL = 0.5     # 重叠容差 m(位置差小于该值视为已重叠,防止互指死锁)


class MergeTickEngine:
    """60° 夹角双路→单车道汇合推演引擎。

    Parameters
    ----------
    topo : MergeTopology
        汇合拓扑。
    plan : list[dict]
        车辆计划,每行含 id / birth_tick / lane("A"或"B")/ 可选 idm_params。
    has_cav : bool
        True=CAV 实验组(协调器时隙),False=IDM 对照组(主路优先)。
    strategy : str
        协调策略:"gap" / "zipper" / "consensus"。
    cav_params : CavParams, optional
        CAV 参数。
    merge_params : MergeParams, optional
        协调器参数。
    dt : float
        每 tick 模拟秒数。
    horizon : int
        仿真时长 tick。
    """

    def __init__(self, topo, plan, has_cav, strategy="gap",
                 cav_params=None, merge_params=None, dt=1.0, horizon=900):
        self.topo = topo
        self.plan = plan
        self.has_cav = has_cav
        self.strategy = strategy
        self.cav_params = cav_params or CavParams()
        self.merge_params = merge_params or MergeParams()
        self.dt = dt
        self.horizon = horizon
        self.vehicles = [
            MergeVehicleAgent(row["id"], row["birth_tick"], topo,
                              row.get("lane", "A"), self.cav_params,
                              row.get("idm_params"))
            for row in plan
        ]
        self.frames = []          # 每 tick: [(vid, lane, s, v)]
        self.crossings = []       # 汇合点穿越记录: {vid, lane, slot_time, cross_tick}
        self.queue_series = []    # 每 tick 汇合区排队数(低速车辆数)
        self.logs = []

    # ------------------------------------------------------------------
    def run(self) -> list:
        """推进整个仿真,返回全行程记录 logs。"""
        coordinator = MergeCoordinator(self.strategy, self.merge_params)
        by_birth = sorted(self.vehicles, key=lambda v: v.birth_tick)
        pointer = 0
        on_road = []

        for t in range(self.horizon):
            while pointer < len(by_birth) and by_birth[pointer].birth_tick <= t:
                on_road.append(by_birth[pointer])
                pointer += 1
            if not on_road:
                break

            if self.has_cav:
                coordinator.update(on_road, t)

            # ---- 分车道查前车 + 主路优先让行判定 ----
            for v in on_road:
                v.leader = self._find_leader(v, on_road)
                v.blocked = self._yield_check(v, on_road) if not self.has_cav else False

            for v in on_road:
                v.step(v.leader, t, self.dt, self.has_cav, v.blocked)

            # ---- 位置推进 / 到达 / 汇合穿越 ----
            for v in on_road:
                v.s += v.v * self.dt
                if v.v < self.cav_params.stop_threshold:
                    v.delay += self.dt
                if not v.arrived and v.merge_state != "MERGED" and v.s >= self.topo.s_merge:
                    v.merge_state = "MERGED"
                    v.cross_tick = t + 1
                    self.crossings.append({
                        "vid": v.vid, "lane": v.lane,
                        "slot_time": v.slot_time, "cross_tick": v.cross_tick,
                    })
                if v.s >= self.topo.total_length:
                    v.s = self.topo.total_length
                    v.arrived = True
                    v.finish_tick = t + 1
            on_road = [v for v in on_road if not v.arrived]

            # ---- 汇合区排队统计(低速车辆数) ----
            queue = sum(
                1 for v in on_road
                if self.topo.zone_lo <= v.s < self.topo.s_merge
                and v.v < self.merge_params.stop_speed
            )
            self.queue_series.append(queue)

            self.frames.append([(v.vid, v.lane, v.s, v.v) for v in self.vehicles
                                if not v.arrived and v.birth_tick <= t])

        self.logs = [{
            "vid": v.vid,
            "lane": v.lane,
            "birth": v.birth_tick,
            "arrived": v.arrived,
            "finish": v.finish_tick,
            "cross_tick": v.cross_tick,
            "slot_time": v.slot_time,
            "slot_dev": (v.cross_tick - v.slot_time) if (self.has_cav and v.cross_tick is not None
                                                         and v.slot_time is not None) else None,
            "travel_time": (v.finish_tick - v.birth_tick) if v.arrived else None,
            "delay": v.delay,
            "avg_speed_kmh": (self.topo.total_length / (v.finish_tick - v.birth_tick) * 3.6)
                             if v.arrived else 0.0,
        } for v in self.vehicles]
        return self.logs

    # ------------------------------------------------------------------
    def _find_leader(self, v, on_road):
        """前车查找:同车道内更靠前者优先;跨过汇合点的车并入单车道队列。

        已汇入单车道(s >= s_merge)的车按统一里程排队;
        车道内车(s < s_merge)的前车 = 同车道更靠前 || 最近的已汇入车。

        重叠容差 _OVERLAP_TOL:多车同时挤过汇合点时位置可能完全重叠,
        若重叠车互指为前车会形成 gap=0 的永久死锁;间距小于容差视为
        已物理重叠,后车不再把前车当 leader(自行加速脱离)。
        """
        best = None
        if v.s >= self.topo.s_merge:
            for v2 in on_road:
                if v2 is v or v2.s < v.s + _OVERLAP_TOL:
                    continue
                if best is None or v2.s < best.s:
                    best = v2
            return best
        for v2 in on_road:
            if v2 is v:
                continue
            if v2.s >= self.topo.s_merge:
                if v2.s >= v.s + _OVERLAP_TOL and (best is None or v2.s < best.s):
                    best = v2
                continue
            if v2.lane == v.lane and v2.s > v.s + _OVERLAP_TOL:
                if best is None or v2.s < best.s:
                    best = v2
        return best

    # ------------------------------------------------------------------
    def _yield_check(self, v, on_road):
        """对照组主路优先让行:仅对**尚未汇合**的 B 车道车生效;
        前方(主车道或已汇入单车道)车辆占住汇合口前方 _MERGE_GAP 米内
        空档时停车等待。"""
        if v.lane != "B":
            return False
        if not (self.topo.s_merge - _YIELD_MOUTH <= v.s < self.topo.s_merge):
            return False
        for v2 in on_road:
            if v2 is v or v2.arrived:
                continue
            if v2.s <= v.s:                       # 只挡更靠前的车
                continue
            if v2.s > v.s + _MERGE_GAP:           # 空档足够,不挡
                continue
            if v2.lane == v.lane and v2.s < self.topo.s_merge:
                continue                          # 同车道排队车由 IDM 跟驰处理
            if v2.s >= self.topo.s_merge - _YIELD_MOUTH:
                return True                       # 主车道/已汇入车占住汇合口
        return False
