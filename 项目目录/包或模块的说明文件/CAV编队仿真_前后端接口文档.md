# CAV 编队仿真 · 前后端接口文档

> 模块：`项目目录/demo_platoon_edge.py` + `项目目录/animate_fleet.py`（成员 C）
> 用途：在真实园区拓扑上跑 4 车 CAV 一致性编队，产出可消费的帧/遥测数据与自包含动画，供 **B（后端）/ E（前端）** 对接。
> 对齐：字段命名延续仿真侧 snake_case 约定，B 的 View 层转 camelCase（见 §6）。

---

## 1. 概述与数据流

```
A（最短路径）                     C 侧（本仿真）                      B / E
union_pack.json         ──►  demo_platoon_edge.py   ──►  data/platoon_multi.json
vehicle_paths[].path              （节点序列→边映射）        ├─ data/platoon_frames.json
（只含节点顺序）                         │                    └─ data/platoon_telemetry.csv
                                      ▼
                              animate_fleet.py  ──►  platoon_animation.html（自包含，浏览器打开即可）
```

- 仿真侧**不依赖** B 后端 / E 前端的运行时，独立可跑可复现。
- B/E 只需消费 `data/` 下的 JSON/CSV（或 B 封装成 REST 接口给 E）。
- 控制率参考 `test/code/cav.py`（一致性编队：`dV = −(L+K)X_e − (βL+γK)V_e`）。

---

## 2. 涉及文件清单

| 文件                           | 角色                      | 说明                                                             |
| ------------------------------ | ------------------------- | ---------------------------------------------------------------- |
| `demo_platoon_edge.py`       | **新增（C）**       | 编队仿真生成器：跑一致性控制，输出帧/遥测；含内置自检            |
| `animate_fleet.py`           | **新增（C）**       | 动画生成器：读帧/遥测 → 生成`platoon_animation.html`          |
| `platoon_animation.html`     | **新增（C，成品）** | 自包含交互动画（ECharts 已内联，离线可开）                       |
| `data/platoon_multi.json`    | 产出                      | 链式多路径数据包（动画页下拉 1~K 边）                            |
| `data/platoon_frames.json`   | 产出                      | 单路径帧数据（含 meta）                                          |
| `data/platoon_telemetry.csv` | 产出                      | 逐秒遥测（每车每 tick 一行）                                     |
| `graph_data.yaml`            | 引用（只读）              | 园区拓扑：节点 id/x/y、边 edgeId/length/weight（节点→边映射用） |
| `data/json/union_pack.json`  | 引用（A 产出）            | 最短路径输出：`vehicle_paths[].path` 为**节点序列**      |
| `test/code/cav.py`           | 引用（只读）              | 一致性控制率参考实现                                             |

> 未改动 B / E / F 的任何代码；`simulation/` 引擎文件保持原样。

---

## 3. 数据格式

### 3.1 `data/platoon_multi.json`（链式多路径，默认产物）

```jsonc
{
  "meta": {
    "source": "demo_platoon_edge.py",
    "chain": ["cross_zh_south", "cross_zh_mid", "cross_zh_north",
              "teacher_apt", "hospital", "supermarket", "canteen_1"],  // 默认链条（7 节点=6 边）
    "n_edges": 6,
    "fleet_size": 4,
    "v_lead": 2.5,        // 巡航速度 m/s
    "spacing": 30.0       // 期望车-车间距 m
  },
  "paths": {
    "1": {
      "route_nodes": ["cross_zh_south", "cross_zh_mid"],
      "total_path_length": 462.7,
      "segment_lengths": [462.7],
      "edge_ids": ["E10"],                          // 节点→边映射结果
      "converged_tick": 8,                          // 首个"车间距≈spacing 且速度≈v_lead"的 tick
      "x_max": 20,                                  // 底图 x 轴上限 = 收敛点 + ceil(30/v_lead)
      "frames": [ { /* 见 3.2 帧结构 */ } ],
      "telemetry": { "CAV_L1": {"tick":[], "speed":[], "gap":[], "dist":[]}, /* 见 3.4 */ }
    },
    "2": { /* … 前 2 条边，共 688.3m，edge_ids:["E10","E11"] */ },
    "…": { },
    "6": { /* 完整链条 1005.8m */ }
  }
}
```

