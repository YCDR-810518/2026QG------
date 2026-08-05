# 成员 A 工作进度实时报告

> 维护人：A（姚晨）｜ 更新日期：2026-08-03 ｜ 版本：v2.1
>
> 范围：宏观算法模块（FR-07/08/09）+ 门/闸分离重构规划

---

## 一、今日（8.3）已完成

### 1.1 代码交付

| 模块 | FR | 文件 | 状态 | 说明 |
|---|---|---|---|---|
| `ShortestPathFinder` | FR-07 | `项目目录/DjShortCut.py`（~920 行） | ✅ v1.2 | Dijkstra 最短路径，支持闸机+红绿灯动态权值，双模式（自动感知/临时覆盖） |
| `TrafficNetworkAnalyzer` | FR-08 | `项目目录/macro_topo.py`（~330 行） | ✅ v1.0 | PageRank + 中介中心性 + heatScore/rank，支持动态状态重塑排名 |
| `AttractRankAnalyzer` | FR-09 | `项目目录/macro_attractrank.py`（~240 行） | ✅ v1.0 | Union-Find 空间聚类 + 区域吸引度评分，配置从 YAML 读 |
| `TrafficNetwork` 数据类 | FR-06 | 同上 `DjShortCut.py` | ✅ v1.2 | YAML 加载、邻接表、闸机/红绿灯/门状态缓存、`_yaml_path` 追溯 |

### 1.2 文档交付

| 文档 | 位置 | 版本 |
|---|---|---|
| DjShortCut 结构及使用说明 | `项目目录/包或模块的说明文件/DjShortCut 结构及使用说明.md` | v1.2 |
| TrafficNetworkAnalyzer 结构及使用说明 | `项目目录/包或模块的说明文件/TrafficNetworkAnalyzer 结构及使用说明.md` | v1.0 |
| AttractRankAnalyzer 结构及使用说明 | `项目目录/包或模块的说明文件/AttractRankAnalyzer 结构及使用说明.md` | v1.0 |
| 仿真引擎使用说明 | `项目目录/包或模块的说明文件/仿真引擎使用说明.md` | 已审阅 |

### 1.3 数据同步

| 事项 | 涉及文件 | 状态 |
|---|---|---|
| 字段对齐冻结 JSON | `开发文档/接口文档/后端接口/1-交通网络接口.md` | ✅ `signalId`/`edgeIds` 字段已补全 |
| 字段对齐冻结 JSON | `开发文档/接口文档/后端接口/2-红绿灯接口.md` | ✅ 无差异 |
| 字段对齐冻结 JSON | `开发文档/接口文档/后端接口/3-门闸接口.md` | ✅ 无差异 |
| AAA 对接清单 snake_case 修正 | `工作区/AAA组长 火之晨曦/接口字段相关文件/8.2接口字段对接清单.md` | ✅ 4 处修复 |
| AAA 拓扑文件汇总修正 | `工作区/AAA组长 火之晨曦/接口字段相关文件/交通网络接口与拓扑文件汇总.md` | ✅ 3 处修复 + 红绿灯备注更新 |

### 1.4 自测覆盖

| 模块 | 测试用例 | 结果 |
|---|---|---|
| ShortestPathFinder | 基础 5 组 + 闸机 5 组 + 红绿灯 4 组 | 全部通过 |
| TrafficNetworkAnalyzer | 静态 PR/BC/Heat + 动态重塑 + 单节点/全量 transform | 全部通过 |
| AttractRankAnalyzer | YAML 配置读 + 聚类 + 动态得分 + 复用分析器 | 全部通过 |

---

## 二、A 模块全景

```
项目目录/
├── DjShortCut.py              ← TrafficNetwork + ShortestPathFinder（FR-06/07）
├── macro_topo.py              ← TrafficNetworkAnalyzer（FR-08）
├── macro_attractrank.py       ← AttractRankAnalyzer（FR-09）
├── graph_data.yaml            ← 拓扑数据源（61 节点 + 105 边 + attractrank 配置）
└── 包或模块的说明文件/
    ├── DjShortCut 结构及使用说明.md
    ├── TrafficNetworkAnalyzer 结构及使用说明.md
    └── AttractRankAnalyzer 结构及使用说明.md
```

