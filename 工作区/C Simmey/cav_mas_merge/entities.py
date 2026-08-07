# -*- coding: utf-8 -*-
"""entities.py —— 60° 夹角汇合场景两类 MAS 智能体

MergeVehicleAgent(车辆智能体)
    - 车道内跟驰:IDM(对照组)/ CTH 编队(实验组),复用 cav_mas 的跟驰公式
    - 汇合决策:状态机 APPROACH → MERGING → MERGED
      * APPROACH:进入协调器感知范围后向协调器申请目标时隙(slot_time)
      * MERGING:按 "v = 剩余里程 / 剩余时间" 反推目标速度,到点即插、不停车
    - 对照组无 CAV:主车道 A 优先,汇入车道 B 在汇合口等待空档(停车排队)

MergeCoordinator(协调器智能体)
    - 集中式决策主体,三种策略可切换:
      gap      时隙分配:按预计到达汇合点的先后 FCFS 排序,依次分配目标时隙
      zipper   拉链式:按到达次序在两车道间严格交替分配时隙
      consensus 一致性协商:对"期望到达时刻"做平均一致性收敛后按时序分隙
"""
import numpy as np

from .config import DEFAULT_IDM, MergeParams

_CREEP_SPEED = 0.8    # 让行时蠕动滑行速度下限 m/s(人工驾驶不停死,找空档插入)
_MERGE_SLOW = 3.0     # 人工驾驶合流区减速上限 m/s(两车道共担让行责任)


class MergeVehicleAgent:
    """汇合场景车辆智能体(MAS 单元)。

    Parameters
    ----------
    vid : str
        车辆唯一标识。
    birth_tick : int
        出生(进入道路)时刻。
    topo : MergeTopology
        汇合拓扑(60° 夹角双路→单车道)。
    lane : str
        "A" 主车道 / "B" 汇入车道。
    cav_params : CavParams
        CAV 编队与时隙控制参数。
    idm_params : dict, optional
        驾驶员跟驰参数(缺省用 DEFAULT_IDM)。
    """

    def __init__(self, vid, birth_tick, topo, lane, cav_params, idm_params=None):
        self.vid = vid
        self.birth_tick = birth_tick
        self.topo = topo
        self.lane = lane
        self.cav_params = cav_params
        self.s = 0.0                       # 里程 m(车道内里程=统一里程)
        self.v = 0.0                       # 速度 m/s
        self.merge_state = "APPROACH"      # APPROACH / MERGING / MERGED
        self.slot_time = None              # 协调器分配的目标到达时刻
        self.arrived = False
        self.finish_tick = None
        self.cross_tick = None             # 跨过汇合点的时刻
        self.delay = 0.0                   # 低速滞留累计 s
        self.p = dict(DEFAULT_IDM) if idm_params is None else dict(idm_params)

    # ------------------------------------------------------------------
    def step(self, leader, t, dt, has_cav, blocked=False):
        """按策略计算加速度并更新速度(位置由引擎统一推进)。

        Parameters
        ----------
        leader : MergeVehicleAgent or None
            前车(同车道更靠前,或已汇入单车道的更靠前车辆)。
        t : float
            当前仿真时刻。
        dt : float
            步长 s。
        has_cav : bool
            True=CAV 实验组,False=IDM 对照组。
        blocked : bool
            仅对照组有效:汇入车道 B 在汇合口是否被主车道车流挡住。
        """
        v = self.v
        v_lim = self.topo.speed_limit(self.s)
        p = self.p
        c = self.cav_params

        if not has_cav and self.topo.in_merge_zone(self.s):
            # 人工驾驶:合流区自觉减速让行(两车道都降速,不设绝对优先)
            v_lim = min(v_lim, _MERGE_SLOW)

        if leader is None:
            gap, dv = np.inf, 0.0
        else:
            gap = max(0.0, leader.s - self.s)
            dv = v - leader.v

        if has_cav:
            # ---- 实验组:CTH 编队跟驰 + 汇合时隙控制 ----
            acc = 0.0
            if leader is not None:
                target_gap = v * c.cth + p["s0"]
                acc += c.kv * (leader.v - v) + c.kg * (gap - target_gap)
            else:
                acc += p["a_max"] * (1.0 - (v / p["v0"]) ** 4)   # 无前车自由巡航
            if self.slot_time is not None and self.s < self.topo.s_merge:
                remain = max(self.topo.s_merge - self.s, 1e-6)
                remain_t = max(self.slot_time - t, 0.5)
                # 下限 = 汇合点最低通过速度:防止多车爬行挤团,
                # 且避免时隙顺延造成"无限爬行"死锁
                v_tgt = max(remain / remain_t, c.v_merge_min)
                acc += c.k_slot * (v_tgt - v)
            acc = float(np.clip(acc, -p["b"], p["a_max"]))
        else:
            # ---- 对照组:IDM 独立决策 + 主路优先让行 ----
            s_star = p["s0"] + max(0.0, v * p["t_head"]
                                   + v * dv / (2.0 * np.sqrt(p["a_max"] * p["b"])))
            acc = p["a_max"] * (1.0 - (v / p["v0"]) ** 4 - (s_star / max(gap, 1e-6)) ** 2)
            if blocked:
                acc = min(acc, -p["b"])                   # 减速让行(不刹停)
            if v > v_lim:
                acc = min(acc, -p["b"])

        self.v = max(0.0, min(v_lim, v + acc * dt))
        if not has_cav:
            # 人工驾驶蠕动下限:合流区以怠速蠕行找空档,全程不停死
            self.v = max(self.v, _CREEP_SPEED)


