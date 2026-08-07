# CavIdmMovement 结构与使用说明（v2.0）

> 模块：`simulation/movement_cav.py`（CAV/IDM 微观移动插件）｜ 负责：成员 C（陈思敏 · 微观/CAV）
>
> 对应需求：FR-11（MAS 智能体）、FR-12（CAV 一致性模拟）、FR-15（CAV 提速量化）
>
> 配套模块：`cav_pack.py`（union_pack 打包采集）、`compare_cav.py`（离线对比）、`micro_fleet.py`（编队实时帧，规划中）
>
> 引擎集成：`engine.py` trip 行程钩子 + `main.py` union_pack 接线（**待 F 合入**，见第六节）
>
> 本文档合并自：《C侧接入代码说明_20260806.md》《CavSimulator契约映射说明_compare_cav替代.md》《F引擎接入改动说明_trip钩子与union_pack打包.md》

---

## 一、模块定位

`CavIdmMovement` 是 F 引擎的**微观移动模型插件**：引擎每 tick 只调用一次 `movement.update_speed(pool)` 更新全体实体速度，本模块在该钩子内实现车辆的 **IDM 跟驰（对照组）/ CAV 编队跟驰（实验组）** 动态速度计算，并兜底行人的固定巡航速度。

```
引擎 TickEngine.step(t) 每 1s
  ├─ movement.update_speed(pool)          ← CavIdmMovement 插入点（本模块）
  │     车辆: IDM/CAV 动态速度（按边分组 + leader 查找）
  │     行人: 固定 1.3 m/s 兜底（否则行人卡死）
  ├─ trip 钩子（engine.py，待 F 合入）：出生/途中/到达 → trip_logs_
  ├─ compare_cav.py：IDM vs CAV 两轮离线对比 → micro_validation_results.json（FR-12/15）
  ├─ cav_pack.py：每 10s union_pack 附带 cav_stats（实时）+ micro_validation_results（离线）
  └─ micro_fleet.py（规划）：每 1s 挑"车多大门→固定终点"4 辆在途车 → B 中转 → E 前端渲染
```

| 模块 | 回答的问题 | 频率 |
|---|---|---|
| `movement_cav.py::CavIdmMovement` | 车辆每 tick 速度怎么算（CAV/IDM） | 每 tick |
| `cav_pack.py::collect_cav_stats` | 当前大门入园车的实时车速/滞留 | 每 10s（随 union_pack） |
| `compare_cav.py` | CAV 相比 IDM 提速多少（FR-15 量化） | 离线一次 |
| `micro_fleet.py`（规划） | 前端 4 车编队动画的逐秒帧 | 每 1s（演示窗口） |

---

## 二、文件结构

```
项目目录/
├── config.yaml                        ← cav:/micro_fleet: 参数统一管理（第七节）
├── graph_data.yaml                    ← 61 节点真实拓扑（坐标 0-100，与前端一致）
├── compare_cav.py                     ← 离线对比入口（C）
├── data/micro_validation_results.json ← compare_cav 产出（C→A 宏微闭环）
├── simulation/
│   ├── movement.py                    ← BaseMovement 基类（F）
│   ├── movement_cav.py                ← 本模块 CavIdmMovement（C）
│   ├── cav_pack.py                    ← union_pack 打包采集（C）
│   ├── micro_fleet.py                 ← 编队实时帧生产（C，✅ 已实现）
│   ├── engine.py                      ← TickEngine（F；trip 钩子 ✅ 已合入）
│   └── topology.py                    ← Topology（61 节点、Dijkstra、base_speed）
└── 包或模块的说明文件/
    └── CavIdmMovement 结构及使用说明.md  ← 本文档（原三份文档已归档）
```

---

## 三、核心接口

### 3.1 构造参数

