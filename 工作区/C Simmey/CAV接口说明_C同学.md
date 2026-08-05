# CAV/IDM 集成指南（给 C 同学）

## 一、F 引擎已经帮你做了什么？

F 的 `TickEngine` 每 tick 执行以下步骤：

```
ingest(生成车辆) → update_speed(← 你的代码插在这里) → state_machine(车辆推进/到终点) → signal_release → aggregate → regulate
```

- **你不需处理**：车辆位置推进（`edge_pos += speed * dt`）、路由切换、信号排队、终点停留、实体回收
- **你只需做**：根据当前路况，计算每辆车的**新速度**，写回 `pool.data["speed"]`

你的插入点是继承 `movement.BaseMovement`，重写 `update_speed(self, pool)`。

---

## 二、你需要向 F 索要的数据

### 2.1 引擎传入的数据

`update_speed(self, pool)` 被调用时，你可以访问：

| 来源 | 字段/方法 | 说明 |
|------|----------|------|
| `pool.data` | numpy 结构化数组 | 所有实体（人+车）共用的定长表，行数=capacity(默认10000)，按 `active` 筛选有效实体 |
| `self.topology` | Topology 实例 | 路网拓扑，提供边长度、节点类型等 |

### 2.2 pool.data —— 实体表字段（entity_dtype）

```python
# 对 update_speed 有用的字段：
data["active"]       # bool     — 是否在场（必须先筛选 active=True）
data["kind"]         # int8     — 0=行人，1=车辆
data["state"]        # int8     — 0=WAIT_SRC, 1=TRAVEL, 2=DWELL, 3=WAIT_SIGNAL
data["cur_node"]     # int32    — 当前边起点节点序号（0~n_nodes-1）
data["edge_target"]  # int32    — 当前边终点节点序号
data["edge_pos"]     # float32  — 当前边内已走距离（米）
data["speed"]        # float32  — **读写**：当前速度，你要把新速度写回这个字段
```

> **关键约束**：只修改 **active & kind==1 & state==1** 的实体（即在路上行驶的车辆），行人维持原速度不动。

### 2.3 pool.paths —— 车辆路径

```python
path = pool.paths[slot]   # np.ndarray of int32，形如 [3, 7, 12, 5]
                           # path[0]=起点, path[-1]=终点
                           # cur_node 对应 path[path_pos], edge_target 对应 path[path_pos+1]
```

### 2.4 self.topology —— 拓扑数据

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `self.topology.edge_length[i, j]` | float32 | 边 i→j 的长度（米），无直接边时=-1 |
| `self.topology.is_signal[i]` | bool | 节点 i 是否为红绿灯 |
| `self.topology.is_gate[i]` | bool | 节点 i 是否为大门 |
| `self.topology.gate_nodes` | list[int] | 大门节点序号列表 |
| `self.topology.signal_nodes` | list[int] | 信号灯节点序号列表 |
| `self.topology.n_nodes` | int | 节点总数 |
| `self.topology.base_speed(kind)` | 静态方法 | 返回默认巡航速度：人 1.3 m/s，车 5.0 m/s |

---

## 三、核心逻辑：怎么找前车（leader）

### 3.1 数据模型差异说明

| C 已有代码 | F 引擎 |
|-----------|--------|
| 每辆车是独立的 `VehicleAgent` 对象 | 所有车在同一张 numpy 结构化数组中 |
| 按边分组用 `edges: Dict[tuple, List[VehicleAgent]]` | 需从 pool.data 中按 `(cur_node, edge_target)` 分组 |
| 按 `edge_pos` 升序排列，`lst[k+1]` 是 leader | **注意**：edge_pos 越大越靠近边终点，前车 edge_pos > 后车 |

### 3.2 leader 查找逻辑

同一条边上有多辆车时：
- `edge_pos` 小的车在后面，`edge_pos` 大的车在前面
- 对于车辆 A，它的 leader（前车）是同边上**edge_pos 大于 A 且最小的**那个
- **edge_pos 最大的那辆车没有 leader**（最靠前的那辆）

```python
# 伪代码：对每组同边车辆，按 edge_pos 升序排列
# arr = sorted_by_edge_pos
# for i in range(len(arr)):
#     if i == len(arr) - 1:
#         leader = None   # 最前面的车
#     else:
#         leader = arr[i + 1]   # 后一个就是edge_pos更大的那辆
```

### 3.3 向量化实现思路

F 引擎的实体量大（8000+），建议尽可能用 numpy 向量化，按以下步骤：

1. 筛选活跃+TRAVEL+车辆 → 得到车辆索引数组 `veh_idx`
2. 用 `(cur_node, edge_target)` 组合成边标识 key
3. 按 key 分组 + 组内按 edge_pos 排序
4. 为每组构建 leader 索引映射
5. 批量计算 IDM/CAV 速度

---

## 四、代码模板

在 `simulation/` 目录下创建你自己的文件，继承 `BaseMovement`：