class MergeCoordinator:
    """汇合协调器智能体(集中式 MAS 决策主体)。

    Parameters
    ----------
    strategy : str
        "gap" 时隙分配 / "zipper" 拉链式交替 / "consensus" 一致性协商。
    params : MergeParams, optional
        协调参数(感知范围 / 时隙间隔等)。
    """

    def __init__(self, strategy: str = "gap", params: MergeParams = None):
        if strategy not in ("gap", "zipper", "consensus"):
            raise ValueError(f"未知汇合策略: {strategy}")
        self.strategy = strategy
        self.params = params or MergeParams()
        self.last_slot = -np.inf            # 已分配的最新时隙
        self.n_assigned = 0                 # 累计分配数

    # ------------------------------------------------------------------
    def update(self, on_road, t):
        """每 tick 调用:为进入感知范围的车辆分配/刷新目标时隙。

        Parameters
        ----------
        on_road : list[MergeVehicleAgent]
            当前在途车辆。
        t : float
            当前时刻。
        """
        p = self.params
        s_merge = on_road[0].topo.s_merge if on_road else None
        # 申请者:已进入感知范围、未跨汇合点、且未持有有效时隙的车辆
        requests = [
            v for v in on_road
            if v.s >= s_merge - p.coord_range and v.s < s_merge
            and v.merge_state == "APPROACH"
            and (v.slot_time is None or v.slot_time < t)
        ]
        if not requests:
            return

        if self.strategy == "zipper":
            self._assign_zipper(requests, t)
        elif self.strategy == "consensus":
            self._assign_consensus(requests, t)
        else:
            self._assign_gap(requests, t)

    # ------------------------------------------------------------------
    def _arrival_est(self, v, t):
        """预计到达汇合点时刻:按车辆期望速度 v0 外推(车联网中协调器
        可知每车期望巡航速度,即"理想到达时刻")。"""
        v_des = v.p.get("v0", 5.0)
        return t + max(0.0, v.topo.s_merge - v.s) / max(v_des, 1e-6)

    def _assign_gap(self, requests, t):
        """gap 策略:FCFS 按预计到达时刻排序;slot = 预计到达时刻,
        与前车时隙冲突(间距 < slot_gap)时向后顺延。"""
        p = self.params
        for v in sorted(requests, key=lambda v: self._arrival_est(v, t)):
            est = self._arrival_est(v, t)
            v.slot_time = max(est, self.last_slot + p.slot_gap)
            self.last_slot = v.slot_time
            v.merge_state = "MERGING"
            self.n_assigned += 1

    def _assign_zipper(self, requests, t):
        """zipper 策略:两车道按预计到达次序严格交替分配时隙
        (slot = 预计到达时刻,依次顺延保证交替间隔)。"""
        p = self.params
        by_lane = {"A": [], "B": []}
        for v in requests:
            by_lane[v.lane].append(v)
        for lane in by_lane:
            by_lane[lane].sort(key=lambda v: self._arrival_est(v, t))

        # 从预计最早到达的车道开始,两车道交替出列(拉链式)
        order = []
        first = "A" if by_lane["A"] and (not by_lane["B"] or
                self._arrival_est(by_lane["A"][0], t) <= self._arrival_est(by_lane["B"][0], t)) else "B"
        cur = first
        while by_lane["A"] or by_lane["B"]:
            if by_lane[cur]:
                order.append(by_lane[cur].pop(0))
            cur = "B" if cur == "A" else "A"
            if not by_lane[cur]:
                cur = "A" if cur == "B" else "B"
        for v in order:
            v.slot_time = max(self._arrival_est(v, t),
                              self.last_slot + p.slot_gap)
            self.last_slot = v.slot_time
            v.merge_state = "MERGING"
            self.n_assigned += 1

    def _assign_consensus(self, requests, t):
        """consensus 策略:对期望到达时刻做平均一致性收敛,按时序围绕均值分隙。"""
        p = self.params
        n = len(requests)
        # 第 0 轮:各车给出自己的期望到达时刻(按当前速度外推)
        desired = {v.vid: self._arrival_est(v, t) for v in requests}
        # 平均一致性:与全体均值取平均,迭代 n 轮后收敛到全局均值
        for _ in range(n):
            mean = sum(desired.values()) / n
            for vid in desired:
                desired[vid] = 0.5 * (desired[vid] + mean)
        consensus_mean = sum(desired.values()) / n
        # 围绕收敛均值按期望时刻排序分隙,最小化整体偏差;
        # base 以最早进入批次车辆的估计为锚,但不得早于当前时刻 + 最小提前量
        # (防止给已接近汇合点的车分配"已过期时隙"导致反复刹车锁死)。
        order = sorted(requests, key=lambda v: desired[v.vid])
        est_first = self._arrival_est(order[0], t)
        base = max(est_first - (n - 1) * p.slot_gap / 2.0,
                   consensus_mean - (n - 1) * p.slot_gap / 2.0,
                   t + p.min_lookahead)
        for i, v in enumerate(order):
            v.slot_time = base + i * p.slot_gap
            self.last_slot = v.slot_time
            v.merge_state = "MERGING"
            self.n_assigned += 1
