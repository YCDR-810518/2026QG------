# DjShortCut 结构与使用说明（v1.3）

> 模块：~~DjShortCut.py~~ → **simulation/topology.py**（整合版）｜ 负责：成员 A（宏观路径）+ 成员 F（拓扑适配）
>
> 对应需求：FR-07（Dijkstra 最短路径）
>
> 对外接口：sklearn 风格（fit / predict / get_params / set_params）
>
> v1.1 更新：新增节点级闸机动态权值控制
>
> v1.2 更新：新增节点级红绿灯相位动态权值控制
>
> v1.3 更新：门/闸分离——门（door）控制边权，闸（gate）仅控制车辆入口吞吐
>
> v2.0 更新：**DjShortCut.py 与 simulation/topology.py 合并**——`TrafficNetwork` / `ShortestPathFinder` / `Topology` 统一收敛到 `simulation/topology.py`；状态词表统一为 `open/restricted/closed`；新增控制状态/路径缓存 JSON 持久化；统一入口 `simulation/main.py`。

## 一、模块定位

`DjShortCut.py` 负责校园交通网络的**最短路径计算**，支持动态**门**状态和**红绿灯**相位对路径权值的实时影响。

**门与闸的区别**：

| | 门 (door) | 闸 (gate) | 红绿灯 (signal) |
|---|---|---|---|
| 字段 | `doorId` | `gateId` | `signalId` |
| 范围 | **全部 61 个节点** | 仅 3 个 entrance 节点 | 7 个 `has_traffic_light` 节点 |
| 控制内容 | 人车拥堵流控 → **影响边权** | 车辆入口吞吐 → **不影响边权** | 通行顺序 → **影响边权** |
| 接口 | `set_door_states` / `predict(door_states=...)` | `set_gate_states`（仅车辆流量） | `set_signal_states` / `predict(signal_states=...)` |

对外提供两个类：
   
| 类 | 用途 | sklearn 风格 |
|---|---|---|
| `TrafficNetwork` | 从 YAML 加载拓扑配置，构建无向邻接表，管理门/红绿灯/闸状态缓存 | 否（纯数据容器） |
| `ShortestPathFinder` | 基于 Dijkstra 算法计算两节点间加权最短路径，支持门 + 红绿灯动态控制 | **是**（fit / predict） |

## 二、文件结构

```
DjShortCut.py
├── 模块级日志（logging.getLogger(__name__)）
├── TrafficNetwork 类
│   ├── __init__(nodes, edges, node_types)       # 构造，只赋值
│   ├── from_yaml(yaml_path)                     # classmethod，YAML → 实例
│   ├── _build_adjacency()                       # 私有，惰性构建邻接表
│   ├── get_node(node_id)                        # 查询节点属性
│   ├── get_neighbors(node_id)                   # 查询邻接节点
│   ├── get_edge_weight(src, dst)                # 查询边权
│   ├── set_door_states(door_states) → self      # 批量更新门状态缓存（影响边权）
│   ├── get_door_states() → dict                 # 获取当前门状态缓存
│   ├── get_doored_nodes() → list[str]           # 获取所有带门节点（=全部 61 个）
│   ├── _resolve_door_state(node_id, override)   # 私有，按优先级解析门状态
│   ├── set_signal_states / get_signal_states / ...  # 红绿灯（不变）
│   └── set_gate_states / get_gate_states / ...  # 闸（仅车辆入口吞吐，不影响边权）
├── ShortestPathFinder 类（sklearn 风格）
│   ├── __init__(penalty_factor=10.0)            # 构造，含门 restricted 惩罚倍数
│   ├── _SIGNAL_RED_WEIGHT = 1000.0              # 类常量，红灯/off 惩罚（内部）
│   ├── _SIGNAL_YELLOW_WEIGHT = 3.0              # 类常量，黄灯惩罚（内部）
│   ├── fit(graph) → self                        # 加载 TrafficNetwork
│   ├── predict(src, dst, door_states=None, signal_states=None) → list
│   ├── get_params(deep=True) → dict             # 含 penalty_factor
│   ├── set_params(**params) → self
│   └── _dijkstra(src, dst, door_states, signal_states)  # 最小堆 Dijkstra + 门 + 信号
└── if __name__ == "__main__": 自测代码（基础 + 门控 + 红绿灯）
```