| 模块 | 回答的问题 | 动态状态感知 |
|---|---|---|
| `ShortestPathFinder.predict(src, dst)` | A→B 最短怎么走 | ✅ gate/signal 动态权值 |
| `TrafficNetworkAnalyzer.fit(graph)` | 哪些节点最重要 | ✅ 封门/红灯重塑排名 |
| `AttractRankAnalyzer.fit(graph)` | 哪些区域是热点 | ✅ 同上 + YAML 配置聚类参数 |

---

## 三、待完成事项

### 3.0 门/闸分离重构（详细设计见下）

### 3.0.1 现状问题

当前 `gate_states` 在 `DjShortCut.TrafficNetwork` 中**同时承担**两个职责：
- **控制边权**：`closed` → 断开邻边（不可通行）；`restricted` → 边权 ×10
- **隐含语义**：门闸是"入口大门的闸"，但实际 14 个 `has_gate: true` 节点中大部分是宿舍楼门禁，语义模糊

这导致：人车混用一个概念，无法区分"限制人流通过某节点"和"限制车辆进入大门"。

### 3.0.2 分离设计

| 维度 | 门 (door) | 闸 (gate) |
|---|---|---|
| **字段** | `doorId`（新增） | `gateId`（已有，保持） |
| **适用范围** | 每个内部节点均可设门 | 仅 entrance 节点（大门） |
| **控制对象** | 人+车的进出 | 仅车辆流量大小 |
| **影响边权** | ✅ closed → 断开, restricted → ×10 | ❌ 不影响 |
| **影响吞吐** | ❌ | ✅ throughput_cap / n_lanes |

#### 功能对比

| 门 (door) | 闸 (gate) |
|---|---|
| 图书馆设门 restricted ×10 → 路径绕行 | 东门设闸 n_lanes=1 / throughput_cap=45 → 车辆限流 |
| 宿舍楼设门 open → 恢复正常 | 西门设闸 mode=close → 车辆不准入 |
| 内部岔路口设门 closed → 封路 | —（内部节点不设闸） |

#### 外部管理接口

```python
# 门：控制边权（新接口）
network.set_door_states({
    "library": "restricted",
    "canteen_1": "open",
})

# 闸：控制车辆流量（沿用现有，语义精简）
network.set_gate_states({
    "gate_south": "closed",    # 车辆不准入
    "gate_east": "restricted",  # 车辆限流
})
```

#### Dijkstra 算法中的行为变化

```
当前：gate_states 同时控制边权
未来：
  ┌ door_states（边权：closed → 跳过, restricted → ×10）
  │ signal_states（边权：red → ×1000, yellow → ×3）
  │ gate_states → 不影响边权，仅控制车辆吞吐
  └ 三层独立，各自不影响
```

### 3.0.3 改动波及

| 文件 | 改动 | 工作量 |
|---|---|---|
| `DjShortCut.py` | TrafficNetwork 新增 `_door_states`, `set_door_states()`, `get_door_states()`；`_dijkstra()` 用 `door_states` 替代 `gate_states` 做边权调整；`gate_states` 保留但仅做吞吐控制 | 中 |
| `graph_data.yaml` | 每个节点增加 `doorId` 字段；原有 `has_gate` 节点同步标注 | 小 |
| `macro_topo.py` | `_build_weight_matrix()`：door_states 替代 gate_states | 小 |
| `macro_attractrank.py` | 同上 | 小 |
| 三份使用说明 | gate_states → door_states 术语更新 | 小 |
| 仿真引擎 `simulation/` | F 侧需要将门闸策略输出分拆为 door 和 gate 两部分 | 待与 F 对齐 |

### 3.0.4 YAML 节点字段变更示例

```yaml
# 当前
gate_south:
  name: 南大门
  type: entrance
  has_traffic_light: true
  has_gate: true

# 未来
gate_south:
  name: 南大门
  type: entrance
  has_traffic_light: true
  has_gate: true       # 有闸（控制车辆进入）
  doorId: D01          # 有门（控制人车进出边权）

library:
  name: 图书馆
  type: academic
  has_traffic_light: false
  has_gate: false
  doorId: D14          # 新增：图书馆也设门
```

