# -*- coding: utf-8 -*-
"""topology.py —— L 型局部拓扑（图书馆→直道→工一拐弯→直道→教一）

拆分自 CAV+MAS.py 第 1 节：把模块级常量与里程↔坐标映射、弯道限速函数
类化为 LShapeTopology，便于引擎/可视化显式注入，消除全局常量依赖。
"""
import numpy as np


class LShapeTopology:
    """L 型两臂（水平臂 len_a、垂直臂 len_b 不等长）路径拓扑。

    路径：图书馆(0,0) → 直道(len_a) → 工一拐弯 → 直道(len_b) → 教一。

    Parameters
    ----------
    len_a, len_b : float
        水平臂 / 垂直臂长度 m。
    limit_margin : float
        弯道限速区半宽 m，限速区为 [len_a - limit_margin, len_a + limit_margin]。
    v_road, v_corner : float
        路段巡航 / 弯道限速 m/s。
    lane_width : float
        路面总宽 m（动画绘制用）。

    Attributes
    ----------
    waypoints : list[tuple]
        折线关键点（含端点）。
    total_length : float
        路径总长 m。
    limit_lo, limit_hi : float
        弯道限速区里程区间。
    """

    def __init__(self, len_a: float = 220.0, len_b: float = 140.0,
                 limit_margin: float = 40.0, v_road: float = 5.0,
                 v_corner: float = 3.0, lane_width: float = 12.0):
        self.len_a = len_a
        self.len_b = len_b
        self.limit_margin = limit_margin
        self.v_road = v_road
        self.v_corner = v_corner
        self.lane_width = lane_width
        self.waypoints = [
            (0.0, 0.0),                          # 图书馆
            (len_a / 2, 0.0),                    # 直道中点
            (len_a, 0.0),                        # 工一（拐弯）
            (len_a, len_b / 2),                  # 弯后直道中点
            (len_a, len_b),                      # 教一
        ]
        self.total_length = len_a + len_b        # 路径总长 m
        self.limit_lo = len_a - limit_margin     # 限速区下界
        self.limit_hi = len_a + limit_margin     # 限速区上界

    # ------------------------------------------------------------------
    def s_to_xy(self, s: float) -> tuple:
        """里程 s → L 型路径坐标 (x, y)。"""
        s = min(max(s, 0.0), self.total_length)
        for i in range(len(self.waypoints) - 1):
            x1, y1 = self.waypoints[i]
            x2, y2 = self.waypoints[i + 1]
            seg = abs(x2 - x1) + abs(y2 - y1)
            if s <= seg:
                f = s / seg
                return x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
            s -= seg
        return self.waypoints[-1]

    # ------------------------------------------------------------------
    def speed_limit(self, s: float) -> float:
        """弯道限速函数：限速区 [limit_lo, limit_hi] 内弯道中心最低 v_corner，
        两侧以钟形平滑过渡回 v_road。"""
        if s <= self.limit_lo or s >= self.limit_hi:
            return self.v_road
        t = (s - self.limit_lo) / (self.limit_hi - self.limit_lo)
        shape = 0.5 + 0.5 * np.cos(2.0 * np.pi * t)
        return self.v_corner + (self.v_road - self.v_corner) * shape
