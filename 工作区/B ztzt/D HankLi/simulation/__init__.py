# -*- coding: utf-8 -*-
"""simulation —— 园区安全智能调控平台核心仿真引擎包（成员 F）

对外导出（对齐设计文档 §2.1）：
    TickEngine / FlowDataGenerator / JointRegulator
另有控制器 HysteresisPolicyController（大门闸+园内门，FR-19 整合）、
红绿灯 SignalPolicyController（FR-19 信号扩展）、
交通网络核心 TrafficNetwork / ShortestPathFinder / Topology、
EntityPool、压测入口 run_level / run_integration / verify_baseline。
"""
import sys
from pathlib import Path

# 包式 import（import simulation）时把本包目录加入 sys.path，绝对导入即可用
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from config import get_config
from controller import HysteresisPolicyController, SignalPolicyController
from engine import SimulationResult, TickEngine
from entities import (
    STATE_DWELL_DST,
    STATE_TRAVEL,
    STATE_WAIT_SIGNAL,
    STATE_WAIT_SRC,
    EntityPool,
    entity_dtype,
)
from flow_data_generator import FlowDataGenerator
from joint_regulator import JointRegulator
from macro_predict import MacroPredictor
from movement import BaseMovement, ConstantSpeedMovement
from movement_cav import CavIdmMovement
from sender import JsonSender, NumpyJSONEncoder, engine_snapshot_to_json, run_and_send
from csv_recorder import CsvRecorder, run_and_record, run_and_record_send
from stress_test import StressReport, run_integration, run_level, verify_baseline
from topology import ShortestPathFinder, Topology, TrafficNetwork

__all__ = [
    "TickEngine",
    "SimulationResult",
    "FlowDataGenerator",
    "HysteresisPolicyController",
    "JointRegulator",
    "MacroPredictor",
    "SignalPolicyController",
    "TrafficNetwork",
    "ShortestPathFinder",
    "Topology",
    "EntityPool",
    "entity_dtype",
    "BaseMovement",
    "ConstantSpeedMovement",
    "CavIdmMovement",
    "STATE_WAIT_SRC",
    "STATE_TRAVEL",
    "STATE_DWELL_DST",
    "STATE_WAIT_SIGNAL",
    "run_level",
    "run_integration",
    "verify_baseline",
    "StressReport",
    "get_config",
    "JsonSender",
    "NumpyJSONEncoder",
    "engine_snapshot_to_json",
    "run_and_send",
    "CsvRecorder",
    "run_and_record",
    "run_and_record_send",
]