### 3.0.5 影响评估

| 方面 | 影响 |
|---|---|
| 向后兼容 | `gate_states` 依然存在但职责收窄；`door_states` 为新增接口 |
| ShortestPathFinder | `predict()` 中 `gate_states` 参数保留但不再影响边权，新增 `door_states` 参数 |
| TrafficNetworkAnalyzer | `fit()` 参数调整同步 |
| F 仿真引擎 | 需分拆输出：门状态 → A 边权控制 / 闸状态 → 车辆吞吐 |
| YAML 更新 | 需新增 `doorId` 字段到每个节点 |

### 3.1 热力图数据适配模块 — HeatmapProvider

#### 3.1.1 背景

前端 E 的大屏需要两类热力图数据：
- **实时热力图**：F 仿真引擎当前时刻的节点密度 → 地图颜色渲染
- **预测热力图**：D 密度预测模型未来 N 分钟（默认 10 分钟）的预测密度 → 未来趋势展示

两类数据来源不同（F 实时 / D 预测），输出格式需统一。

#### 3.1.2 数据源分析

| 来源 | 输出 | 格式 |
|---|---|---|
| **F 仿真引擎** | `engine.step(tick)["nodes"]` 或 `engine.people_density` | `[{node_id, people, vehicles, density(0~1+), level}, ...]` 每秒一帧 |
| **D 密度预测** | `DensityPredictor.predict(X)` | `(n_nodes=61, pred_horizon=10)` — 未来 10 分钟，每节点每分钟一个密度值 |

#### 3.1.3 新增文件

```
项目目录/heatmap.py     ← HeatmapProvider 类
```

#### 3.1.4 接口设计

```python
class HeatmapProvider:
    """热力图数据适配器：合并拓扑坐标 + 实时/预测密度 → 热力图 JSON"""

    def __init__(self, network: TrafficNetwork): ...

    # ── 实时热力图 ──
    def realtime(self, density_dict: dict) -> list:
        """
        density_dict = {node_id: float}  # F 的 people_density
        → [{nodeId, nodeName, x, y, type, density, level, isPredicted}, ...]
        61 条 × 1 帧
        """

    # ── 预测热力图 ──
    def predicted(self, y_pred: np.ndarray, node_ids: list,
                  timestamp: str, mode: str = "all") -> list:
        """
        y_pred: (n_nodes, pred_horizon)  # D 的 predict() 原始输出
        mode: "all" → 全 10 帧; "peak" → 峰值 1 帧; "mean" → 均值 1 帧
        → [{nodeId, nodeName, x, y, type, density, level,
            timestamp, predStep, isPredicted}, ...]
        """
```

#### 3.1.5 数据流

```
        F 仿真引擎                          D 密度预测
       step(tick)["nodes"]             predictor.predict(X)
       {node_id → density} 实时         (61, 10) 未来10分钟
             │                                  │
             └────────────┬─────────────────────┘
                          ▼
              HeatmapProvider(network)
              合并拓扑坐标 + 密度分级
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
       实时热力图 JSON          预测热力图 JSON
       [{nodeId,x,y,density     [{nodeId,x,y,density
         ,level,isPredicted}]     ,level,timestamp,predStep,isPredicted}]
```

#### 3.1.6 输出格式示例

```python
# 实时热力图（当前时刻，1 帧 × 61 节点）
provider.realtime({"gate_south": 0.12, "library": 0.45, ...})
# → [
#   {"nodeId": "gate_south", "nodeName": "南大门", "x": 65, "y": 5,
#    "type": "entrance", "density": 0.12, "level": "low", "isPredicted": false},
#   ... 61 条
# ]

# 预测热力图（未来 10 分钟，默认 mode="all" → 10 帧 × 61 节点 = 610 条）
provider.predicted(y_pred, node_ids, timestamp="06:00:00")
# → [
#   {"nodeId": "gate_south", ..., "density": 0.15, "level": "low",
#    "timestamp": "06:01:00", "predStep": 1, "isPredicted": true},
#   ... 610 条
# ]
```

