# F 引擎接入改动说明：trip 行程钩子 + union_pack 打包接线

> **性质**：本文件是给成员 F 复核的**改动申请说明**，尚未改动任何 F 的代码。
> 涉及文件：`项目目录/simulation/engine.py`、`项目目录/simulation/main.py`
> 提出方：C（陈思敏）｜对接协作：协作 4 / 协作 8（宏微联合调控闭环）
> 日期：2026-08-06

---

## 一、背景与目标

集成引擎当前只产出"节点密度"数据（`metrics.py`），**没有任何 per-vehicle（单辆车）行程数据**，因此无法量化 CAV/IDM 对比效果（FR-12 平均通行时间、平均速度、滞留时长）——而这是 C 负责的微观对比与宏微联合验证（协作 4/8）的必要输入。

本申请共 **2 处改动**，全部为**纯新增、不修改任何现有行为**：

1. `engine.py` 加 **trip 行程钩子**：记录每辆车的 `出生时刻 / 途中累计速度·时长 / 到达时刻`，产出 `eng.trip_logs_`；
2. `main.py` 的 `_on_package` 在 union_pack 中**追加 2 个 key**：`cav_stats`（实时段）+ `micro_validation_results`（离线段附带）。

新增的业务代码（`simulation/cav_pack.py`、`项目目录/compare_cav.py`）由 C 提供并维护，F 只需在 main.py 加一个 import 和两个 key，无需了解实现。

---

## 二、改动总览

| # | 文件 | 位置 | 内容 | 行数估算 |
|---|---|---|---|---|
| 1a | engine.py | `reset()`（约 L176-190） | 新增平行数组 `_birth/_spd_sum/_delay_sum` + `trip_logs_` | +5 |
| 1b | engine.py | `_spawn_batch()`（约 L243 后） | 记录出生 tick 并清零累计器 | +4 |
| 1c | engine.py | `step()` 的 movement 计时之后 | 每 tick 向量化累计速度/滞留 | +7 |
| 1d | engine.py | `_on_reach()` 到达分支（约 L368-376） | 到达时落一条 trip 日志 | +15 |
| 2  | main.py | `_on_package()`（约 L288-322） | import + union_pack 追加 2 key | +4 |

> 全部为纯新增。不触碰：状态机、信号、闸机、调控、路径、metrics 等任何现有逻辑。

---

## 三、改动 1：engine.py trip 行程钩子

### 1a. `reset()` —— 新增平行数组（约 L176-190）

在 `self.metrics = EngineMetrics()` 之前插入：

```python
# ---- trip 钩子：per-vehicle 行程记录（C/协作8 需要，纯新增） ----
self._birth = np.zeros(self.max_capacity, dtype=np.int32)      # 槽位 → 出生(过闸入园) tick
self._spd_sum = np.zeros(self.max_capacity, dtype=np.float64)  # 槽位 → 累计行驶里程 Σ(v·dt) m
self._delay_sum = np.zeros(self.max_capacity, dtype=np.float64)  # 槽位 → 累计滞留 s
self.trip_logs_ = []                                            # 行程日志（到达时追加）
```

> 不修改 `entity_dtype`（避免影响压测/序列化），出生时刻放在引擎侧平行数组。

### 1b. `_spawn_batch()` —— 记录出生 tick（约 L243 后，`slots` 分配之后）

```python
self._birth[slots] = self._tick
self._spd_sum[slots] = 0.0
self._delay_sum[slots] = 0.0
```

> `_spawn_batch` 仅在 `step()` 内被调用（`self._tick` 已设置），语义：**出生时刻 = 过闸/正式入园时刻**（大门排队未放行的车在 `_pending` 中、尚未分配槽位，不计入）。

### 1c. `step()` —— 每 tick 向量化累计（插在 `timer.stop("movement")` 之后、`timer.start("state")` 之前）

```python
# ---- trip 钩子：车辆速度/滞留累计（向量化，开销 ≈ 2 次数组运算/tick） ----
_d = self.pool.data
_veh = _d["active"] & (_d["kind"] == 1)
_mov = _veh & (_d["state"] == STATE_TRAVEL)
self._spd_sum[_mov] += _d["speed"][_mov] * self.dt
_que = _veh & (_d["state"] == STATE_WAIT_SIGNAL)
self._delay_sum[_que] += self.dt
_slow = _mov & (_d["speed"] < _DELAY_SPEED_THRESHOLD)
self._delay_sum[_slow] += self.dt
```

模块顶部新增常量（放在 `SNAPSHOT_CSV_FIELDS` 附近）：

```python
_DELAY_SPEED_THRESHOLD = 1.39   # 5 km/h 以下视为滞留（与 C 的 agents.py 口径一致）
```

**指标语义（请 F 确认口径）**：
- `_spd_sum` 只在 `STATE_TRAVEL` 时累计 → 等于真实行驶里程（m）；红绿灯排队（WAIT_SIGNAL）期间速度不虚增；
- `_delay_sum` = 信号排队时长 + 低速行驶（<5km/h）时长，**不含**出发前等待（WAIT_SRC，因为大门排队未分配槽位、非大门出发等待属计划性等待不视为滞留）。

### 1d. `_on_reach()` —— 到达终点分支落日志（约 L368-376，`data["wait_ticks"][s] = ...` 之后）

