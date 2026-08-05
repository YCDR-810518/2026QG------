# -*- coding: utf-8 -*-
"""joint_regulator.py —— 宏观-微观联合调控器（成员 F，FR-18）

宏观层每 check_interval tick 识别密集区域（结合密度 + A 热度指标），按优先级
从 budget_pool 分配微观计算预算；闲置区按 decay 回收，形成
宏观 → 微观 → 反馈 → 宏观 闭环。v1 采用简化链路（区域排序 → 预算分配 →
decay 回收 → 全局策略标识），供 8.6 与 A/C 联调扩展。

对外 sklearn 风格：fit / predict / get_params / set_params，
predict 返回 {regions, global_plan}。

依赖：numpy
用法：
    jr = JointRegulator()
    jr.fit(macro_cfg)
    plan = jr.predict({"tick": t, "density": dens, "node_ids": ids, "heat": heat})
"""
import numpy as np


class JointRegulator:
    """宏观-微观联合调控器（FR-18，简化闭环）。

    Parameters
    ----------
    check_interval : int
        调控触发间隔（tick）。
    budget_pool : float
        总预算池。
    decay : float
        闲置区每周期预算回收率（0~1）。
    dense_threshold : float
        视为密集区的密度阈值。
    top_k : int
        单周期最多纳入分配的密集区数。

    Attributes
    ----------
    node_ids_ : list of str
        fit 后拓扑节点编码（学习属性）。
    budget_ : dict
        各区域当前分配预算（学习属性）。
    """

    def __init__(self, check_interval=10, budget_pool=100.0, decay=0.5,
                 dense_threshold=0.6, top_k=5):
        self.check_interval = int(check_interval)
        self.budget_pool = float(budget_pool)
        self.decay = float(decay)
        self.dense_threshold = float(dense_threshold)
        self.top_k = int(top_k)
        self.node_ids_ = None
        self.budget_ = {}

    # ------------------------------------------------------------------ sklearn 风格
    def fit(self, macro_cfg=None):
        """学习拓扑节点集合与宏观热度配置。

        Parameters
        ----------
        macro_cfg : dict, optional
            含 "node_ids" 的拓扑信息；缺省仅初始化空预算。

        Returns
        -------
        JointRegulator
            返回 self。
        """
        if macro_cfg and "node_ids" in macro_cfg:
            self.node_ids_ = list(macro_cfg["node_ids"])
            self.budget_ = {nid: 0.0 for nid in self.node_ids_}
        return self

    def get_params(self, deep=True):
        return {
            "check_interval": self.check_interval,
            "budget_pool": self.budget_pool,
            "decay": self.decay,
            "dense_threshold": self.dense_threshold,
            "top_k": self.top_k,
        }

    def set_params(self, **params):
        for k, v in params.items():
            if not hasattr(self, k):
                raise ValueError(f"Unknown param: {k}")
            setattr(self, k, v)
        return self

    # ------------------------------------------------------------------ 调控
    def predict(self, state):
        """按当前状态输出资源分配方案。

        Parameters
        ----------
        state : dict
            引擎状态：{"tick": int, "density": (n_nodes,) 或 (n_nodes, 2),
                      "node_ids": list[str], "heat": (n_nodes,), optional}。

        Returns
        -------
        dict
            {"regions": [{"region_id", "priority", "compute_budget"}],
             "global_plan": str}。
        """
        dens = np.asarray(state["density"], dtype=np.float64)
        if dens.ndim == 2:
            dens = dens[:, 0]
        node_ids = state.get("node_ids", self.node_ids_ or [])
        heat = np.asarray(state.get("heat", 0.0), dtype=np.float64)
        if heat.ndim == 0:
            heat = np.zeros_like(dens)

        # 宏观识别：密度 + 热度（可归一）加权排序
        score = dens + 0.5 * np.clip(heat, 0.0, 1.0)
        order = np.argsort(-score)
        dense_idx = [i for i in order if dens[i] >= self.dense_threshold][: self.top_k]

        # 预算回收（闲置区按 decay 回收）
        for nid in self.budget_:
            self.budget_[nid] *= (1.0 - self.decay)

        # 按优先级分配
        total_score = float(score[dense_idx].sum()) if dense_idx else 0.0
        regions = []
        for i in dense_idx:
            priority = float(np.clip(score[i] / max(total_score, 1e-9), 0.0, 1.0))
            alloc = self.budget_pool * priority if total_score > 0 else 0.0
            nid = node_ids[i] if i < len(node_ids) else f"zone_{i}"
            self.budget_[nid] = self.budget_.get(nid, 0.0) + alloc
            regions.append({"region_id": nid, "priority": round(priority, 3),
                            "compute_budget": round(self.budget_[nid], 2)})

        global_plan = "focus_dense" if (dens > self.dense_threshold).any() else "normal"
        return {"regions": regions, "global_plan": global_plan}


if __name__ == "__main__":
    jr = JointRegulator()
    jr.fit({"node_ids": [f"node_{i}" for i in range(6)]})
    plan = jr.predict({"tick": 0, "density": np.array([0.1, 0.8, 0.7, 0.2, 0.9, 0.3]),
                       "node_ids": [f"node_{i}" for i in range(6)]})
    print(plan)
