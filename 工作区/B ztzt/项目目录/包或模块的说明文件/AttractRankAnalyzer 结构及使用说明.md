# AttractRankAnalyzer 结构与使用说明（v1.0）

> 模块：macro_attractrank.py ｜ 负责：成员 A（宏观算法）
>
> 对应需求：FR-09（AttractRank 热点区域分析）
>
> 对外接口：sklearn 风格（fit / transform / get_params / set_params）

## 一、模块定位

`AttractRankAnalyzer` 将 61 个离散拓扑节点**聚合为有意义的区域**（如食堂区、运动区、教学区），并计算每个区域的吸引度得分。

```
TrafficNetworkAnalyzer（每个节点的 heatScore）
         │
         ▼
AttractRankAnalyzer（Union-Find 空间聚类 + 区域评分）
         │
         ▼
[{region: "living_1", nodeIds: [...], attractScore: 88.0}, ...]
```

| 模块 | 回答的问题 |
|---|---|
| `ShortestPathFinder` | A→B 最短怎么走 |
| `TrafficNetworkAnalyzer` | 哪个节点最重要 |
| `AttractRankAnalyzer` | 哪些区域是热点 |

## 二、文件结构

```
项目目录/
├── macro_attractrank.py       ← 本模块
├── macro_topo.py              ← TrafficNetworkAnalyzer（依赖）
├── simulation/topology.py     ← TrafficNetwork（依赖，原 DjShortCut.py 已并入）
├── graph_data.yaml            ← 拓扑 + attractrank 配置
└── 包或模块的说明文件/
    └── AttractRankAnalyzer 结构及使用说明.md  ← 本文档
```

## 三、核心接口

### 3.1 构造参数

```python
AttractRankAnalyzer(alpha=0.5)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `alpha` | float | 0.5 | 传给内部 TrafficNetworkAnalyzer 的 heatScore 权值 |

`distance_threshold` 和 `min_nodes` 从 `graph_data.yaml` 的 `attractrank` 块读取，不作为构造参数：

```yaml
# graph_data.yaml
attractrank:
  distance_threshold: 15.0   # 空间聚类距离（坐标单位）
  min_nodes: 2               # 最少节点数
```

### 3.2 fit(graph, analyzer=None, door_states=None, signal_states=None) → self

| 参数 | 说明 |
|---|---|
| `graph` | TrafficNetwork 拓扑实例 |
| `analyzer` | 可选，已 fit 的 TrafficNetworkAnalyzer（复用省时） |
| `door_states` | 临时门状态覆盖 |
| `signal_states` | 临时红绿灯覆盖 |

**自动感知模式**：F 更新 `network._door_states` → A 直接 `fit(network)` 无参 → 自动读缓存。

### 3.3 transform() → list

```python
analyzer.transform()
# → [
#   {"region": "living_1",  "nodeIds": ["canteen_1", "gate_south", ...], "attractScore": 100.0},
#   {"region": "lab_1",     "nodeIds": ["eng_1", "exp_1", ...],       "attractScore": 39.1},
#   {"region": "academic_1","nodeIds": ["library", "teach_1", ...],   "attractScore": 34.0},
# ]
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `region` | str | 区域标识（类型名_序号） |
| `nodeIds` | list[str] | 该区域包含的节点 ID |
| `attractScore` | float | 吸引度得分（0~100） |

### 3.4 学习属性（尾下划线 `_`）

| 属性 | 类型 | 说明 |
|---|---|---|
| `regions_` | list[dict] | 全部热点区域 |
| `n_regions_` | int | 区域总数 |
| `graph_` | TrafficNetwork | 拓扑实例 |
| `analyzer_` | TrafficNetworkAnalyzer | 内部分析器 |
| `distance_threshold_` | float | 从 YAML 读取的聚类距离 |
| `min_nodes_` | int | 从 YAML 读取的最少节点数 |

### 3.5 get_params / set_params

```python
analyzer.get_params()
# → {"alpha": 0.5}
```

## 四、使用示例

### 4.1 基础用法

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "simulation"))
from topology import TrafficNetwork
from macro_attractrank import AttractRankAnalyzer

network = TrafficNetwork.from_yaml("graph_data.yaml")
analyzer = AttractRankAnalyzer(alpha=0.5).fit(network)

for r in analyzer.transform():
    print(f"{r['region']:15s}  nodes={len(r['nodeIds']):2d}  score={r['attractScore']:5.1f}")
```

### 4.2 复用已有 TrafficNetworkAnalyzer

```python
from macro_topo import TrafficNetworkAnalyzer

pre = TrafficNetworkAnalyzer(alpha=0.5).fit(network)
# ... 其他代码也可使用 pre 的 heatScore

analyzer = AttractRankAnalyzer(alpha=0.5).fit(network, analyzer=pre)
```

### 4.3 动态状态（自动感知）

```python
network.set_door_states({"gate_west": "closed"})
network.set_signal_states({"cross_zh_mid": {"phase": "red"}})

analyzer = AttractRankAnalyzer().fit(network)  # 无参 → 自动读缓存
for r in analyzer.transform():
    print(r["region"], r["attractScore"])
# → lab_1 得分下降（绕路降低热度）
```

## 五、算法细节

### Union-Find 空间聚类

```
1. 每个节点初始为独立簇
2. 遍历每条边 (src, dst):
   - 若两节点坐标距离 ≤ distance_threshold → 合并
3. 遍历同类型节点对:
   - 若距离 ≤ distance_threshold / 2 → 合并
4. 过滤成员数 < min_nodes 的孤立簇
5. 按规模降序排列
```

### 吸引度评分

```
attractScore = (Σ heatScore_i) / max(Σ heatScore) × 100
```

### 区域命名

取区域内节点类型的**众数** + 该类型的**出现序号**，如 `living_1`、`lab_1`。

## 六、自测

```bash
python 项目目录/macro_attractrank.py
```

测试覆盖：
- YAML 配置读取
- 空间聚类 + 区域评分
- 复用 TrafficNetworkAnalyzer
- 动态状态（封门+红灯）→ 得分重塑
- get_params

## 七、与冻结 JSON 对齐

```json
{"region": "canteen_area", "nodeIds": ["zone_canteen", "cross_4"], "attractScore": 88.0}
```

| JSON 字段 | transform() 返回 | 备注 |
|---|---|---|
| `region` | `region` | 区域标识 |
| `nodeIds` | `nodeIds` | 成员节点列表 |
| `attractScore` | `attractScore` | 0~100 |

## 八、规范合规

| 规范项 | 遵循情况 |
|---|---|
| sklearn 风格（fit 返回 self） | ✓ |
| `__init__` 只赋值不计算 | ✓ |
| 学习属性尾下划线 | ✓ `regions_` `n_regions_` |
| 构造参数无下划线 | ✓ `alpha` |
| `get_params` / `set_params` | ✓ |
| 空间参数从 YAML 读 | ✓ |
| 与冻结 JSON 字段对齐 | ✓ |
