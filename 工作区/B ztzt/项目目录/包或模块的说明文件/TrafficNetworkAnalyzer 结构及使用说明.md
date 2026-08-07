# TrafficNetworkAnalyzer 结构与使用说明（v1.0）

> 模块：macro_topo.py ｜ 负责：成员 A（宏观算法）
>
> 对应需求：FR-08（PageRank / 中介中心性）
>
> 对外接口：sklearn 风格（fit / transform / get_params / set_params）

## 一、模块定位

`TrafficNetworkAnalyzer` 基于 `TrafficNetwork` 拓扑计算**节点重要性排名**，输出 PageRank 和中介中心性，支持门/红绿灯动态状态影响排名重塑。

| 模块 | 回答的问题 |
|---|---|
| `ShortestPathFinder` (simulation/topology) | A→B **最短怎么走**（路径级） |
| `TrafficNetworkAnalyzer` (macro_topo) | 此刻**哪个节点最重要**（节点级） |

## 二、文件结构

```
项目目录/
├── macro_topo.py              ← 本模块
│   └── TrafficNetworkAnalyzer
├── simulation/topology.py     ← 依赖（TrafficNetwork，原 DjShortCut.py 已并入）
├── graph_data.yaml            ← 拓扑数据
└── 包或模块的说明文件/
    └── TrafficNetworkAnalyzer 结构及使用说明.md  ← 本文档
```

## 三、核心接口

### 3.1 构造参数

```python
TrafficNetworkAnalyzer(damping_factor=0.85, max_iter=100, tol=1e-6, alpha=0.5)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `damping_factor` | float | 0.85 | PageRank 阻尼系数 |
| `max_iter` | int | 100 | PageRank 最大迭代次数 |
| `tol` | float | 1e-6 | PageRank 收敛阈值 |
| `alpha` | float | 0.5 | heatScore 中 PageRank 权重占比。heatScore = (α×PR_norm + (1-α)×BC_norm)×100 |

### 3.2 fit(graph, door_states=None, signal_states=None) → self

加载拓扑并计算节点重要性指标。

| 参数 | 类型 | 说明 |
|---|---|---|
| `graph` | TrafficNetwork | 拓扑实例 |
| `door_states` | dict, optional | `{node_id: "open"\|"closed"\|"restricted"}` |
| `signal_states` | dict, optional | `{node_id: {"phase": "green"\|"yellow"\|"red"\|"off", ...}}` |

- **不传** gate/signal → 纯静态拓扑分析
- **传入** → 边权被动态修正后重算（closed→断开，restricted→×10，red→×1000，yellow→×3）

### 3.3 transform(node=None) → dict

| 调用 | 返回 |
|---|---|
| `transform()` | `{node_id: {pagerank, betweenness, heatScore, rank}, ...}` 全量 |
| `transform("library")` | `{nodeId, pagerank, betweenness, heatScore, rank}` 单节点 |

**单节点返回字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `nodeId` | str | 节点编码 |
| `pagerank` | float | PageRank 得分（0~1） |
| `betweenness` | float | 中介中心性（归一化 0~1） |
| `heatScore` | float | 综合热度分（0~100） |
| `rank` | int | 热度排名（1 = 最热） |

### 3.4 学习属性（尾下划线 `_`）

| 属性 | 类型 | 说明 |
|---|---|---|
| `pagerank_` | `{node_id: float}` | 全节点 PageRank |
| `betweenness_` | `{node_id: float}` | 全节点中介中心性 |
| `heat_scores_` | `{node_id: float}` | 全节点综合热度（0~100） |
| `ranks_` | `{node_id: int}` | 全节点排名 |
| `n_nodes_` | int | 节点总数 |
| `node_ids_` | list[str] | 节点 ID 列表 |
| `graph_` | TrafficNetwork | 拓扑实例 |

### 3.5 get_params / set_params

```python
analyzer.get_params()
# → {"damping_factor": 0.85, "max_iter": 100, "tol": 1e-06, "alpha": 0.5}