```python
# 文件: my_idm_movement.py
import numpy as np
from movement import BaseMovement

_IDM_DEFAULT = {
    "v0": 5.0,      # 期望速度 m/s
    "a_max": 1.5,   # 最大加速度 m/s²
    "b": 2.0,       # 舒适减速度 m/s²
    "s0": 2.0,      # 最小安全间距 m
    "t_head": 1.5,  # 跟车时距 s
}
_CAV_CTH = 0.8      # CAV 车头时距 s

class MyIdmMovement(BaseMovement):
    def __init__(self, topology, mode="idm", **idm_params):
        super().__init__(topology)
        self.mode = mode              # "idm" 或 "cav"
        self.params = _IDM_DEFAULT.copy()
        self.params.update(idm_params)

    def update_speed(self, pool):
        data = pool.data
        # 1. 筛选活跃+在途+车辆
        veh_mask = data["active"] & (data["kind"] == 1) & (data["state"] == 1)
        veh_idx = np.nonzero(veh_mask)[0]
        if veh_idx.size == 0:
            return

        # 2. 取出车辆数据列
        cur = data["cur_node"][veh_idx]
        tgt = data["edge_target"][veh_idx]
        pos = data["edge_pos"][veh_idx].copy()
        spd = data["speed"][veh_idx].copy()

        # 3. 构建边 key（cur_node * n_nodes + edge_target）
        edge_key = cur.astype(np.int64) * self.topology.n_nodes + tgt.astype(np.int64)

        # 4. 按边分组，组内排序，计算新速度
        new_speed = spd.copy()
        unique_keys = np.unique(edge_key)
        for key in unique_keys:
            group_mask = edge_key == key
            g_idx = np.nonzero(group_mask)[0]
            if g_idx.size <= 1:
                # 单辆车：自由巡航
                s = g_idx[0]
                new_speed[s] = self._free_cruise(spd[s])
                continue

            # 按 edge_pos 升序排列
            order = np.argsort(pos[g_idx])
            sorted_local = g_idx[order]

            for i, loc in enumerate(sorted_local):
                if i == len(sorted_local) - 1:
                    # 最前面的车：无 leader，自由巡航
                    new_speed[loc] = self._free_cruise(spd[loc])
                else:
                    leader_loc = sorted_local[i + 1]
                    gap = pos[leader_loc] - pos[loc]
                    dv = spd[loc] - spd[leader_loc]
                    if self.mode == "cav":
                        new_speed[loc] = self._cav_step(spd[loc], spd[leader_loc], gap, dv)
                    else:
                        new_speed[loc] = self._idm_step(spd[loc], spd[leader_loc], gap, dv)

        # 5. 写回
        data["speed"][veh_idx] = new_speed.astype(np.float32)

    def _free_cruise(self, v):
        """无前车时向期望速度加速"""
        p = self.params
        acc = p["a_max"] * (1 - (v / p["v0"]) ** 4)
        return max(0.0, v + acc)

    def _idm_step(self, v, v_leader, gap, dv):
        """IDM 跟驰（对照组）"""
        p = self.params
        s_star = p["s0"] + max(0.0, v * p["t_head"] + v * dv / (2 * np.sqrt(p["a_max"] * p["b"])))
        acc = p["a_max"] * (1 - (v / p["v0"]) ** 4 - (s_star / max(gap, 1e-6)) ** 2)
        return max(0.0, v + acc)

    def _cav_step(self, v, v_leader, gap, dv):
        """CAV 编队跟驰（实验组）"""
        p = self.params
        cth = p.get("cth", _CAV_CTH)
        target_gap = v * cth + p["s0"]
        acc = 0.6 * dv + 0.4 * (gap - target_gap)
        acc = np.clip(acc, -p["b"], p["a_max"])
        return max(0.0, v + acc)
```

---

## 五、怎么跑仿真

### 方式一：通过 config.yaml 切换（推荐）

在 `config.yaml` 的 `simulation` 节下增加：

```yaml
simulation:
  movement_class: MyIdmMovement   # 默认 ConstantSpeedMovement
  movement_mode: cav              # idm / cav
```

F 侧会据此动态加载你的类。你的 `.py` 文件放在 `simulation/` 目录下即可被 import。

### 方式二：直接传参

```python
from my_idm_movement import MyIdmMovement
from engine import TickEngine

mov = MyIdmMovement(topo, mode="cav", v0=5.0, cth=0.8)
engine = TickEngine(topo, generator, movement=mov)
```

### 运行命令

```bash
cd simulation
python main.py run
```

---

## 六、参数速查表

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `v0` | 5.0 m/s | 期望巡航速度（与 F 引擎 base_speed(1) 一致） |
| `a_max` | 1.5 m/s² | 最大加速度 |
| `b` | 2.0 m/s² | 舒适减速度 |
| `s0` | 2.0 m | 停车最小间距 |
| `t_head` | 1.5 s | IDM 跟车时距 |
| `cth` | 0.8 s | CAV 恒定车头时距 |

---

## 七、注意事项

1. **只改车辆**：`kind==0` 的行人不要动，F 引擎自带的 `ConstantSpeedMovement` 已处理
2. **edge_pos 方向**：数值越大越靠近边终点（前车）；数值小的在后面
3. **速度单位**：统一 m/s，与 F 引擎的 `base_speed(1)=5.0` 对齐
4. **性能要求**：8000+ 实体量级下，尽量避免 Python 逐行循环；优先使用 numpy 向量化分组操作
5. **tick 频率**：1 tick = 1 秒，`update_speed` 返回的速度即下一 tick 的瞬时速度
6. **当前版本不提供信号预判**：`update_speed` 被调用时信号相位尚未计算。如需红绿灯预判减速，后续单独沟通扩展接口
