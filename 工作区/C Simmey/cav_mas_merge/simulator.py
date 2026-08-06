# -*- coding: utf-8 -*-
"""simulator.py —— MergeSimulator(成员 C · FR-12/FR-15,sklearn 风格)

封装"60° 夹角双路→单车道协同汇合"对比实验:同一车辆计划分别跑
IDM 对照组(主路优先)与 CAV 实验组(协调器时隙),汇总通行效率 +
汇合专项指标。

对外接口:
    MergeSimulator(strategy, tick_rate, cth, random_state)
        .fit(flow_config, topo=None, vehicles_plan=None) -> self
        .predict(horizon=None) -> Dict[str, Dict[str, float]]

用法:
    sim = MergeSimulator(strategy="gap").fit(flow_config, topo=topo, vehicles_plan=plan)
    results = sim.predict(horizon=600)
"""
import logging
from typing import Any, Dict, List, Optional

from .config import CavParams, DEFAULT_IDM
from .engine import MergeTickEngine
from .metrics import merge_metrics, summarize
from .topology import MergeTopology

logger = logging.getLogger(__name__)


class MergeSimulator:
    """60° 夹角双路→单车道汇合协同仿真器(FR-12/FR-15,sklearn 风格)。

    Parameters
    ----------
    strategy : str, default="gap"
        汇合协调策略:"gap" / "zipper" / "consensus"。
    tick_rate : float, default=1.0
        每 tick 模拟秒数。
    cth : float, default=0.8
        CAV 恒定车头时距(秒)。
    random_state : int, default=42
        随机种子(生成车辆计划时用)。

    Attributes
    ----------
    flow_config_ / vehicles_plan_ : fit 时记录
    trip_logs_idm_ / trip_logs_cav_ : 对照组/实验组全行程记录
    crossings_idm_ / crossings_cav_ : 汇合点穿越记录
    queue_idm_ / queue_cav_ : 汇合区逐 tick 排队序列
    results_ : predict 输出的指标对比
    """

    def __init__(self, strategy: str = "gap", tick_rate: float = 1.0,
                 cth: float = 0.8, random_state: int = 42):
        self.strategy = strategy
        self.tick_rate = tick_rate
        self.cth = cth
        self.random_state = random_state

    # ------------------------------------------------------------------ fit
    def fit(self, flow_config: Dict[str, Any], topo: Any = None,
            vehicles_plan: Optional[Any] = None) -> "MergeSimulator":
        """记录仿真配置与车辆计划,返回 self。

        Parameters
        ----------
        flow_config : dict
            车流配置:n_vehicles / horizon / lane_ratio / random_state /
            len_lane / len_single / angle_deg 等。
        topo : MergeTopology, optional
            汇合拓扑;缺省按 flow_config 构造。
        vehicles_plan : list of dict, optional
            车辆计划(id / birth_tick / lane);缺省按 flow_config 生成。
        """
        self.flow_config_ = dict(flow_config)
        self.topo_ = topo or MergeTopology(
            len_lane=flow_config.get("len_lane", 180.0),
            len_single=flow_config.get("len_single", 140.0),
            angle_deg=flow_config.get("angle_deg", 60.0),
        )
        self.vehicles_plan_ = (self._to_plan(vehicles_plan)
                               if vehicles_plan is not None
                               else self._make_plan(flow_config))
        return self

    # --------------------------------------------------------------- predict
    def predict(self, horizon: Optional[int] = None) -> Dict[str, Dict[str, float]]:
        """跑完 IDM 对照组 + CAV 实验组,返回通行效率 + 汇合专项指标对比。"""
        horizon = horizon or self.flow_config_.get("horizon", 900)
        cav_params = CavParams(cth=self.cth)

        eng_idm = MergeTickEngine(self.topo_, self.vehicles_plan_, has_cav=False,
                                  cav_params=cav_params, dt=self.tick_rate, horizon=horizon)
        self.trip_logs_idm_ = eng_idm.run()
        eng_cav = MergeTickEngine(self.topo_, self.vehicles_plan_, has_cav=True,
                                  strategy=self.strategy, cav_params=cav_params,
                                  dt=self.tick_rate, horizon=horizon)
        self.trip_logs_cav_ = eng_cav.run()

        self.crossings_idm_ = eng_idm.crossings
        self.crossings_cav_ = eng_cav.crossings
        self.queue_idm_ = eng_idm.queue_series
        self.queue_cav_ = eng_cav.queue_series

        s_idm, s_cav = summarize(self.trip_logs_idm_), summarize(self.trip_logs_cav_)
        m_idm = merge_metrics(self.trip_logs_idm_, self.crossings_idm_, self.queue_idm_)
        m_cav = merge_metrics(self.trip_logs_cav_, self.crossings_cav_, self.queue_cav_)

        tt_gain = (s_idm["tt_mean"] - s_cav["tt_mean"]) / max(s_idm["tt_mean"], 1e-9)
        q_gain = (m_idm["queue_mean"] - m_cav["queue_mean"]) / max(m_idm["queue_mean"], 1e-9)
        self.results_ = {
            "tt_mean_idm": round(s_idm["tt_mean"], 2),
            "tt_mean_cav": round(s_cav["tt_mean"], 2),
            "tt_reduce_pct": round(tt_gain * 100, 2),
            "speed_kmh_idm": round(s_idm["speed_kmh"], 2),
            "speed_kmh_cav": round(s_cav["speed_kmh"], 2),
            "delay_mean_idm": round(s_idm["delay_mean"], 2),
            "delay_mean_cav": round(s_cav["delay_mean"], 2),
            "merge_throughput_idm": m_idm["throughput_per_min"],
            "merge_throughput_cav": m_cav["throughput_per_min"],
            "queue_mean_idm": m_idm["queue_mean"],
            "queue_mean_cav": m_cav["queue_mean"],
            "queue_max_idm": m_idm["queue_max"],
            "queue_max_cav": m_cav["queue_max"],
            "queue_reduce_pct": round(q_gain * 100, 2),
            "slot_dev_mean_s": m_cav["slot_dev_mean"],
        }
        logger.info("[MergeSimulator.predict] strategy=%s 汇总完成", self.strategy)
        return self.results_

    # ------------------------------------------------------------- sklearn
    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        return {"strategy": self.strategy, "tick_rate": self.tick_rate,
                "cth": self.cth, "random_state": self.random_state}

    def set_params(self, **params) -> "MergeSimulator":
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

    def _make_plan(self, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """按配置生成车辆计划:双车道交替出生(默认 A/B 交替,保证公平)。"""
        import numpy as np

        rng = np.random.default_rng(cfg.get("random_state", self.random_state))
        n = cfg.get("n_vehicles", 40)
        interval = cfg.get("spawn_interval", 1.0)
        lane_ratio = cfg.get("lane_ratio", 0.5)
        idm_base = dict(DEFAULT_IDM, **cfg.get("idm_params", {}))
        plan = []
        for i in range(n):
            lane = "A" if rng.random() < lane_ratio else "B"
            plan.append({
                "id": f"car_{i:02d}",
                "birth_tick": int(i * interval),
                "lane": lane,
                "idm_params": dict(idm_base, v0=float(np.clip(rng.normal(5.0, 0.5), 4.0, 6.0))),
            })
        return plan