> `paths` 的键 `"k"` = 使用前 k 条边（leader 沿第 k 段终点停车堆叠）。动画页下拉选择 k 即切换。

### 3.2 帧结构（`frames[]` 元素，单路径与多路径一致）

```jsonc
{
  "tick": 0,
  "gate_id": "cross_zh_south",
  "total_path_length": 688.3,
  "path": {
    "start_node_id": "cross_zh_south",
    "end_node_id": "cross_zh_north",
    "route_nodes": ["cross_zh_south", "cross_zh_mid", "cross_zh_north"]  // 高亮折线用
  },
  "fleet": [
    {
      "car_id": "CAV_L1", "role": "leader",
      "position": {"x": 44.03, "y": 20.95},          // 0-100 画布坐标（与前端 topology.json 一致）
      "speed": 2.5, "acceleration": 0.0,
      "distance_to_front": 0.0,                      // leader 恒 0
      "mileage": 90.0,                               // 已行驶弧长 m
      "distance_to_target": 598.3                    // 到终点剩余 m
    },
    { "car_id": "CAV_F1", "role": "follower", "position": {"x":44.33,"y":18.16},
      "speed": 2.5, "acceleration": 0.0, "distance_to_front": 28.0,
      "mileage": 62.0, "distance_to_target": 626.3 }
  ]
}
```

### 3.3 `data/platoon_frames.json`（单路径）

```jsonc
{
  "meta": { "source": "…", "fleet_size": 4, "total_path_length": 211.9,
            "v_lead": 2.5, "spacing": 30.0, "edge_ids": ["E3"],
            "converged_tick": 8, "x_max": 20 },
  "frames": [ { /* 帧结构同 3.2 */ } ]
}
```

### 3.4 `data/platoon_telemetry.csv`

```
tick,car_id,role,mileage,speed,gap_to_front,front_distance_to_target
0,CAV_L1,leader,90.0,2.5,0.0,121.9
0,CAV_F1,follower,62.0,2.5,28.0,121.9
…
```

- 每车每整秒一行（1 tick = 1 s）。
- `telemetry` 字段与 CSV 对应：`gap = gap_to_front`，`dist = front_distance_to_target`。

---

## 4. 新增字段说明（相对仿真侧原有 micro_fleet 帧）

| 字段                         | 含义                                                                    | 位置                          |
| ---------------------------- | ----------------------------------------------------------------------- | ----------------------------- |
| `mileage`                  | 该车沿路已行驶**弧长** m（leader=起步位 + 已走距离）              | 帧 fleet[]、CSV、telemetry    |
| `distance_to_target`       | 该车到终点**剩余** m                                              | 帧 fleet[]                    |
| `total_path_length`        | 整条路径总长 m                                                          | 帧级、meta                    |
| `edge_ids`                 | 节点→边映射得到的边编号（`["E10","E11",…]`）                        | 单路径 meta / 多路径 paths[k] |
| `converged_tick`           | 首个满足"所有跟随车车间距≈spacing 且速度≈v_lead"的 tick               | meta / paths[k]               |
| `x_max`                    | 底图 x 轴上限 =`converged_tick + ceil(30/v_lead)`（收敛后再显示 30m） | meta / paths[k]               |
| `gap_to_front`             | **车-车间距**：与前车的弧长差（leader 恒 0）                      | CSV、telemetry(`gap`)       |
| `front_distance_to_target` | **前车到目标节点距离**：紧邻前车的剩余里程（leader 用自身）       | CSV、telemetry(`dist`)      |

---

## 5. 调用方式

### 5.1 仿真生成（`demo_platoon_edge.py`）

