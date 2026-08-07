# CAV小车编队演示：字段与接口定义文档

> **文档说明**：本文档定义了中期考核中微观车联网（MAS+CAV）小车编队演示的数据结构。涵盖前端可视化所需字段、前后端 REST 交互接口，以及后端与算法建模人员的 Python 接口规范。

## 一、 可视化核心字段需求分析

针对前端拓扑图上的路径高亮以及4辆小车的直线编队行驶，可视化界面主要依赖以下两组核心数据：

### 1. 宏观路径数据 (Macro Path)
用于在 ECharts 或 3D 拓扑图上高亮最短路径：
* **`startNodeId` (起点ID)**: 路径规划的起始节点。
* **`endNodeId` (终点ID)**: 路径规划的目的地节点。
* **`routeNodes` (途径节点序列)**: 按顺序排列的节点 ID 数组，前端按此序列连线进行高亮渲染。

### 2. 微观车队数据 (Micro CAV Fleet)
用于实时渲染4辆小车的物理状态与编队协同效果：
* **`carId` (车辆唯一标识)**: 如 `CAV_01`, `CAV_02` 等。
* **`position` (当前位置坐标)**: 包含 `x` 和 `y` 的相对或绝对坐标，用于在直线路径上定位。
* **`speed` (当前速度)**: 小车实时速度 (m/s)，可用于仪表盘展示。
* **`acceleration` (当前加速度)**: 实时加速度 (m/s²)，体现 CAV 算法的协同调节能力。
* **`distanceToFront` (距前车车距)**: 毫米或米级单位，领航车该值为 0 或 null。

---

## 二、 前后端交互接口 (REST API)

遵循本项目的通用接口规范（`/api/v1`前缀、小写驼峰命名、Bearer Token鉴权）。

### 接口：获取 CAV 编队路径与实时状态

- **方法/路径**：`GET /api/v1/vehicle/cav-formation`
- **功能**：根据起始和终止节点，返回最优规划路径及该路径上4辆 CAV 小车的初始/实时物理状态。
- **权限**：需登录 (Header: `Authorization: Bearer <token>`)

#### 请求参数 (Query)

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| startNodeId | string | 是 | 拓扑图起点节点 ID |
| endNodeId | string | 是 | 拓扑图终点节点 ID |
| timeStep | number | 否 | 模拟的时间步长或请求的帧序列（可选） |

#### 请求示例

```http
GET /api/v1/vehicle/cav-formation?startNodeId=node_A&endNodeId=node_D
```

#### 响应示例

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "path": {
      "startNodeId": "node_A",
      "endNodeId": "node_D",
      "routeNodes": ["node_A", "node_B", "node_C", "node_D"]
    },
    "cavFleet": [
      {
        "carId": "CAV_L1",
        "role": "leader",
        "position": { "x": 120.0, "y": 45.0 },
        "speed": 15.5,
        "acceleration": 0.2,
        "distanceToFront": 0.0
      },
      {
        "carId": "CAV_F1",
        "role": "follower",
        "position": { "x": 110.0, "y": 45.0 },
        "speed": 15.5,
        "acceleration": 0.1,
        "distanceToFront": 10.0
      },
      {
        "carId": "CAV_F2",
        "role": "follower",
        "position": { "x": 100.0, "y": 45.0 },
        "speed": 15.4,
        "acceleration": 0.3,
        "distanceToFront": 10.0
      },
      {
        "carId": "CAV_F3",
        "role": "follower",
        "position": { "x": 90.0, "y": 45.0 },
        "speed": 15.3,
        "acceleration": -0.1,
        "distanceToFront": 10.0
      }
    ]
  }
}
```

---

## 三、 后端与建模人员交互接口 (Python 算法层)

遵循项目约定，后端调用算法层必须严格遵循 **sklearn 风格** (fit / predict)。所有内部变量均采用 `snake_case` 下划线命名。

后端与建模人员需明确两个核心算法类：`ShortestPathFinder`（宏观最优路径）和 `CavSimulator`（微观编队一致性）。

### 1. 最优路径算法接口 (宏观)

**所在模块**: `algorithms.macro_topo.ShortestPathFinder`

```python
class ShortestPathFinder:
    def __init__(self, weight_type='distance'):
        '''
        初始化寻路器 (参数不加下划线)
        '''
        self.weight_type = weight_type

    def fit(self, graph_data):
        '''
        传入交通网络拓扑数据进行构图
        :param graph_data: 包含节点和边权重的字典或邻接矩阵
        :return: self
        '''
        # 内部构图逻辑...
        self.is_fitted_ = True # 习得属性加下划线
        return self

    def predict(self, src_node, dst_node):
        '''
        执行 Dijkstra 或其他最优算法，返回路径节点序列
        :return: list [src_node, node_x, ..., dst_node]
        '''
        pass
```

### 2. 车联网一致性模拟接口 (微观)

针对 4 辆小车的直线编队协同，计算各个时间步下的跟驰速度、加速度与车距。

**所在模块**: `algorithms.cav_sim.CavSimulator`

```python
import numpy as np

class CavSimulator:
    def __init__(self, fleet_size=4, target_speed=15.0, safe_distance=10.0):
        '''
        初始化编队模拟器
        :param fleet_size: 车队规模（默认4辆）
        :param target_speed: 期望速度
        :param safe_distance: 期望安全车距
        '''
        self.fleet_size = fleet_size
        self.target_speed = target_speed
        self.safe_distance = safe_distance

    def fit(self, initial_state):
        '''
        装载初始车辆状态
        :param initial_state: 初始状态矩阵，shape (fleet_size, n_features) 
                              特征含 [x, y, v, a]
        :return: self
        '''
        # 模型参数初始化...
        self.current_state_ = np.copy(initial_state)
        return self

    def predict(self, horizon=1):
        '''
        预测未来 horizon 个时间步长内的车辆状态，展现一致性收敛过程
        :param horizon: 预测的时间步数
        :return: ndarray, shape (horizon, fleet_size, 4)
                 4 个维度分别为 [position_x, position_y, speed, acceleration]
        '''
        pass
```

### 4. 数据转换说明（后端开发人员关注）
算法模型 `CavSimulator.predict()` 返回的是 `numpy.ndarray`，后端开发人员需要在 View 层将其转换为 JSON 支持的格式，并将 `snake_case` 转换为前端所需的小写驼峰格式（如 `distance_to_front` 转为 `distanceToFront`），然后再通过 API 接口发送给前端。