analyzer.set_params(alpha=0.7).fit(network)
```

## 四、使用示例

### 4.1 静态分析

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "simulation"))
from topology import TrafficNetwork
from macro_topo import TrafficNetworkAnalyzer

network = TrafficNetwork.from_yaml("graph_data.yaml")
analyzer = TrafficNetworkAnalyzer(alpha=0.5).fit(network)

# Top 5 最重要节点
top5 = sorted(analyzer.pagerank_.items(), key=lambda x: -x[1])[:5]
# → [("cross_zh_south", 0.0334), ("cross_zh_mid", 0.0324), ...]

# 单节点查询
result = analyzer.transform("library")
# → {"nodeId": "library", "pagerank": 0.027, "betweenness": 0.41,
#    "heatScore": 66.7, "rank": 3}

# 全量返回（给前端/后端用）
all_nodes = analyzer.transform()
# → {"gate_south": {...}, "gate_west": {...}, ...}
```

### 4.2 动态分析（门 + 红绿灯）

**两种工作模式**：

| 模式 | 调用方式 | 门/信号来源 | 适用场景 |
|---|---|---|---|
| **自动感知**（推荐） | `fit(network)` 无参 | `network._door_states` / `network._signal_states` 缓存 | F 仿真引擎持续更新状态，A 随时 `fit` 拿最新排名 |
| **临时覆盖** | `fit(network, door_states=..., signal_states=...)` | 参数直接传入，优先于缓存 | What-if 分析（"假如西门关了会怎样"） |

**自动感知示例**（F 改动 → A 自动感知，零参数）：

```python
network = TrafficNetwork.from_yaml("graph_data.yaml")
analyzer = TrafficNetworkAnalyzer(alpha=0.5).fit(network)
# → 当前排名：underpass rank=1, library rank=3

# F 仿真引擎改了门/红绿灯
network.set_door_states({"gate_west": "closed"})
network.set_signal_states({"cross_zh_mid": {"phase": "red"}})

# A 直接重新 fit，不传任何参数 → 自动读取缓存中的最新状态
analyzer.fit(network)
# → 排名立刻重塑：library rank=3 → rank=8
```

**临时覆盖示例**（一次性的 What-if）：

```python
analyzer.fit(network,
    door_states={"gate_west": "closed"},
    signal_states={"cross_zh_mid": {"phase": "red"}},
)
# → 仅本次生效，不修改 network 缓存

## 五、算法细节

### PageRank（加权无向图）

1. 构建邻接权值矩阵（含门/红绿灯修正）
2. 转移矩阵: `P[i][j] = w[i][j] / Σw[i]`
3. 迭代: `r = (1-d)/N + d × Pᵀ × r`
4. 收敛: `max|Δr| < tol` 或达到 `max_iter`

### 中介中心性（Brandes 算法，加权）

1. 对每个节点 s 作为源，运行加权 Dijkstra
2. 记录 `σ[v]`（s→v 最短路径数）和 `predecessors`
3. 逆序回溯: `δ[v] = Σ (σ[v]/σ[w]) × (1 + δ[w])`
4. 累加 `betweenness[w] += δ[w]`（w ≠ s）
5. 归一化: `÷ (n-1)(n-2)/2`

### 综合热度分

```
heatScore = (α × PR_norm + (1-α) × BC_norm) × 100
```

## 六、自测

```bash
python 项目目录/macro_topo.py
```

测试覆盖：
- 静态 PageRank / Betweenness / HeatScore 计算
- 单节点 + 全量 transform 查询
- 动态分析（封西门 + 中环红灯）→ 排名重塑验证
- 静态 Top5 在动态分析中的排名变化对比

## 七、规范合规

| 规范项 | 遵循情况 |
|---|---|
| sklearn 风格（fit 返回 self） | ✓ |
| `__init__` 只赋值不计算 | ✓ |
| 学习属性尾下划线 | ✓ `pagerank_` `betweenness_` |
| 构造参数无下划线 | ✓ `damping_factor` `max_iter` `tol` `alpha` |
| `get_params` / `set_params` | ✓ |
| Docstring 含 Parameters / Attributes / Returns | ✓ |
| 日志 `logging.getLogger(__name__)` | ✓ |
| 红绿灯罚权为内部常量 | ✓ |
