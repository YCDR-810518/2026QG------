# -*- coding: utf-8 -*-
"""cav_mas —— L 型局部拓扑（直道+拐弯）CAV+MAS 对比实验包

模块化拆分自原单文件 CAV+MAS.py，依赖方向单向、无循环引用：

    config ← topology ← entities ← engine ← metrics
    privacy 仅依赖 config/topology（对外发布数据差分隐私脱敏）
    visualization 仅依赖 config/topology
    main 统一组装并驱动整个实验

用法：
    python run_cav_mas.py   # 入口脚本（等价于原单文件行为）
    python -m cav_mas.main  # 或直接以模块方式运行
"""
from .config import (DEFAULT_IDM, DP_EPSILON, HORIZON, N_VEHICLES, SEED,
                     SPAWN_INTERVAL, CavParams)
from .entities import VehicleAgent
from .engine import TickEngine
from .metrics import summarize
from .privacy import apply_differential_privacy, export_private_paths
from .topology import LShapeTopology
from .visualization import (make_animation, plot_speed_delay_bar,
                            plot_space_time, plot_travel_time_box)

__version__ = "1.1.0"

__all__ = [
    "DEFAULT_IDM", "DP_EPSILON", "HORIZON", "N_VEHICLES", "SEED",
    "SPAWN_INTERVAL", "CavParams",
    "LShapeTopology", "VehicleAgent", "TickEngine", "summarize",
    "apply_differential_privacy", "export_private_paths",
    "plot_travel_time_box", "plot_speed_delay_bar", "plot_space_time",
    "make_animation",
]