```python
CavIdmMovement(topology, mode="idm", **kwargs)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `topology` | Topology | 必填 | 引擎拓扑（提供 `edge_length` / `n_nodes`） |
| `mode` | str | `"idm"` | `"idm"` 对照组 / `"cav"` 实验组 |
| `v0` | float | 5.0 | 期望巡航速度 m/s（园区口径，与 `base_speed(1)` 一致） |
| `a_max` | float | 1.5 | 最大加速度 m/s² |
| `b` | float | 2.0 | 舒适减速度 m/s² |
| `s0` | float | 2.0 | 停车最小间距 m |
| `t_head` | float | 1.5 | IDM 跟车时距 s |
| `cth` | float | 0.8 | CAV 恒定车头时距 s |
| `kv` / `kg` | float | 0.6 / 0.4 | CAV 速度差 / 间距差增益 |

> **参数统一管理**：以上默认值与 `config.yaml` 的 `cav:` 段一致（v2.0 起以 config 为准）；
> 引擎侧接线（main.py 从 config 传参）属于待 F 合入改动，见第六节。

### 3.2 `update_speed(pool)` —— 引擎调用钩子

- **调用时机**：`engine.step()` 中 `_ingest` 之后、`_state_machine` 之前（engine.py:512）；
- **只改车辆**：筛选 `active & kind==1 & state==1`（TRAVEL 态车辆），行人由**兜底段**写回 `base_speed(0)`=1.3 m/s（**缺失会导致全园行人卡死**，Step 0 已修复）；
- **按边分组**：`edge_key = cur_node × n_nodes + edge_target` → 同边内按 `edge_pos` 升序排（数值大=靠前）→ 后车 leader = 前一位（`edge_pos` 更大者）→ 计算 IDM/CAV 速度写回 `pool.data["speed"]`；
- **隐含 dt=1s**：速度公式 `v + acc` 无 dt 乘法，与引擎 `dt=1.0` 对齐（tick 频率变更需同步改公式）。

### 3.3 `get_params()` / `set_params(**params)`

sklearn 风格；`get_params()` 返回 `{"mode", v0, a_max, b, s0, t_head, cth, kv, kg}`。

### 3.4 配置管理（config.yaml）

```yaml
cav:
  idm: {v0: 5.0, a_max: 1.5, b: 2.0, s0: 2.0, t_head: 1.5}
  cth: 0.8
  kv: 0.6
  kg: 0.4
  lookahead: 25.0
  delay_speed_threshold: 1.39     # 5 km/h 滞留阈值（cav_stats / trip 同口径）

micro_fleet:
  enabled: false                  # 默认关；仅 8/7 演示窗口打开（避免整日 8 万次 POST）
  interval: 1                     # 每 N tick（1s）发一帧
  fleet_size: 4
  dst_node_id: canteen_1          # 固定 O-D 终点（真实节点；zone_canteen 不存在）
  backend_endpoint: /api/v1/sim/micro-fleet
```

---

## 四、使用示例

### 4.1 脚本注入（离线/测试）

```python
from simulation import CavIdmMovement, TickEngine

eng = TickEngine(topo, gen, movement=CavIdmMovement(topo, mode="cav"))
```

### 4.2 config 切换（集成流水线，main.py:210 已写好分支）

```yaml
# config.yaml
simulation:
  movement_class: CavIdmMovement   # 或 ConstantSpeedMovement（默认）
  movement_mode: cav               # idm / cav
