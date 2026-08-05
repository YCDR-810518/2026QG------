# -*- coding: utf-8 -*-
"""cav_sim.py —— CavSimulator（成员 C · FR-12/FR-15）

sklearn 风格封装微观仿真：同一车辆计划分别跑 IDM 对照组与 CAV 实验组，
汇总每节点通行效率指标（供成员 A 的宏微对齐验证与 FR-15 量化分析）。

对外接口：
    CavSimulator(tick_rate, global_density_level, cth, random_state)
        .fit(flow_config, topo=None, vehicles_plan=None) -> self
        .predict(horizon=None) -> Dict[str, Dict[str, float]]（micro_validation_results）

用法：
    sim = CavSimulator().fit(flow_config, topo=topo, vehicles_plan=plan)
    results = sim.predict(horizon=600)
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np

from agents import CAV_CTH, TickEngine

logger = logging.getLogger(__name__)

_MS_TO_KMH = 3.6  # m/s → km/h


class CavSimulator:
    """CAV 提速对比仿真器（FR-12/FR-15，sklearn 风格）。

    Parameters
    ----------
    tick_rate : float, default=1.0
        每 tick 模拟秒数。
    global_density_level : float, default=0.5
        全局拥挤度（0~1），越高期望车速越低、初始间距越大。
    cth : float, default=CAV_CTH
        CAV 恒定车头时距（秒）。
    random_state : int, default=42
        随机种子（生成车辆计划时用）。

    Attributes
    ----------
    flow_config_ : dict
        fit 时的车流配置。
    vehicles_plan_ : list of dict
        车辆计划（fit 时归一化的行列表）。
    trip_logs_idm_ / trip_logs_cav_ : list of dict
        对照组/实验组的全行程原始记录。
    results_ : dict
        predict 输出的 per-node 指标（micro_validation_results）。

    Examples
    --------
    >>> sim = CavSimulator().fit(flow_config, topo=topo, vehicles_plan=plan)
    >>> micro = sim.predict(horizon=600)
    """

    def __init__(
        self,
        tick_rate: float = 1.0,
        global_density_level: float = 0.5,
        cth: float = CAV_CTH,
        random_state: int = 42,
    ):
        self.tick_rate = tick_rate
        self.global_density_level = global_density_level
        self.cth = cth
        self.random_state = random_state

    # ------------------------------------------------------------------ fit
    def fit(
        self,
        flow_config: Dict[str, Any],
        topo: Any = None,
        vehicles_plan: Optional[Any] = None,
    ) -> "CavSimulator":
        """记录仿真配置与车辆计划，返回 self。

        Parameters
        ----------
        flow_config : dict
            车流配置（F 提供）：n_vehicles / tick_rate / global_density_level /
            horizon / nodes / edges / random_state。
        topo : object, optional
            拓扑提供方（F 的 Topology 或内置兜底图所需 nodes/edges）。
        vehicles_plan : list of dict or DataFrame, optional
            车辆计划（vehicles.csv）；缺省按 flow_config 随机生成。
        """
        self.flow_config_ = dict(flow_config)
        self.topo_ = topo
        self.vehicles_plan_ = self._to_plan(vehicles_plan) if vehicles_plan is not None else None

        self._engine_cfg_ = dict(self.flow_config_)
        self._engine_cfg_.pop("has_cav", None)
        self._engine_cfg_.setdefault("tick_rate", self.tick_rate)
        self._engine_cfg_.setdefault("global_density_level", self.global_density_level)
        self._engine_cfg_.setdefault("random_state", self.random_state)
        return self

    # --------------------------------------------------------------- predict
    def predict(self, horizon: Optional[int] = None) -> Dict[str, Dict[str, float]]:
        """跑完 IDM 对照组 + CAV 实验组，返回 per-node 通行效率指标对比。

        Parameters
        ----------
        horizon : int, optional
            仿真时长（tick），缺省用 flow_config 的 horizon。

        Returns
        -------
        dict
            {node_id: {avg_speed_idm, avg_speed_cav, efficiency_gain_pct,
                       avg_delay_time, throughput}}，速度单位 km/h。
        """
        cfg_idm = dict(self._engine_cfg_)
        cfg_idm["has_cav"] = False
        cfg_cav = dict(self._engine_cfg_)
        cfg_cav["has_cav"] = True

        self.trip_logs_idm_ = TickEngine(
            self.topo_, cfg_idm, self.vehicles_plan_
        ).run(horizon)
        self.trip_logs_cav_ = TickEngine(
            self.topo_, cfg_cav, self.vehicles_plan_
        ).run(horizon)

        self.results_ = self._aggregate(self.trip_logs_idm_, self.trip_logs_cav_)
        logger.info(
            "[cav_sim.CavSimulator.predict] 汇总 %s 个节点指标", len(self.results_),
        )
        return self.results_

    # ------------------------------------------------------------ 指标汇总
    @classmethod
    def _aggregate(
        cls,
        logs_idm: List[Dict[str, Any]],
        logs_cav: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, float]]:
        nodes = {
            c["node_id"]
            for log in logs_idm + logs_cav
            for c in log["crossings"]
        } | {
            node
            for log in logs_idm + logs_cav
            for node in log["node_delay"]
        }

        def time_avg_speed(logs: List[Dict[str, Any]], node: str) -> float:
            """时间加权平均速度（m/s）：Σ(speed×dt) / Σdt，反映走走停停的真实车速。"""
            total = sum_time = 0.0
            for log in logs:
                total += log["node_speed_sum"].get(node, 0.0)
                sum_time += log["node_time"].get(node, 0.0)
            return total / sum_time if sum_time > 0 else 0.0

        results = {}
        for node in nodes:
            sp_idm = [c["speed"] for log in logs_idm for c in log["crossings"] if c["node_id"] == node]
            sp_cav = [c["speed"] for log in logs_cav for c in log["crossings"] if c["node_id"] == node]
            delays = [
                log["node_delay"].get(node, 0.0)
                for log in logs_idm + logs_cav
            ]
            avg_idm = time_avg_speed(logs_idm, node)
            avg_cav = time_avg_speed(logs_cav, node)
            if avg_idm == 0.0 and sp_idm:
                avg_idm = float(np.mean(sp_idm))
            if avg_cav == 0.0 and sp_cav:
                avg_cav = float(np.mean(sp_cav))
            results[node] = {
                "avg_speed_idm": round(float(avg_idm) * _MS_TO_KMH, 2),
                "avg_speed_cav": round(float(avg_cav) * _MS_TO_KMH, 2),
                "efficiency_gain_pct": round(float(avg_cav - avg_idm) / float(avg_idm), 4) if avg_idm > 0 else 0.0,
                "avg_delay_time": round(float(np.mean(delays)), 2),
                "throughput": max(len(sp_idm), len(sp_cav)),
            }
        return results

    # ------------------------------------------------------------- sklearn
    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        """获取超参数（sklearn 风格）。"""
        return {
            "tick_rate": self.tick_rate,
            "global_density_level": self.global_density_level,
            "cth": self.cth,
            "random_state": self.random_state,
        }

    def set_params(self, **params) -> "CavSimulator":
        """设置超参数（sklearn 风格），返回 self。"""
        for key, value in params.items():
            if not hasattr(self, key):
                raise ValueError(f"无效参数: {key}")
            setattr(self, key, value)
        return self

    # -------------------------------------------------------------- 内部
    @staticmethod
    def _to_plan(vehicles: Any) -> List[Dict[str, Any]]:
        if hasattr(vehicles, "to_dict"):
            return list(vehicles.to_dict("records"))
        return list(vehicles)


# ===========================================================================
# 本地兜底测试：mock 拓扑 + 真实 F 拓扑各跑一遍
# ===========================================================================
if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s][%(name)s] %(message)s")

    print("\n===== 场景 1：内置兜底拓扑（40 辆车密集流入 + 南门先关 60s 排队） =====")

    def close_gate_60(t, engine):
        gate = engine.gates_.get("gate_south")
        if gate is None:
            return
        if t < 60 and gate.is_open:
            gate.set_status(False)
        elif t == 60 and not gate.is_open:
            gate.set_status(True)

    mock_cfg = {
        "horizon": 900,
        "on_tick": close_gate_60,
        "nodes": ["gate_south", "node_15", "node_22", "zone_canteen"],
        "edges": [
            ("gate_south", "node_15", 100.0),
            ("node_15", "node_22", 120.0),
            ("node_22", "zone_canteen", 80.0),
        ],
    }
    mock_plan = [
        {"id": f"car_{i}", "birth_tick": i, "src_node": "gate_south",
         "dst_node": "zone_canteen", "is_internal": 0}
        for i in range(40)
    ]
    sim = CavSimulator(global_density_level=0.8).fit(mock_cfg, vehicles_plan=mock_plan)
    micro = sim.predict()
    for node, metrics in micro.items():
        print(f"{node}: {metrics}")

    print("\n===== 场景 2：真实拓扑 + F 的 vehicles.csv（前 200 行） =====")
    import pandas as pd

    sys.path.insert(0, r"D:\code\2026QG暑期中期考核\工作区\F zdzdzdzdz\simulation")
    sys.path.insert(0, r"D:\code\2026QG暑期中期考核\项目目录")
    from topology import Topology

    topo = Topology()
    df = pd.read_csv(r"D:\code\2026QG暑期中期考核\工作区\F zdzdzdzdz\data\vehicles.csv")
    df = df.head(200)
    sim2 = CavSimulator(global_density_level=0.6).fit(
        {"horizon": 7200}, topo=topo, vehicles_plan=df
    )
    micro2 = sim2.predict()
    for node, metrics in list(micro2.items())[:6]:
        print(f"{node}: {metrics}")
    print(f"...共 {len(micro2)} 个节点")