## 三、核心接口说明

### 3.1 TrafficNetwork

#### `from_yaml(yaml_path)`

**classmethod**，从 `graph_data.yaml` 加载拓扑配置。

```python
from pathlib import Path
from DjShortCut import TrafficNetwork

yaml_path = Path(__file__).parent / "graph_data.yaml"
network = TrafficNetwork.from_yaml(yaml_path)
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `yaml_path` | `str \| Path` | YAML 文件路径 |

| 返回 | 说明 |
|---|---|
| `TrafficNetwork` | 加载完成的网络拓扑实例 |

| 异常 | 触发条件 |
|---|---|
| `FileNotFoundError` | YAML 文件不存在 |
| `yaml.YAMLError` | YAML 解析失败 |

#### 门状态管理（v1.3，影响边权）

##### `set_door_states(door_states)` → self

```python
network.set_door_states({
    "gate_west": "closed",      # 封门，该节点所有邻边不可通行
    "library": "restricted",    # 限流，通过该节点的边权 × penalty_factor
    "gate_south": "open",       # 正常通行（默认值）
})
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `door_states` | `dict` | `{node_id: "open" \| "closed" \| "restricted"}` |

> **有效状态说明**：
> - `"open"` — 正常通行，边权不变
> - `"closed"` — 封门，该节点所有邻边被 Dijkstra 跳过（不可通行）。**例外**：作为终点时可到达
> - `"restricted"` — 限流，通过该节点的邻边权值 × `penalty_factor`

##### `get_door_states()` → dict

```python
network.get_door_states()
# → {"gate_west": "closed", "library": "restricted"}
```

##### `get_doored_nodes()` → list[str]

返回全部 61 个节点 ID（所有节点均可设门）。

#### 红绿灯状态管理（v1.2，影响边权）

`set_signal_states()` / `get_signal_states()` / `get_signaled_nodes()` — 7 个 `has_traffic_light` 节点。

#### 闸状态管理（v1.3，仅车辆入口吞吐，不影响边权）

`set_gate_states()` / `get_gate_states()` / `get_gated_nodes()` — 仅 3 个 entrance 节点，用于控制车辆入口流量。`ShortestPathFinder.predict()` **不使用** gate_states 做边权调整。

---

### 3.2 ShortestPathFinder（sklearn 风格核心）

#### `__init__(penalty_factor=10.0)`

```python
planner = ShortestPathFinder()                   # 默认惩罚倍数 10.0
planner = ShortestPathFinder(penalty_factor=5.0)  # 自定义惩罚倍数
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `penalty_factor` | `float` | `10.0` | 门 `restricted` 时的边权惩罚倍数 |

> 红绿灯罚权为内部常量，不对外暴露：`_SIGNAL_RED_WEIGHT=1000.0`、`_SIGNAL_YELLOW_WEIGHT=3.0`

#### `fit(graph)` → self

```python
planner = ShortestPathFinder().fit(network)
```

#### `predict(src, dst, door_states=None, signal_states=None)` → list[str] ← v1.3 更新

**两种工作模式**：

| 模式 | 调用方式 | 门/信号来源 | 适用场景 |
|---|---|---|---|
| **自动感知**（推荐） | `predict(src, dst)` 无参 | `network._door_states` / `network._signal_states` 缓存 | F 持续更新门状态，A 随时查最新路径 |
| **临时覆盖** | `predict(src, dst, door_states=..., signal_states=...)` | 参数优先于缓存 | What-if 查询（"假如图书馆门关了"） |

```python
# 自动感知：F 改了门状态后，A 无参 predict 直接感知
network.set_door_states({"gate_west": "closed"})
network.set_signal_states({"cross_zh_mid": {"phase": "red"}})
path = planner.predict("gate_west", "gate_east")  # 自动避开封门+红灯

