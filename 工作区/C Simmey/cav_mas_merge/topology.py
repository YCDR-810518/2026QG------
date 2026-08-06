# -*- coding: utf-8 -*-
"""topology.py —— MergeTopology:60° 夹角双路汇入单车道拓扑

场景:主路 A(水平)与汇入路 B(与 A 成 angle_deg 夹角)在汇合点合并为
单车道。车辆先在各自车道内行驶 len_lane 米(汇合点 s_merge = len_lane),
汇合后沿单车道继续行驶 len_single 米到出口。夹角默认 60°,体现两条
道路以锐角交汇、车流汇聚为一个车道的典型快速路匝道汇入形态。

几何约定(画布坐标,米)
----------------------
- 汇合点: (x_m, y_m) = (len_lane, 0),两路在此交汇后沿水平向右延伸
- 主路 A:水平,入口 (0, 0),方向 u_a = (1, 0)
- 汇入路 B:与 A 夹角 angle_deg,入口在左上方,
  入口 = 汇合点 - len_lane * u_b,方向 u_b = (cos θ, -sin θ)(自上而下汇入)
- 出口: (len_lane + len_single, 0)

里程约定
--------
- 汇合前:每辆车的 s 是**本车道内里程**,范围 [0, len_lane],A/B 各算各的
- 汇合后:跨过 s_merge 的车辆 s 变为**统一里程**,范围 [len_lane, len_lane + len_single]
- 总长 total_length = len_lane + len_single
"""
import numpy as np


class MergeTopology:
    """60° 夹角双路→单车道汇合拓扑。

    Parameters
    ----------
    len_lane : float
        每条车道汇合前长度 m,汇合点里程 s_merge = len_lane。
    len_single : float
        汇合后单车道段长度 m。
    angle_deg : float
        两路夹角(度),默认 60°。
    merge_zone : float
        汇合区半宽 m:冲突带为 [s_merge - merge_zone, s_merge + merge_zone]。
    v_road : float
        巡航速度 m/s。
    lane_width : float
        单条车道总宽 m(动画绘制用,半宽 = lane_width / 2)。
    """

    def __init__(self, len_lane: float = 180.0, len_single: float = 140.0,
                 angle_deg: float = 60.0, merge_zone: float = 20.0,
                 v_road: float = 5.0, lane_width: float = 10.0):
        self.len_lane = len_lane
        self.len_single = len_single
        self.angle_deg = angle_deg
        self.merge_zone = merge_zone
        self.v_road = v_road
        self.lane_width = lane_width
        self.s_merge = len_lane                     # 汇合点里程
        self.total_length = len_lane + len_single   # 路径总长 m
        self.zone_lo = len_lane - merge_zone        # 汇合区下界(减速/排队起点)
        self.zone_hi = len_lane + merge_zone        # 汇合区上界

        # ---- 画布几何 ----
        self.half = lane_width / 2.0                # 车道半宽 m(绘制用)
        theta = np.deg2rad(angle_deg)
        self.u_a = np.array([1.0, 0.0])             # 主路 A 行进方向
        self.u_b = np.array([np.cos(theta), -np.sin(theta)])   # 汇入路 B 行进方向(向下汇入)
        self.merge_xy = np.array([len_lane, 0.0])   # 汇合点坐标
        self.entry_a = np.array([0.0, 0.0])         # 主路 A 入口
        self.entry_b = self.merge_xy - len_lane * self.u_b     # 汇入路 B 入口(左上方)
        self.exit_xy = np.array([len_lane + len_single, 0.0])  # 出口

    # ------------------------------------------------------------------
    def in_merge_zone(self, s: float) -> bool:
        """s 是否处于汇合冲突带(与车道无关,统一里程判断)。"""
        return self.zone_lo <= s <= self.zone_hi

    def speed_limit(self, s: float) -> float:
        """巡航限速:全路段匀速(汇合协调由策略控制,非硬限速)。"""
        return self.v_road

    # ------------------------------------------------------------------
    def s_to_xy(self, s: float, lane: str = "A") -> tuple:
        """里程 s + 车道 → 画布坐标 (x, y)。

        汇合前:A 沿水平中线, B 沿与 A 成 60° 夹角的中线自上而下汇入;
        汇合后:全部沿水平单车道中线行驶。
        """
        s = min(max(s, 0.0), self.total_length)
        if s < self.s_merge:
            if lane == "B":
                return tuple(self.entry_b + s * self.u_b)
            return float(s), 0.0
        return float(self.merge_xy[0] + s - self.s_merge), 0.0

    def lane_center_y(self, lane: str) -> float:
        """车道中心线 y 坐标(旧版兼容接口,新场景由 s_to_xy 全权负责)。"""
        return self.half if lane == "A" else -self.half