```
```bash
cd 项目目录 && python simulation/main.py run
```

### 4.3 离线对比（compare_cav.py，FR-12/15 量化）

```bash
cd 项目目录
python compare_cav.py --selftest                     # 纯逻辑自测
python compare_cav.py --n-people 2000 --n-vehicles 300 --n-ticks 7200 --n-hours 2
# 产出: data/micro_validation_results.json（meta + per_node + od_stats）
```
**前置**：F 合入 engine.py trip 钩子；未合入时脚本打印阻塞提示并退出（退出码 1）。
**消费方**：A（宏微对齐，协作 4/8）——`od_stats` 的 O-D 对 ↔ `union_pack.vehicle_paths` 静态 `travelTime` 对照，差值 = 宏观未预见的微观时延。

### 4.4 实时打包（cav_pack.py，随 union_pack 附带）

| 函数 | 说明 |
|---|---|
| `collect_cav_stats(eng)` | 实时段：仅统计 `src_node ∈ gate_nodes` 的门入车辆，返回 `{avg_speed_kmh, low_speed_ratio, n_vehicles, n_low_speed}`；空车流全 0 |
| `pack_micro_results(path=None)` | 离线段：读 `micro_validation_results.json` 附带；缺失返回 `{}` |

main.py `_on_package` 接线后，union_pack 增加 2 个 key（示例）：

```json
{
  "cav_stats": {"avg_speed_kmh": 14.2, "low_speed_ratio": 0.31, "n_vehicles": 143, "n_low_speed": 44},
  "micro_validation_results": {"meta": {...}, "per_node": {...}, "od_stats": {...}}
}
```

### 4.5 编队实时帧（micro_fleet.py ✅ 已实现，依赖 F-3 发射通道）

演示窗口启用时，每 1s 从真实引擎挑选"车多的大门 → `canteen_1`"的 4 辆在途车，组装
`{path:{startNodeId,endNodeId,routeNodes}, cavFleet:[4]}` 帧 → POST B 的 `micro-fleet` 端点（只存最新帧）→ E 每秒轮询渲染。

```python
from micro_fleet import collect_micro_fleet, clear_speed_cache
clear_speed_cache()                      # 新会话开始时清加速度缓存
frame = collect_micro_fleet(eng, fleet_size=4, dst_node_id="canteen_1")
# frame = {"tick": 1930, "gate_id": "gate_south",
#          "path": {"start_node_id": "gate_south", "end_node_id": "canteen_1",
#                   "route_nodes": ["gate_south", ..., "canteen_1"]},
#          "fleet": [{"car_id": "CAV_L1", "role": "leader", "position": {"x": 62.8, "y": 10.1},
#                     "speed": 4.5, "acceleration": 0.0, "distance_to_front": 0.0}, ...]}
```

---

## 五、指标口径与字段定义

### 5.1 数据链路（micro_validation_results 的来源）

```
车辆计划 flow_data_generator（vehicles.csv: src/dst/birth_tick）
  → _spawn_batch 落 pool.data（src_node/dst_node 保留，speed=0 出生）
  → topology.path(src,dst,kind=1)（Dijkstra，出生时算好缓存 = "路径规划后固定"）
  → movement.update_speed 每 tick 动态速度（IDM/CAV）
  → trip 钩子：出生记 _birth → 途中 Σ(v·dt)、Σ(信号排队+低速)·dt → 到达落 trip_logs_
  → compare_cav.aggregate_trips → micro_validation_results.json
