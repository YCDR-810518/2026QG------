# -*- coding: utf-8 -*-
"""config.py —— cav_mas_merge 包(60° 夹角双路汇入单车道协同汇合)的场景与仿真参数

只存放"怎么跑"相关的配置常量;拓扑尺寸/夹角/限速参数归 MergeTopology
(topology.py) 管理。本模块不依赖其他任何模块。
"""
from dataclasses import dataclass

# --- 驾驶员跟驰基础参数(IDM) ------------------------------------------
DEFAULT_IDM = {"v0": 5.0, "a_max": 1.5, "b": 2.0, "s0": 2.0, "t_head": 1.5}

# --- 场景参数 -----------------------------------------------------------
N_VEHICLES = 40          # 车辆数(双车道交替分配)
SPAWN_INTERVAL = 1.0     # 发车间隔 s
HORIZON = 900            # 仿真时长 tick
SEED = 42                # 随机种子(本场景为确定性场景)

# --- 汇合协调参数 --------------------------------------------------------
COORD_RANGE = 60.0       # 协调器感知范围 m(进入此范围即申请时隙)
MERGE_SLOT_GAP = 1.5     # 汇合时隙间隔 s(两车通过汇合点的最小时间差,需≤车流自然间隔)
MIN_LOOKAHEAD = 2.0      # 时隙最早可分配时刻: now + MIN_LOOKAHEAD
STOP_SPEED = 0.3         # m/s 以下视为停车(指标统计用)


@dataclass
class CavParams:
    """CAV 实验组车联网协同参数(编队跟驰 + 汇合时隙控制)。"""

    cth: float = 0.8                 # 恒定车头时距 s(车道内编队)
    kv: float = 0.6                  # 速度差增益
    kg: float = 0.4                  # 间距差增益
    lookahead: float = 25.0          # 前视限速距离 m
    stop_threshold: float = 1.0      # m/s 以下计为滞留
    k_slot: float = 0.8              # 时隙跟踪增益(目标速度逼近系数)
    v_merge_min: float = 2.5         # 通过汇合点的最低速度 m/s(防止多车
                                     # 爬行挤团导致单车道内摩擦死锁)


@dataclass
class MergeParams:
    """汇合协调器参数(策略在 coordinator 上选,此处只存常数)。"""

    coord_range: float = COORD_RANGE      # 感知/申请范围 m
    slot_gap: float = MERGE_SLOT_GAP      # 时隙间隔 s
    min_lookahead: float = MIN_LOOKAHEAD  # 最早分配时刻提前量 s
    stop_speed: float = STOP_SPEED        # 停车判定阈值 m/s
