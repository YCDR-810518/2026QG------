# -*- coding: utf-8 -*-
"""
data_config.py — 数据格式集中配置（MindSpore 版，与 d_mindspore 一致）
======================================================================
成员F的 density_series.csv 列结构若变化，只改这里，train/predict/alter_predict/alert 都引用本文件。

新格式表头：
tick, timestamp, node_id, people, vehicles, density, level,
gate_status, gate_flow_rate, door_status, door_flow_rate, signal_status, signal_flow_rate

说明：
  - gate_status / door_status  : 门闸状态，字符串 open / restricted / closed
  - signal_status              : 信号灯状态，字符串 red / yellow / green
  - gate_flow_rate / door_flow_rate / signal_flow_rate : 速率，int
  - 大量空值统一填 0（引擎快照中速率列多为空）
"""

# 原始列名（按 CSV 表头顺序）
RAW_COLUMNS = [
    "tick",
    "timestamp",
    "node_id",
    "people",
    "vehicles",
    "density",
    "level",
    "gate_status",
    "gate_flow_rate",
    "door_status",
    "door_flow_rate",
    "signal_status",
    "signal_flow_rate",
]

# 预测目标列（density）
DENSITY_COL = "density"

# level 由字符串转数字
LEVEL_MAP = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# 门闸状态字符串 → 数值（实测取值：open / restricted / closed，空值统一填 0）
GATE_STATUS_MAP = {"open": 2, "restricted": 1, "closed": 0}
SIGNAL_STATUS_MAP = {"red": 0, "yellow": 1, "green": 2}

# ---- 模型特征列（状态特征） ----
# 顺序固定，训练时保存到 preprocessor.json，predict/alert 按此构造。
# 连续列：density, people, vehicles, gate_flow_rate, door_flow_rate, signal_flow_rate
# 类别列：level, gate_status, door_status, signal_status（编码为数值）
STATE_FEATURES = [
    "density",            # 0 预测目标
    "people",             # 1
    "vehicles",           # 2
    "gate_status",        # 3 编码 open→2 restricted→1 closed→0
    "gate_flow_rate",     # 4
    "level",              # 5 编码 low→1...critical→4
    "door_status",        # 6 编码 open→2 restricted→1 closed→0
    "door_flow_rate",     # 7
    "signal_status",      # 8 编码 red→0 yellow→1 green→2
    "signal_flow_rate",   # 9
]

# 预测目标在"特征矩阵 STATE_FEATURES"中的索引（train.py 取目标、评估都用这个）
DENSITY_COL_IDX = STATE_FEATURES.index(DENSITY_COL)   # = 0

# 时间特征：从 timestamp 派生
TIME_FEATURES = ["hour_sin", "hour_cos", "dow_sin", "dow_cos"]

# 全部特征 = 状态特征 + 时间特征
ALL_FEATURES = STATE_FEATURES + TIME_FEATURES
N_STATE = len(STATE_FEATURES)

# 需要 Min-Max 归一化的连续列索引（类别列不归一化）
# 状态特征索引：density=0, people=1, vehicles=2, gate_flow_rate=4,
#              door_flow_rate=7, signal_flow_rate=9
NORMALIZE_COLS = [0, 1, 2, 4, 7, 9]
