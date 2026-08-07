# -*- coding: utf-8 -*-
"""entities.py —— 实体结构化数组与预分配实体池（成员 F）

实体为定长结构化数组，人/车共用一张表，按 kind 区分；
EntityPool 预分配 capacity 个槽位，空闲槽栈复用，避免 8000+ 对象 GC 与动态扩容。

依赖：numpy
"""
import numpy as np

# 实体状态机
STATE_WAIT_SRC = 0   # 在起点等待（出发前排/过闸）
STATE_TRAVEL = 1     # 沿边移动
STATE_DWELL_DST = 2  # 在终点停留
STATE_WAIT_SIGNAL = 3  # 在信号路口排队等待

# ---------------------------------------------------------------------------
# 实体结构化 dtype（见设计文档 §3.1 + 四态扩展）
# ---------------------------------------------------------------------------
entity_dtype = np.dtype([
    ("id", np.int32),          # 唯一 ID
    ("kind", np.int8),         # 0=行人, 1=车辆
    ("src_node", np.int32),    # 起点节点序号
    ("dst_node", np.int32),    # 终点节点序号
    ("state", np.int8),        # 状态机 0~3
    ("cur_node", np.int32),    # 当前所在节点（等待/停留/排队时 >=0，移动时=当前边起点）
    ("edge_target", np.int32), # 移动时当前边终点节点（-1=在节点上）
    ("edge_pos", np.float32),  # 沿当前边行进偏移（米）
    ("speed", np.float32),     # 当前速度（m/s）
    ("wait_ticks", np.int32),  # 剩余等待/停留 tick 计数
    ("path_pos", np.int32),    # 路径节点序列下标（移动时指向当前起点）
    ("active", np.bool_),      # 是否在场
])


class EntityPool:
    """预分配结构化数组实体池（槽位复用）。

    Parameters
    ----------
    capacity : int
        实体池预分配上限（>8000 并发量）。

    Attributes
    ----------
    data : np.ndarray (capacity,)
        结构化数组（entity_dtype）。
    paths : list
        按槽位存储的路径序号序列（np.ndarray 或 None）。
    """

    def __init__(self, capacity=10000):
        self.capacity = int(capacity)
        self.data = np.empty(self.capacity, dtype=entity_dtype)
        self.data["active"] = False
        self.data["state"] = STATE_WAIT_SRC
        self.data["cur_node"] = -1
        self.data["edge_target"] = -1
        self.data["edge_pos"] = 0.0
        self.data["path_pos"] = 0
        self.data["wait_ticks"] = 0
        self.paths = [None] * self.capacity
        self._free = list(range(self.capacity))
        self._counter = 0
        self.n_spawned = 0
        self.n_recycled = 0

    # ------------------------------------------------------------------
    @property
    def active_mask(self):
        """全场在场布尔掩码。"""
        return self.data["active"]

    @property
    def n_active(self):
        return int(self.active_mask.sum())

    # ------------------------------------------------------------------
    def allocate(self, n):
        """分配 n 个空闲槽位，标记 active。

        Parameters
        ----------
        n : int
            需要的槽位数。

        Returns
        -------
        np.ndarray of int32
            分配的槽位下标数组；不足 n 时返回实际可分配数量。
        """
        n = min(int(n), len(self._free))
        if n <= 0:
            return np.empty(0, dtype=np.int32)
        slots = np.asarray(self._free[:n], dtype=np.int32)
        self._free = self._free[n:]
        ids = np.arange(self._counter, self._counter + n, dtype=np.int32)
        self._counter += n
        self.n_spawned += n
        # 注意：结构化数组 fancy 索引返回副本，必须"字段优先"就地写入
        d = self.data
        d["id"][slots] = ids
        d["active"][slots] = True
        d["state"][slots] = STATE_WAIT_SRC
        d["cur_node"][slots] = -1
        d["edge_target"][slots] = -1
        d["edge_pos"][slots] = 0.0
        d["speed"][slots] = 0.0
        d["wait_ticks"][slots] = 0
        d["path_pos"][slots] = 0
        return slots

    def set_path(self, slot, path):
        """为槽位写入路径序号序列。"""
        self.paths[slot] = np.asarray(path, dtype=np.int32)

    def path(self, slot):
        """取槽位路径。"""
        return self.paths[slot]

    def recycle(self, mask):
        """回收 mask（全场布尔）对应槽位。

        Parameters
        ----------
        mask : np.ndarray of bool
            与 data 同长度的布尔掩码。
        """
        idx = np.nonzero(mask)[0]
        if idx.size == 0:
            return
        self.data["active"][idx] = False
        for s in idx:
            self.paths[s] = None
        self._free.extend(int(s) for s in idx)
        self.n_recycled += int(idx.size)

    def recycle_ids(self, ids):
        """按槽位下标回收。"""
        ids = np.asarray(ids, dtype=np.int32)
        if ids.size == 0:
            return
        self.data["active"][ids] = False
        for s in ids:
            self.paths[s] = None
        self._free.extend(int(s) for s in ids)
        self.n_recycled += int(ids.size)


if __name__ == "__main__":
    pool = EntityPool(capacity=100)
    slots = pool.allocate(5)
    pool.set_path(slots[0], np.array([0, 1, 2]))
    pool.data[slots] = np.rec.fromarrays(
        [np.arange(5, dtype=np.int32), np.zeros(5, dtype=np.int8),
         np.zeros(5, dtype=np.int32), np.ones(5, dtype=np.int32),
         np.zeros(5, dtype=np.int8), np.zeros(5, dtype=np.int32),
         np.zeros(5, dtype=np.int32), np.zeros(5, dtype=np.float32),
         np.full(5, 1.3, dtype=np.float32), np.zeros(5, dtype=np.int32),
         np.zeros(5, dtype=np.int32), np.ones(5, dtype=np.bool_)],
        dtype=entity_dtype)
    print("active:", pool.n_active, "| path0:", pool.path(slots[0]))
    pool.recycle(pool.active_mask & (pool.data["id"] < 3))
    print("after recycle active:", pool.n_active, "| free:", len(pool._free))
