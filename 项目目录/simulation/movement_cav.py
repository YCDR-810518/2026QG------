# -*- coding: utf-8 -*-
"""movement_cav.py —— CAV/IDM 跟驰移动模型（成员 C，集成到 F 引擎）

提供 CavIdmMovement(BaseMovement)，支持两种模式：
  - "idm" : IDM 跟驰（对照组，每车独立决策）
  - "cav" : CAV 编队跟驰（实验组，车联网协同 CTH）

用法：
    from movement_cav import CavIdmMovement
    engine = TickEngine(topo, generator, movement=CavIdmMovement(topo, mode="cav"))

依赖：numpy
"""

import numpy as np

from movement import BaseMovement

# ---------------------------------------------------------------------------
# 默认参数（与 C 的 agents.py 中 DEFAULT_IDM_PARAMS / CAV_CTH 一致）
# ---------------------------------------------------------------------------
_IDM_DEFAULTS = {
    "v0": 5.0,      # 期望速度 m/s（园区巡航，与 base_speed(1) 一致）
    "a_max": 1.5,   # 最大加速度 m/s²
    "b": 2.0,       # 舒适减速度 m/s²
    "s0": 2.0,      # 最小安全间距 m
    "t_head": 1.5,  # 跟车时距 s
}
_CAV_CTH = 0.8    # CAV 恒定车头时距 s（编队跟驰）
_CAV_KV = 0.6     # CAV 速度差增益
_CAV_KG = 0.4     # CAV 间距差增益


class CavIdmMovement(BaseMovement):
    """CAV/IDM 跟驰移动模型。

    Parameters
    ----------
    topology : Topology
        园区拓扑实例（提供 edge_length、n_nodes 等）。
    mode : str {"idm", "cav"}
        "idm" = IDM 跟驰对照组 | "cav" = CAV 编队实验组。
    v0 : float
        期望巡航速度 m/s（缺省 5.0）。
    a_max : float
        最大加速度 m/s²（缺省 1.5）。
    b : float
        舒适减速度 m/s²（缺省 2.0）。
    s0 : float
        停车最小间距 m（缺省 2.0）。
    t_head : float
        IDM 跟车时距 s（缺省 1.5）。
    cth : float
        CAV 恒定车头时距 s（缺省 0.8）。
    kv : float
        CAV 速度差增益（缺省 0.6）。
    kg : float
        CAV 间距差增益（缺省 0.4）。
    """

    def __init__(self, topology, mode="idm", **kwargs):
        super().__init__(topology)
        self.mode = mode
        self.params = _IDM_DEFAULTS.copy()
        for key in ("v0", "a_max", "b", "s0", "t_head", "cth", "kv", "kg"):
            if key in kwargs:
                self.params[key] = kwargs[key]

    # ------------------------------------------------------------------
    def update_speed(self, pool):
        data = pool.data

        # 1. 筛选活跃 + 在途(TRAVEL) + 车辆
        veh_mask = data["active"] & (data["kind"] == 1) & (data["state"] == 1)
        veh_idx = np.nonzero(veh_mask)[0]
        if veh_idx.size == 0:
            return

        # 2. 取出车辆数据列（本地副本，稍后写回 speed 列）
        cur = data["cur_node"][veh_idx]
        tgt = data["edge_target"][veh_idx]
        pos = data["edge_pos"][veh_idx].copy()
        spd = data["speed"][veh_idx].copy()

        n_nodes = self.topology.n_nodes

        # 3. 边标识 = cur * n_nodes + tgt（每组 = 同一条路）
        edge_key = cur.astype(np.int64) * n_nodes + tgt.astype(np.int64)

        # 4. 按边分组，组内按 edge_pos 排序，逐车更新速度
        new_speed = spd.copy()
        unique_keys = np.unique(edge_key)

        for key in unique_keys:
            group_mask = edge_key == key
            g_idx = np.nonzero(group_mask)[0]
            if g_idx.size == 1:
                s = g_idx[0]
                new_speed[s] = self._free_cruise(spd[s])
                continue

            # 同边内按 edge_pos 升序排列（pos 小的在后，大的在前）
            order = np.argsort(pos[g_idx])
            sorted_local = g_idx[order]

            for i, loc in enumerate(sorted_local):
                if i == len(sorted_local) - 1:
                    # 最靠前：无 leader，自由巡航
                    new_speed[loc] = self._free_cruise(spd[loc])
                else:
                    leader_loc = sorted_local[i + 1]
                    gap = pos[leader_loc] - pos[loc]
                    if self.mode == "cav":
                        new_speed[loc] = self._cav_step(
                            spd[loc], spd[leader_loc], gap
                        )
                    else:
                        new_speed[loc] = self._idm_step(
                            spd[loc], spd[leader_loc], gap, spd[loc] - spd[leader_loc]
                        )

        # 5. 写回 float32
        data["speed"][veh_idx] = new_speed.astype(np.float32)

    # ------------------------------------------------------------------
    # 内部速度计算
    # ------------------------------------------------------------------
    def _free_cruise(self, v):
        """无前车巡航：向期望速度 v0 加速。"""
        p = self.params
        acc = p["a_max"] * (1.0 - (v / p["v0"]) ** 4)
        return max(0.0, v + acc)

    def _idm_step(self, v, v_leader, gap, dv):
        """IDM 跟驰（对照组）：经典 Intelligent Driver Model。"""
        p = self.params
        s_star = p["s0"] + max(
            0.0, v * p["t_head"] + v * dv / (2.0 * np.sqrt(p["a_max"] * p["b"]))
        )
        acc = p["a_max"] * (
            1.0 - (v / p["v0"]) ** 4 - (s_star / max(gap, 1e-6)) ** 2
        )
        return max(0.0, v + acc)

    def _cav_step(self, v, v_leader, gap):
        """CAV 编队跟驰（实验组）：CTH 恒定车头时距协同。"""
        p = self.params
        cth = p.get("cth", _CAV_CTH)
        kv = p.get("kv", _CAV_KV)
        kg = p.get("kg", _CAV_KG)
        target_gap = v * cth + p["s0"]
        acc = kv * (v_leader - v) + kg * (gap - target_gap)
        acc = np.clip(acc, -p["b"], p["a_max"])
        return max(0.0, v + acc)

    # ------------------------------------------------------------------
    def get_params(self, deep=True):
        d = {"mode": self.mode}
        d.update(self.params)
        return d

    def set_params(self, **params):
        for k, v in params.items():
            if k == "mode":
                self.mode = v
            else:
                self.params[k] = v
        return self


