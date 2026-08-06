# -*- coding: utf-8 -*-
"""engine.py —— tick 推演引擎（拆分自 CAV+MAS.py 的 TickEngine）

按出生时刻注入车辆，每 tick 全体智能体决策推进，产出逐 tick 车辆
快照（frames）与全行程记录（logs）。
"""
from .config import CavParams
from .entities import VehicleAgent


class TickEngine:
    """tick 推演引擎：按出生时刻注入车辆，每 tick 全体智能体决策推进。

    Parameters
    ----------
    topo : LShapeTopology
        路径拓扑。
    plan : list[dict]
        车辆计划，每行含 id / birth_tick / 可选 idm_params。
    has_cav : bool
        True=CAV 实验组，False=IDM 对照组。
    cav_params : CavParams, optional
        CAV 参数（对照组不启用也需传入，用于构造智能体）。
    dt : float
        每 tick 模拟秒数。
    horizon : int
        仿真时长 tick。
    """

    def __init__(self, topo, plan: list, has_cav: bool, cav_params: CavParams = None,
                 dt: float = 1.0, horizon: int = 900):
        self.topo = topo
        self.plan = plan
        self.has_cav = has_cav
        self.cav_params = cav_params or CavParams()
        self.dt = dt
        self.horizon = horizon
        self.vehicles = [VehicleAgent(row["id"], row["birth_tick"], topo,
                                      self.cav_params, row.get("idm_params"))
                         for row in plan]
        self.frames = []                  # 每 tick: [(vid, s, v)] 未到达车辆
        self.logs = []

    # ------------------------------------------------------------------
    def run(self) -> list:
        """推进整个仿真，返回全行程记录 logs。"""
        by_birth = sorted(self.vehicles, key=lambda v: v.birth_tick)
        pointer = 0
        on_road = []
        for t in range(self.horizon):
            while pointer < len(by_birth) and by_birth[pointer].birth_tick <= t:
                on_road.append(by_birth[pointer])
                pointer += 1

            on_road.sort(key=lambda v: v.s, reverse=True)
            for i, veh in enumerate(on_road):
                leader = on_road[i - 1] if i > 0 else None
                veh.step(leader, self.dt, self.has_cav)

            for veh in on_road:
                veh.s += veh.v * self.dt
                if veh.v < self.cav_params.stop_threshold:
                    veh.delay += self.dt
                if veh.s >= self.topo.total_length:
                    veh.s = self.topo.total_length
                    veh.arrived = True
                    veh.finish_tick = t + 1
            on_road = [v for v in on_road if not v.arrived]
            self.frames.append([(v.vid, v.s, v.v) for v in self.vehicles
                                if not v.arrived and v.birth_tick <= t])

        self.logs = [{
            "vid": v.vid,
            "birth": v.birth_tick,
            "arrived": v.arrived,
            "finish": v.finish_tick,
            "travel_time": (v.finish_tick - v.birth_tick) if v.arrived else None,
            "delay": v.delay,
            "avg_speed_kmh": (self.topo.total_length / (v.finish_tick - v.birth_tick) * 3.6)
                             if v.arrived else 0.0,
        } for v in self.vehicles]
        return self.logs