```

### 5.2 `per_node[节点ID]` 字段表（按 dst_node 归集）

| 字段 | 类型 | 定义 | 单位 |
|---|---|---|---|
| `avg_speed_idm` / `avg_speed_cav` | float | 域内 trip 的 `avg_speed_kmh` 均值 | km/h |
| `efficiency_gain_pct` | float | `(cav−idm)/idm`；idm≤0 时为 0 | 小数 |
| `avg_delay_time` | float | 域内（idm+cav）trip 的 `delay_time` 均值 | 秒 |
| `throughput` / `n_trips` | int | 域内到达车辆数 | 辆 |
| `avg_travel_time_idm` / `_cav` | float | 域内 trip 的 `travel_time` 均值（FR-12 主指标） | 秒 |

单辆车：`avg_speed_kmh = Σ(v·dt) / (finish−birth) × 3.6`（门到门速度）；`delay_time` = 信号排队 + 车速<1.39 m/s 低速（不含计划性出发等待）。归集口径按 dst 近似（引擎无节点穿越记录）。

### 5.3 `od_stats["src|dst"]` 字段表

`trips` / `avg_travel_time_idm|cav` / `avg_speed_idm|cav` —— 完整保留 O-D 维度（"起点终点不缺"），
供 A 与静态 `vehicle_paths.travelTime` 逐对核对（宏微闭环验证点）。

### 5.4 为什么必须用 trip 钩子（而非旁路采样）

`entity_dtype` 无出生时刻字段 → 旁路快照算不出通行时间；旁路瞬时速度均值受在场车龄结构污染；
trip 钩子为逐车全程统计，口径干净。

---

## 六、对 F 的集成改动（状态：6.1/6.2 已合入 ✅；6.3 待 F 确认，纯新增）

### 6.1 engine.py —— trip 行程钩子（4 处，约 20 行）【✅ 已合入 2026-08-06】

| # | 位置 | 内容 |
|---|---|---|
| 1a | `reset()`（约 L176） | 平行数组 `_birth/_spd_sum/_delay_sum` + `trip_logs_` |
| 1b | `_spawn_batch()`（slots 之后） | `_birth[slots] = self._tick`（过闸入园时刻）；累计器清零 |
| 1c | `step()` movement 计时后 | 向量化累计：`_spd_sum[_mov] += v·dt`（仅 TRAVEL）；`_delay_sum` += dt（WAIT_SIGNAL 或 v<1.39） |
| 1d | `_on_reach()` 到达分支 | 落 `trip_logs_`：{src_node, dst_node, birth_tick, finish_tick, travel_time, avg_speed_kmh, delay_time} |

新增模块常量 `_DELAY_SPEED_THRESHOLD = 1.39`。**已实测**：`python compare_cav.py` 端到端产出
`data/micro_validation_results.json`（正式对比结果见第七节）。

### 6.2 main.py —— union_pack 接线（2 key + 1 import，约 4 行）【✅ 已合入 2026-08-06】

```python
from cav_pack import collect_cav_stats, pack_micro_results
union_pack = {..., "cav_stats": collect_cav_stats(eng),
              "micro_validation_results": pack_micro_results()}
