# -*- coding: utf-8 -*-
"""movement.py —— 微观移动模型插件骨架（成员 F，供 C 扩展 IDM/CAV）

默认实现 ConstantSpeedMovement：行人 1.3 m/s、车辆 5.0 m/s 匀速（与生成器
_travel_tick 速度一致）；C 可在该骨架基础上覆盖车辆跟驰/加速逻辑（IDM / CAV），
经 config.yaml 指定类名即可替换（见设计文档 §7.1）。

依赖：numpy
"""
import numpy as np

from topology import Topology


class BaseMovement:
    """微观移动模型基类（替换点：movement）。

    Parameters
    ----------
    topology : Topology
        园区拓扑实例。
    """

    def __init__(self, topology=None):
        self.topology = topology

    def update_speed(self, pool):
        """按当前实体状态更新速度（向量化钩子，tick 内被引擎调用）。"""
        raise NotImplementedError

    def get_params(self, deep=True):
        return {}

    def set_params(self, **params):
        return self


class ConstantSpeedMovement(BaseMovement):
    """匀速移动模型（默认实现）：行人与车辆按固定巡航速度移动。

    仅在实体进入移动态（STATE_TRAVEL）时维护速度；C 可重写 update_speed
    实现 IDM / CAV 跟驰。
    """

    def update_speed(self, pool):
        data = pool.data
        active = data["active"]
        moving = active & (data["state"] == 1)
        if not np.any(moving):
            return
        speed = np.where(data["kind"][moving] == 1, Topology.base_speed(1),
                         Topology.base_speed(0)).astype(np.float32)
        data["speed"][moving] = speed


if __name__ == "__main__":
    from entities import EntityPool
    from topology import Topology

    topo = Topology()
    pool = EntityPool(capacity=10)
    slots = pool.allocate(2)
    pool.data[slots[0]]["kind"] = 0
    pool.data[slots[1]]["kind"] = 1
    pool.data[slots[0]]["state"] = 1
    pool.data[slots[1]]["state"] = 1
    mov = ConstantSpeedMovement(topo)
    mov.update_speed(pool)
    print("ped speed:", pool.data[slots[0]]["speed"], "| veh speed:", pool.data[slots[1]]["speed"])
