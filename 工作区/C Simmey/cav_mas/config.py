# -*- coding: utf-8 -*-
"""config.py —— cav_mas 包的场景与仿真参数（拆分自 CAV+MAS.py）

只存放与"怎么跑"相关的配置常量，不依赖任何其他模块；
拓扑尺寸/限速参数归 LShapeTopology（topology.py）管理。
"""
from dataclasses import dataclass

# --- 驾驶员跟驰基础参数（IDM） ------------------------------------------
DEFAULT_IDM = {"v0": 5.0, "a_max": 1.5, "b": 2.0, "s0": 2.0, "t_head": 1.5}

# --- 场景参数 -----------------------------------------------------------
N_VEHICLES = 40          # 车辆数
SPAWN_INTERVAL = 1.0     # 发车间隔 s
HORIZON = 900            # 仿真时长 tick
SEED = 42                # 随机种子（本场景为确定性场景）

# --- 隐私保护参数 --------------------------------------------------------
DP_EPSILON = 1.0         # 差分隐私预算 ε（越小噪声越大、保护越强）


@dataclass
class CavParams:
    """CAV 实验组车联网协同参数（编队跟驰 + 前视限速）。"""

    cth: float = 0.8                 # 恒定车头时距 s
    kv: float = 0.6                  # 速度差增益
    kg: float = 0.4                  # 间距差增益
    lookahead: float = 25.0          # 前视限速距离 m
    stop_threshold: float = 1.0      # m/s 以下计为滞留