# 临时覆盖：参数优先级高于缓存
path = planner.predict("gate_south", "canteen_1",
    door_states={"gate_west": "open"},   # 临时解除缓存中的 closed
)
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `src` | `str` | 起点节点 ID |
| `dst` | `str` | 终点节点 ID |
| `door_states` | `dict, optional` | 实时门状态覆盖 |
| `signal_states` | `dict, optional` | 实时红绿灯状态覆盖 |

**门/信号状态优先级**（两者一致）：
```
predict(door_states=传入)   >  TrafficNetwork._door_states 缓存    >  默认 "open"
predict(signal_states=传入) >  TrafficNetwork._signal_states 缓存  >  默认 "green"
```

#### `get_params()` / `set_params(**params)`

```python
planner.get_params()
# → {"penalty_factor": 10.0}
planner.set_params(penalty_factor=5.0).fit(network)
```

## 四、完整使用示例

### 4.1 基础最短路径

```python
from pathlib import Path
from DjShortCut import TrafficNetwork, ShortestPathFinder

yaml_path = Path(__file__).parent / "graph_data.yaml"
network = TrafficNetwork.from_yaml(yaml_path)
planner = ShortestPathFinder().fit(network)

path = planner.predict(src="gate_south", dst="canteen_1")
print(f"最短路径: {' → '.join(path)}")
```

输出：
```
最短路径: gate_south → admin_building → library → gongchuanggu → underpass → east_dorm_12_14 → east_dorm_8_11 → supermarket → canteen_1
总权值: 11.34
```

### 4.2 门动态控制（v1.3）

```python
network.set_door_states({
    "gate_west": "closed",      # 封西门
    "library": "restricted",    # 图书馆限流
})

# 方式一：自动感知（无参 predict）
path = planner.predict("gate_south", "canteen_1")

# 方式二：临时覆盖（参数优先于缓存）
path = planner.predict("gate_south", "canteen_1",
    door_states={"gate_west": "open"},
)

# 清除缓存
network.set_door_states({"gate_west": "open", "library": "open"})
```

### 4.3 红绿灯 + 门双层控制

```python
path = planner.predict("gate_south", "canteen_1",
    door_states={"library": "restricted"},
    signal_states={"cross_zh_mid": {"phase": "red"}},
)
# → 门 restricted ×10 + 信号 red ×1000 = 连乘惩罚
```

## 五、算法细节

### Dijkstra（最小堆实现 + 门感知 + 红绿灯感知）

- **时间复杂度**: O((V + E) log V)，V=61 节点，E=105 边
- **边权**: 使用 YAML 中的 `weight` 字段
- **图类型**: 无向图（每条边双向可通）

### 门 + 红绿灯双层感知算法流程

```
1. 初始化 distances[src]=0, 其余为 ∞
2. 将 (0, src) 推入最小堆
3. 循环弹出堆顶节点:
   a. 已访问则跳过
   b. 到达 dst 则终止
   c. 松弛每条邻边 (current → neighbor):
      i.   门检查:
           - 解析 neighbor 门状态（override > 缓存 > 默认 "open"）
           - "closed" 且 neighbor ≠ dst → 跳过该边
           - "restricted" → effective_weight = base_weight × penalty_factor
           - "open" → effective_weight = base_weight
      ii.  红绿灯检查（门罚权的基础上继续）:
           - 解析 neighbor 红绿灯相位（override > 缓存 > 默认 "green"）
           - "red" / "off" 且 neighbor ≠ dst → effective_weight × 1000
           - "yellow" → effective_weight × 3
           - "green" → 不变
      iii. new_dist = dist[current] + effective_weight
           若更优则更新 distances / predecessors，并入堆
4. 从 predecessors 回溯构建路径
```

## 六、数据依赖

```
项目目录/
├── DjShortCut.py          ← 本模块
├── graph_data.yaml        ← 拓扑数据（61 节点, 105 边, 3 闸 + 61 门 + 7 红绿灯节点）
└── 包或模块的说明文件/
    └── DjShortCut 结构及使用说明.md  ← 本文档
```

### YAML 节点关键字段