```

### 6.3 sender.py / main.py —— 每 tick 微包发射（编队实时帧）【⬜ 待 F 确认，唯一剩余项】

`micro_fleet.py`（C 侧，**已就绪**，见第八节字段表）每 tick 从真实引擎组装一帧
`{tick, gate_id, path{...}, fleet[4]}`。发射通道共 3 处**纯新增**：
① `sender.py::run_and_send` / `csv_recorder.py::run_and_record_send` 加可选 `on_tick(engine, tick)` 回调（~2 行）；
② `main.py::cmd_run` 在 `micro_fleet.enabled=true` 时每 tick 组装帧并 POST（附代码）；
③ `backend_client.py` 加通用 `post_json(endpoint, payload)` 方法。

**完整规格与代码已独立成文**：👉 《F3微包发射通道改动说明_待F合入.md》（本目录，直接转 F 即可）

> 微包仅演示窗口开启（config `micro_fleet.enabled: false` 默认），B 端只覆盖保存最新一帧、不落库。

### 6.4 验证与回退

```bash
cd 项目目录
python -c "from simulation import ...; ..."   # trip_logs_ 条数≈到达数，字段完整
python simulation/main.py verify              # 原有正确性校验不受影响
```
回退 = 删除新增段即可；全部为纯新增，不触碰状态机/信号/闸机/调控/路径/metrics 现有逻辑。

---

## 七、自测

| 命令 | 覆盖 | 状态 |
|---|---|---|
| `python simulation/movement_cav.py` | IDM/CAV 两模式速度计算 | ✅ 通过 |
| `python simulation/cav_pack.py` | 缺失文件容错（返回 {}） | ✅ 通过 |
| `python compare_cav.py --selftest` | 门入过滤 / per_node / od_stats / 指标公式 | ✅ 通过 |
| `python simulation/micro_fleet.py` | 挑车多大门 / 同路径编队 / leader排序 / 坐标插值 / 加速度差分 / 兜底 | ✅ 通过 |
| 端到端（300人/80车/1h/3600tick） | 行人 1.30 m/s 无卡死；cav_stats 采样正常 | ✅ 通过 |
| 真实引擎 micro_fleet 集成（300车/1h） | 12/12 采样点抓到 1~4 辆同路径编队车 | ✅ 通过 |
| 性能对比 | tick_mean_ms 0.51（constant 0.50），无退化 | ✅ 通过 |
| **正式对比（FR-15 结论，2026-08-06）** | 1000人/1000车/1h/7200tick：**通行时间 -3.2%，速度 7.04→7.46 km/h，逐节点最高 +13.7%**（canteen_1 +20.1%）| ✅ 产出 data/micro_validation_results.json |
| 密度敏感性验证 | 同车流密度越高 CAV 优势越明显（0.5h 密集窗口：通行时间 -9.1%）——报告建议注明口径 | ✅ |

> 演示 O-D 提示：`canteen_1` 在常规车流下到达量偏少（正式对比中吞吐=2），演示当天若编队频繁不足 4 辆，
> 可将 `config.yaml micro_fleet.dst_node_id` 切换为高流量节点（如 `east_dorm_8_11` / `west_dorm_5_8`，吞吐 40+）。

---

## 八、与前端文档对齐（《CAV小车编队接口定义文档.md》）

### 8.1 字段映射表（编队实时帧）

| 前端字段 | 数据来源 | 说明 |
|---|---|---|
| `path.startNodeId` | 车 src_node（大门） | 真实 nodeId（如 gate_south） |
| `path.endNodeId` | config `micro_fleet.dst_node_id` | 固定终点（canteen_1） |
| `path.routeNodes` | `pool.paths[slot]` 节点序列 | 出生时 Dijkstra 真实路径 |
| `cavFleet[].carId` | `CAV_L1 / CAV_F1..` | 按沿路里程排序生成 |
| `cavFleet[].role` | leader / follower | 最前=leader |
| `cavFleet[].position{x,y}` | 沿当前边按 edge_pos/edge_length 插值 topology.xy | **0-100 坐标，与前端 topology.json 一致** |
| `cavFleet[].speed` | pool.data["speed"] | 引擎实际速度（CAV/IDM 动态，0~5 m/s） |
| `cavFleet[].acceleration` | 模块内缓存 (v[t]−v[t−1])/dt | 速度差分 |
| `cavFleet[].distanceToFront` | 前后车沿路里程差；leader=0 | — |

> 前端文档示例速度 15.5 m/s 仅为**示意值**：项目统一园区口径 **5.0 m/s**（topology._VEH_SPEED / cav.idm.v0），
> 若嫌动画慢请在前端渲染层做速度倍率，不改动物理。

### 8.2 前后端接口（B 实现）

- `POST /api/v1/sim/micro-fleet`：F 每 tick 上报最新帧，B **只覆盖保存最新一帧**（不落库）；
- `GET /api/v1/vehicle/cav-formation?startNodeId&endNodeId&timeStep`：返回最新帧（snake→camel 转换在 View 层）。

---

## 九、规范合规

| 规范项 | 遵循情况 |
|---|---|
| sklearn 风格（fit/predict/get_params/set_params） | ✓ `CavIdmMovement` 构造+get/set_params；`compare_cav` 输出契约 |
| 构造参数无下划线、学习属性尾下划线 | ✓ |
| 参数统一管理（config.yaml 单一来源） | ✓ `cav:` / `micro_fleet:` 段（v2.0） |
| 与冻结接口/前端文档字段对齐 | ✓ 见第八节字段映射表 |
| 纯新增、不侵入 F 现有逻辑 | ✓ trip 钩子 / union_pack key / on_tick 均为增量 |
| 速度口径统一 5.0 m/s | ✓ 全链路（引擎/跟驰/生成器/报告） |
