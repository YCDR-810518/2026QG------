# -*- coding: utf-8 -*-
"""entities.py —— 车辆智能体（MAS 单元，拆分自 CAV+MAS.py 的 VehicleAgent）

IDM（对照组）：经典智能驾驶员模型，每车独立决策，对前车减速反应激进；
CAV（实验组）：车联网协同，CTH 恒定车头时距编队 + 前视限速平滑减速。
"""
import numpy as np

from .config import DEFAULT_IDM


class VehicleAgent:
    """车辆智能体（MAS 单元）：沿 L 型路径自主决策跟驰行驶。

    Parameters
    ----------
    vid : str
        车辆唯一标识（如 "car_00"）。
    birth_tick : int
        出生（进入道路）时刻。
    topo : LShapeTopology
        路径拓扑（提供 s_to_xy / speed_limit / total_length）。
    cav_params : CavParams
        CAV 编队与前视限速参数。
    idm_params : dict, optional
        驾驶员跟驰参数（缺省用 DEFAULT_IDM）。

    Attributes
    ----------
    s, v : float
        当前里程 m / 速度 m/s。
    arrived, finish_tick : bool/int
        是否到达终点及到达时刻。
    delay : float
        滞留（低速行驶）累计时间 s。
    """

    def __init__(self, vid: str, birth_tick: int, topo, cav_params, idm_params=None):
        self.vid = vid
        self.birth_tick = birth_tick
        self.topo = topo
        self.cav_params = cav_params
        self.s = 0.0                      # 里程 m
        self.v = 0.0                      # 速度 m/s
        self.arrived = False
        self.finish_tick = None
        self.delay = 0.0                  # 滞留累计 s
        self.p = dict(DEFAULT_IDM) if idm_params is None else dict(idm_params)

    # ------------------------------------------------------------------
    def step(self, leader, dt: float, has_cav: bool):
        """按策略计算加速度并更新速度（位置由引擎统一推进）。"""
        v = self.v
        v_lim = self.topo.speed_limit(self.s)
        p = self.p
        if leader is None:
            gap, dv = np.inf, 0.0
        else:
            gap = max(0.0, leader.s - self.s)
            dv = v - leader.v

        if has_cav:
            acc = 0.0
            if leader is not None:
                target_gap = v * self.cav_params.cth + p["s0"]
                acc = (self.cav_params.kv * (leader.v - v)
                       + self.cav_params.kg * (gap - target_gap))
            acc = float(np.clip(acc, -p["b"], p["a_max"]))
            acc += 0.5 * (self.topo.speed_limit(self.s + self.cav_params.lookahead) - v)
        else:
            s_star = p["s0"] + max(0.0, v * p["t_head"]
                                   + v * dv / (2.0 * np.sqrt(p["a_max"] * p["b"])))
            acc = p["a_max"] * (1.0 - (v / p["v0"]) ** 4 - (s_star / max(gap, 1e-6)) ** 2)
            if v > v_lim:
                acc = min(acc, -p["b"])

        self.v = max(0.0, min(v_lim, v + acc * dt))