```
cd 项目目录
python demo_platoon_edge.py                                      # 默认链式（6 条边），产出 platoon_multi.json + 动画
python demo_platoon_edge.py --v-lead 2.5 --spacing 30            # 调巡航速度 / 期望间距
python demo_platoon_edge.py --path "cross_zh_south,cross_zh_mid,cross_zh_north"   # 单路径（节点序列）
python demo_platoon_edge.py --src canteen_1 --dst gate_east      # 单条真实边
python demo_platoon_edge.py --path-json data/json/union_pack.json [--src X --dst Y]  # 直接读 A 的最短路径
python demo_platoon_edge.py --no-html                            # 只出数据不出动画
```

参数表：

| 参数                          | 默认                          | 说明                                          |
| ----------------------------- | ----------------------------- | --------------------------------------------- |
| `--fleet-size`              | 4                             | 车队规模（leader + 跟随车）                   |
| `--v-lead`                  | 2.5                           | 巡航速度 m/s                                  |
| `--spacing`                 | 30.0                          | 期望车-车间距 m                               |
| `--vmax`                    | 6.0                           | 速度上限（须 > v-lead）                       |
| `--k-leader/--beta/--gamma` | 0.6 / 1.2 / 0.8               | 一致性控制增益（调低→收敛变慢）              |
| `--leader-start`            | 60.0                          | leader 起步位（内部自适应 ≥ 间距×跟随车数） |
| `--path`                    | —                            | 节点序列（逗号分隔，相邻须成边）              |
| `--chain`                   | 默认链                        | 链式路径：对 1~K 段各出一套                   |
| `--path-json`               | —                            | 读最短路径 JSON 的`vehicle_paths[].path`    |
| `--src/--dst`               | —                            | 单条边 / path-json 过滤                       |
| `--synthetic`               | —                            | 合成 A→B 迷你拓扑（调试用）                  |
| `--out-dir` / `--html`    | data / platoon_animation.html | 输出路径                                      |
| `--no-html`                 | —                            | 不自动生成动画                                |

### 5.2 动画生成（`animate_fleet.py`）

```
python animate_fleet.py --topology graph_data.yaml \
    --input data/platoon_multi.json --out platoon_animation.html                 # 链式（多路径，页内下拉选边数）
python animate_fleet.py --topology graph_data.yaml \
    --input data/platoon_frames.json --telemetry data/platoon_telemetry.csv --out platoon_animation.html  # 单路径
```

### 5.3 A 侧输入契约（最短路径 → 节点→边映射）

- A 侧最短路径输出为 **`vehicle_paths[].path`：节点 id 序列**（`union_pack.json` 中无边长/边号）。
- 本仿真用 `graph_data.yaml` 的边表（`edgeId`/`nodes`/`length`）将相邻节点对**映射为边**并校验相邻。
- 例：`["gate_south","cross_zh_south","cross_zh_mid"]` → `edge_ids ["E3","E10"]`。

---

## 6. B / E 对接

### 6.1 命名约定（snake_case → camelCase，B 的 View 层负责转换）

| 仿真侧（本文件）                              | B 输出给 E（camelCase）                    |
| --------------------------------------------- | ------------------------------------------ |
| `car_id`                                    | `carId`                                  |
| `role`                                      | `role`                                   |
| `position`                                  | `position {x, y}`                        |
| `speed` / `acceleration`                  | `speed` / `acceleration`               |
| `distance_to_front`                         | `distanceToFront`                        |
| `mileage`                                   | `mileage`                                |
| `distance_to_target`                        | `distanceToTarget`                       |
| `route_nodes`                               | `routeNodes`                             |
| `start_node_id` / `end_node_id`           | `startNodeId` / `endNodeId`            |
| `total_path_length`                         | `totalPathLength`                        |
| `edge_ids` / `converged_tick` / `x_max` | `edgeIds` / `convergedTick` / `xMax` |

### 6.2 E 前端渲染所需

- **主图**：`routeNodes` 画高亮折线（节点坐标取前端 `topology.json`）；`fleet[].position` 为 0-100 画布坐标（与 `topology.json` 一致），直接定位车辆；`carId`/`role` 区分 leader/跟随。
- **leader 标签**：`fleet[0].mileage`（显示"已行驶距离 m"）。
- **三张曲线**：`telemetry{carId:{tick[], speed[], gap[], dist[]}}` → 速度 / 车-车间距 / 前车到目标节点距离；x 轴聚焦到 `xMax`。
- 动画成品已含以上全部逻辑：**`platoon_animation.html` 直接可用**，E 无需重复实现。