# ============================================================================
# 本地自测
# ============================================================================
if __name__ == "__main__":
    from entities import EntityPool
    from topology import Topology

    topo = Topology()
    pool = EntityPool(capacity=20)

    # 分配 4 辆车，2 辆在 edge 0→2，2 辆在 edge 1→3
    veh_slots = pool.allocate(4)
    for s in veh_slots:
        pool.data["kind"][s] = 1
        pool.data["state"][s] = 1
    # edge 0→2
    pool.data["cur_node"][veh_slots[0]] = 0
    pool.data["edge_target"][veh_slots[0]] = 2
    pool.data["edge_pos"][veh_slots[0]] = 10.0
    pool.data["speed"][veh_slots[0]] = 3.0

    pool.data["cur_node"][veh_slots[1]] = 0
    pool.data["edge_target"][veh_slots[1]] = 2
    pool.data["edge_pos"][veh_slots[1]] = 25.0
    pool.data["speed"][veh_slots[1]] = 4.0

    # edge 1→3
    pool.data["cur_node"][veh_slots[2]] = 1
    pool.data["edge_target"][veh_slots[2]] = 3
    pool.data["edge_pos"][veh_slots[2]] = 5.0
    pool.data["speed"][veh_slots[2]] = 2.0

    pool.data["cur_node"][veh_slots[3]] = 1
    pool.data["edge_target"][veh_slots[3]] = 3
    pool.data["edge_pos"][veh_slots[3]] = 20.0
    pool.data["speed"][veh_slots[3]] = 4.5

    # IDM 模式测试
    mov_idm = CavIdmMovement(topo, mode="idm")
    print("=== IDM 模式 ===")
    print("前 speed:", [pool.data["speed"][s] for s in veh_slots])
    mov_idm.update_speed(pool)
    print("后 speed:", [pool.data["speed"][s] for s in veh_slots])

    # 重置
    pool.data["speed"][veh_slots[0]] = 3.0
    pool.data["speed"][veh_slots[1]] = 4.0
    pool.data["speed"][veh_slots[2]] = 2.0
    pool.data["speed"][veh_slots[3]] = 4.5

    # CAV 模式测试
    mov_cav = CavIdmMovement(topo, mode="cav")
    print("\n=== CAV 模式 ===")
    print("前 speed:", [pool.data["speed"][s] for s in veh_slots])
    mov_cav.update_speed(pool)
    print("后 speed:", [pool.data["speed"][s] for s in veh_slots])