```python
# ---- trip 钩子：到达终点，落行程日志（纯新增） ----
if int(data["kind"][s]) == 1:
    self.trip_logs_.append({
        "src_node": self.topology.node_ids[int(data["src_node"][s])],
        "dst_node": self.topology.node_ids[int(data["dst_node"][s])],
        "birth_tick": int(self._birth[s]),
        "finish_tick": int(self._tick),
        "travel_time": int(self._tick) - int(self._birth[s]),
        "avg_speed_kmh": round(self._spd_sum[s] / max(int(self._tick) - int(self._birth[s]), 1) * 3.6, 2),
        "delay_time": round(self._delay_sum[s], 1),
    })
```

> 只记车辆（kind==1）；行人行程不进入 trip_logs_（C 的微观对比仅针对车辆）。
> `avg_speed_kmh = 行驶里程 / (到达时刻 − 出生时刻) × 3.6`，即"门到门"平均速度，含等待/排队时间。

### 性能与内存

- 每 tick 新增 3 次向量化布尔运算（~n_active 规模），相对现有 movement/state 阶段开销可忽略；
- 内存：3 个 `max_capacity=10000` 数组 ≈ 10000×(4+8+8) B ≈ 200 KB；
- `trip_logs_` 条数 = 到达车辆数（最大为车辆投放总量，如 5000 辆/天级），纯内存 list，无 IO。

---

## 四、改动 2：main.py union_pack 打包接线

### 位置与代码

`_on_package()` 内（约 L288），在构造 `union_pack` dict 处追加 2 个 key：

```python
def _on_package(files):
    ...
    from cav_pack import collect_cav_stats, pack_micro_results   # C 提供的新模块
    union_pack = {
        "engine_snapshot": rows,
        "vehicle_paths": eng.vehicle_paths_json(),
        "predict_network": df_net.to_dict(orient="records"),
        "predict_hotspots": df_hot.to_dict(orient="records"),
        "prediction": test_pred,
        "alerts": test_alerts,
        "cav_stats": collect_cav_stats(eng),                      # 新增 1：实时微观统计
        "micro_validation_results": pack_micro_results(),          # 新增 2：离线对比结果附带
    }
```

### 两个新增 key 的说明

**`cav_stats`（实时段，每 interval=10 tick 随包刷新）**
- 只统计**从大门(门闸)入园的车辆**（`src_node ∈ topology.gate_nodes`）；
- 字段：`avg_speed_kmh`（移动中车辆瞬时速度均值）、`low_speed_ratio`、`n_vehicles`、`n_low_speed`；
- 空车流时返回全 0，不会抛异常。

**`micro_validation_results`（离线段，对比实验结论附带）**
- 来源：`compare_cav.py`（C 提供）跑 IDM/CAV 两轮后落盘 `项目目录/data/micro_validation_results.json`；
- `pack_micro_results()` 读取该文件；文件缺失/损坏时返回 `{}`（打包不中断）；
- 结构：`{meta, per_node, od_stats}`，与 8.2 接口文档中 C 交付 A 的 `micro_validation_results` 字段对齐（avg_speed_idm/cav、efficiency_gain_pct、avg_delay_time、throughput + 新增 n_trips、avg_travel_time_idm/cav、od_stats）。

> 实时流水线每 10 tick 无法产出"IDM vs CAV 对比值"（需跑完两轮仿真），因此对比结论以"最近一次离线实验快照"形式随包附带——如需变更此设计请提出。

---

## 五、验证方法（F 可自行执行）

```bash
cd 项目目录
python -c "
from simulation import FlowDataGenerator, HysteresisPolicyController, JointRegulator, TickEngine, Topology, CavIdmMovement
topo = Topology()
gen = FlowDataGenerator(n_people=200, n_vehicles=20, random_state=42, n_days=1); gen.generate()
eng = TickEngine(topo, gen, movement=CavIdmMovement(topo, mode='idm'),
                 gate_policy=HysteresisPolicyController(role='gate'),
                 door_policy=HysteresisPolicyController(role='door'),
                 joint_regulator=JointRegulator())
eng.run(600)
print('trips:', len(eng.trip_logs_))
if eng.trip_logs_:
    print(eng.trip_logs_[0])
"
```

预期：`trips` 条数 ≈ 到达车辆数；首条日志字段完整、`travel_time ≥ 0`、`avg_speed_kmh ∈ (0, 25]`、`delay_time ≥ 0`。运行结束后 **`report()["tick_mean_ms"]` 与改动前差异应 < 1ms**（可用 `python simulation/main.py verify` 复验正确性）。

---

## 六、风险与回退

| 风险 | 说明与应对 |
|---|---|
| 影响现有行为 | 全部为纯新增代码；1a/1b/1c 不触碰任何现有语句，1d 仅在同一 `if` 块末尾追加；回退=删除新增段即可 |
| 性能退化 | 每 tick 仅 3 次向量化运算，理论可忽略；用 `module_mean_ms` 实测确认 |
| 与 F 现有风格冲突 | 所有新增代码遵循 engine.py 现有风格（`_` 私有前缀、`data["..."]` 字段访问、模块常量大写） |
| trip 口径争议 | 语义（出生=过闸、滞留=信号排队+低速、不含计划等待）在 1c 已明确，如需调整请直接提出 |

---

## 七、待 F 确认事项

1. trip 钩子的**指标口径**（第三节 1c/1d 的语义定义）是否认可？
2. `cav_stats` / `micro_validation_results` 两个 key 加入 union_pack 是否认可？
3. 改动由 F 自己合入，还是由 C/组长按本说明实施后请 F 复核？（本说明已到可直接落地的代码级粒度）

> 确认后，`compare_cav.py`（依赖 trip_logs_）即可立即产出 `micro_validation_results.json` 并接入 union_pack。