### 6.3 B 后端建议端点（B 自行实现，标注为建议）

| 端点                                    | 方法 | 说明                                           |
| --------------------------------------- | ---- | ---------------------------------------------- |
| `GET /api/v1/cav/fleet?k=2`           | GET  | 返回最新/指定 k 的编队帧 + 路径（响应见下）    |
| `GET /api/v1/cav/fleet/telemetry?k=2` | GET  | 返回该 k 的遥测（speed/gap/dist 序列）         |
| `POST /api/v1/cav/fleet`              | POST | 接收仿真侧推送的最新帧（仅存最新一帧，不落库） |

响应示例（`GET /api/v1/cav/fleet?k=2`）：

```jsonc
{
  "code": 0,
  "msg": "success",
  "data": {
    "fleetSize": 4, "vLead": 2.5, "spacing": 30.0, "totalPathLength": 688.3,
    "edgeIds": ["E10", "E11"],
    "convergedTick": 8, "xMax": 20,
    "path": { "startNodeId": "cross_zh_south", "endNodeId": "cross_zh_north",
              "routeNodes": ["cross_zh_south","cross_zh_mid","cross_zh_north"] },
    "fleet": [
      { "carId": "CAV_L1", "role": "leader", "position": {"x":44.03,"y":20.95},
        "speed": 2.5, "acceleration": 0.0, "distanceToFront": 0.0,
        "mileage": 90.0, "distanceToTarget": 598.3 },
      { "carId": "CAV_F1", "role": "follower", "position": {"x":44.33,"y":18.16},
        "speed": 2.5, "acceleration": 0.0, "distanceToFront": 28.0,
        "mileage": 62.0, "distanceToTarget": 626.3 }
    ]
  }
}
```

---

## 7. 语义与口径

- **坐标**：`position{x,y}` 为 **0-100 画布坐标系**（与前端 `topology.json` 节点坐标一致）；车辆位置 = 沿路径弧长在线段间的线性插值（leader 拐过节点后落在下一段，跟随车仍在前一段，弧长即真实道路距离）。
- **单位**：速度 m/s、间距/里程/剩余距离 m、加速度 m/s²；1 tick = 1 s。
- **收敛判定**（`converged_tick`）：所有跟随车 `distance_to_front` 与 `spacing` 偏差 < 0.5 m 且所有车速度与 `v_lead` 偏差 < 0.2 m/s。
- **`x_max`**：`converged_tick + ceil(30 / v_lead)`，仅用于底图 x 轴聚焦收敛段；主图动画完整播放。
- **终点行为**：leader 到终点停车，跟随车收敛堆叠停在其后（leader 瞬时停车导致队首车会略刹车过头，属正常制动现象）。
- **`gap_to_front`**（leader=0）与 **`front_distance_to_target`**（前车到目标节点距离；leader 用自身剩余里程）即为"车-车间距 / 前车到目标节点距离"两个核心展示量。

---

## 8. 自检与复现

```
cd 项目目录
python demo_platoon_edge.py              # 跑默认链式：6 组路径各自自检 + 生成数据 + 动画
python demo_platoon_edge.py --path-json data/json/union_pack.json --no-html   # A 路径单测
python -m py_compile demo_platoon_edge.py animate_fleet.py
```

- 内置自检项：收敛早于到达、稳定保持、跨 ≥2 边帧、leader 遍历全部段、坐标严格在折线上（垂直距离<0.05 画布单位）、反推弧长≈mileage、终点停车堆叠、CSV 行数 = 4×tick。
- 打开 `platoon_animation.html` 验证：下拉切换 1~6 边、4 车沿黄线收拢→巡航→终点堆叠、leader 上方显示已行驶距离、底图三条曲线聚焦收敛段。