| 字段 | 说明 |
|---|---|
| `doorId: D01~D61` | 每个节点均有门，支持动态状态控制（共 61 个） |
| `has_gate: true` | 该节点有闸，仅 3 个 entrance 节点（gate_south/west/east） |
| `has_traffic_light: true` | 该节点有红绿灯（共 7 个） |

### YAML 边字段说明

| 字段 | 说明 | Dijkstra 用途 |
|---|---|---|
| `edgeId` | 边编号 | 不直接使用 |
| `nodes` | 两端节点 ID 列表 | 构建邻接表 |
| `length` | 几何距离 | 不直接使用 |
| `weight` | 边权（= length / 100） | **Dijkstra 基权值** |
| `capacity` | 通行容量（预留） | 不直接使用 |

## 七、学习属性（尾下划线 `_`）

| 属性 | 类型 | 说明 |
|---|---|---|
| `planner.penalty_factor` | `float` | 门 restricted 的惩罚倍数（构造参数，无下划线） |
| `planner.graph_` | `TrafficNetwork` | fit 后设置的拓扑实例 |
| `planner.n_nodes_` | `int` | 节点总数（61） |
| `planner.node_ids_` | `list[str]` | 所有节点 ID 列表 |

## 八、自测

```bash
python 项目目录/DjShortCut.py
```

### 第一部分：基础最短路径（5 组）

| 起点 | 终点 | 步数 | 总权值 |
|---|---|---|---|
| 南大门 | 东区一饭 | 8 | 11.34 |
| 西门出口 | 东门出口 | 10 | 11.32 |
| 图书馆 | 体育馆 | 5 | 4.77 |
| 西一~四宿舍 | 东一~三宿舍 | 4 | 5.01 |
| 行政楼 | 校医院 | 7 | 9.51 |

### 第二部分：门动态控制（5 组）

| 测试 | 描述 | 预期 |
|---|---|---|
| A | 缓存 `gate_west=closed, gate_east=closed`（封门） | 路径正常，中间无封门节点 |
| B | 实时 `gate_west=open` 覆盖缓存 closed | 覆盖生效 |
| C | `library=restricted` ×10 惩罚 | 限流权值生效 |
| D | 南大门全部邻接节点封门 | 不可达 |
| E | 清除缓存恢复默认 | 路径恢复正常 |

### 第三部分：红绿灯动态控制（4 组）

| 测试 | 描述 | 预期 |
|---|---|---|
| F | 缓存 `cross_zh_mid=red, gate_south=red` | 路径避开中环路口 |
| G | `gate_south=restricted`（门）+ 信号 `red` 双层叠加 | 惩罚连乘生效 |
| H | 实时 `cross_zh_mid=green` 覆盖缓存 red | 路径恢复直接通过中环 |
| I | 清除全部缓存恢复默认 | 路径恢复正常（10 步, 11.32） |

## 九、规范合规

| 规范项 | 遵循情况 |
|---|---|
| PEP 8 命名（类 PascalCase / 函数 snake_case / 变量 snake_case） | ✓ |
| sklearn 风格（fit 返回 self / predict 返回结果） | ✓ |
| `__init__` 只赋值不计算 | ✓ |
| 学习属性尾下划线 `_`（graph_ / n_nodes_ / node_ids_） | ✓ |
| 构造参数无下划线（penalty_factor） | ✓ |
| Docstring 含 Parameters / Attributes / Returns / Raises | ✓ |
| `get_params` / `set_params` 接口 | ✓ |
| 日志 `logging.getLogger(__name__)` + `[时间][级别][模块.方法]` | ✓ |
| UTF-8 / LF / pathlib 路径 | ✓ |
| 边权使用 `weight` 字段（capacity 预留） | ✓ |
| 门状态优先级：实传 > 缓存 > 默认 | ✓ |
| 红绿灯状态优先级：实传 > 缓存 > 默认 | ✓ |
| 红绿灯罚权为内部常量，不暴露给调用方 | ✓ |
| 门 + 红绿灯双层惩罚连乘叠加 | ✓ |
| 闸仅控制车辆入口吞吐，不影响边权 | ✓ |