#### 3.1.7 密度分级逻辑（内置）

```python
def _level(density: float) -> str:
    if density < 0.3: return "low"
    if density < 0.6: return "medium"
    if density < 0.9: return "high"
    return "critical"
```

与 F 的 `metrics.level_of()` 和生成器口径完全一致。

#### 3.1.8 改动波及

| 文件 | 改动 | 工作量 |
|---|---|---|
| `项目目录/heatmap.py` | 新建 HeatmapProvider 类 | 小 |
| `项目目录/包或模块的说明文件/` | 新增使用说明文档 | 小 |
| `开发文档/接口文档/后端接口/实时数据接口.md` | 补全热力图接口定义 | 小 |
| D 的 predict.py | 输出格式适配（已有 `density_stats` dict，可直接对接） | 极小 |
| F 的 engine.py | 无需改动（已有 `people_density` 和 `step()` 输出） | 无 |

#### 3.1.9 与现有代码的关系

| 消费方 | HeatmapProvider 调用方式 | 谁对接 |
|---|---|---|
| 实时热力图 | B 后端 → F `engine.people_density` → A `provider.realtime(dict)` → JSON | B |
| 预测热力图 | B 后端 → D `predictor.predict(X)` → A `provider.predicted(array, ...)` → JSON | B |
| 前端 E | 通过 B 的 REST API 获取 JSON（与现有 `/api/v1/realtime/` 路径对齐） | E |

**HeatmapProvider 只依赖 `numpy` + `DjShortCut.TrafficNetwork`**，不新增外部包。

---

## 四、排期

| 日期 | 事项 | 状态 |
|---|---|---|
| 8.1-8.2 | 文档规划 + 接口冻结准备 | ✅ 完成 |
| **8.3** | **FR-06/07/08/09 全部模块完成 + 三份说明文档 + 字段对齐** | **✅ 完成** |
| 8.4 | 门/闸分离重构 + 热力图数据适配模块 HeatmapProvider | ⬜ 待做 |
| 8.5 | 与 F 联调 A3 协作（宏观数据对接）+ 与 C 联调 A4 协作（宏观路径 → CAV） | ⬜ 待做 |
| 8.6 | 与 A+C+F 联调 A8 协作（宏微联合调控闭环） | ⬜ 待做 |
| 8.7 | 整合测试 + Bug 修复 | ⬜ 待做 |
| 8.8 | PPT + 答辩交付 | ⬜ 待做 |

---

## 五、协作待完成

| 协作编号 | 内容 | 对接方 | 状态 |
|---|---|---|---|
| A3 | 宏观数据对接（F 仿真数据灌入 A 拓扑验证 PageRank/BC/热度） | F | ⬜ 待 8.5 |
| A4 | 宏观路径 → CAV 输入（ShortestPathFinder 路径给 CavSimulator） | C | ⬜ 待 8.5 |
| A8 | 宏微联合调控闭环（A AttractRank 热点 → F 门闸策略 → C 微观验证） | A+C+F | ⬜ 待 8.6 |
| — | 门/闸分离（A 重构 ready → F 侧分拆输出接入） | F | ⬜ 待 8.4 |

---

## 六、风险与备注

- `graph_data.yaml` 中 `attractrank.distance_threshold` 默认 15.0 产生 3 个区域（living_1 35 节点过于集中），后续可调至 ~8.0 以获得更细粒度
- 门/闸分离重构需要与 F 的 `GatePolicyController` 输出的 mode（open/restrict/close）做好映射
- 红绿灯内部罚权（×1000/×3）当前为类常量，无对外暴露——若后续 F 仿真引擎需要对齐红绿灯罚权强度，可考虑统一为 YAML 配置
- 热力图模块需要 D 提供 `predict()` 的原始输出 `(61, 10)` 数组（非聚合后的单值 `density_stats`），确认 D 的 predict.py 接口可提供原始数据
- 预测热力图的 `predStep` 字段（1~10）需要前端支持时间轴滑动或动画播放
