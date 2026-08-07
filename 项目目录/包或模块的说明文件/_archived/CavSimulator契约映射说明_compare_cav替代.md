# CavSimulator 契约映射说明（已归档 → compare_cav.py 取代）

> 日期：2026-08-06
> 决策：**方案 a（归档）**。C 工作区旧版 `algorithms/cav_sim.py`（含其依赖 `algorithms/agents.py`）不再维护，
> 其在 8.2 接口契约中的职责由集成版 `项目目录/compare_cav.py`（+ F 引擎 trip 钩子）完全承接。
> 本文档供 **A（宏观算法）** 按同一契约消费微观结果时对照使用。

---

## 一、为什么归档

| 对比项 | 旧版 CavSimulator（`algorithms/cav_sim.py`） | 集成版 compare_cav.py |
|---|---|---|
| 引擎 | C 自研 `agents.py::TickEngine`（独立仿真本体） | F 引擎 `TickEngine`（项目目录统一仿真） |
| 拓扑 | 兜底 `_MiniTopo` 或硬编码路径适配（`__main__` 引用本机不存在的 `D:\code\...`） | 直接读 `项目目录/graph_data.yaml`（61 节点真实园区拓扑） |
| 车流 | 自行生成车辆计划 | 与集成流水线同一 `FlowDataGenerator`（同一车流两轮对照） |
| 数据出口 | `predict()` 返回 per-node 汇总（Python 内存） | 落盘 `data/micro_validation_results.json` + 随 union_pack 附带 |
| 维护性 | 与 F 引擎存在两套实现漂移风险 | 单套实现，指标口径与引擎一致 |

两套实现输出结构同构（均为 `micro_validation_results`），为避免口径漂移，旧版归档。

---

## 二、契约映射（旧 → 新）

### 1. 类/入口映射

| 8.2 契约中的 C 侧接口 | 旧实现 | 新实现（集成版） |
|---|---|---|
| `CavSimulator.fit(flow_config, topo, vehicles_plan)` | `cav_sim.py::CavSimulator.fit` | 不再需要 fit——`compare_cav.py` 直接由 `FlowDataGenerator` + `Topology` 构造引擎（等价于 fit(flow_config=args, topo=Topology(), vehicles_plan=FlowDataGenerator 生成)） |
| `CavSimulator.predict(horizon)` → `micro_validation_results` | `cav_sim.py::CavSimulator.predict` | `python compare_cav.py --n-ticks <horizon>` → `data/micro_validation_results.json` |
| `CavSimulator.get_params / set_params`（sklearn 风格） | `cav_sim.py` | 命令行参数（`--tick-rate` 未暴露，固定 dt=1s；`cth` 等 CAV 参数在 `CavIdmMovement` 构造参数中） |

### 2. 输出字段映射（8.2 契约 5 字段全部保留，另增 3 项）

| 字段 | 契约来源 | 新旧一致性 |
|---|---|---|
| `avg_speed_idm` / `avg_speed_cav` | FR-12 | ✅ 一致（km/h；新口径 = 门到门平均速度，含排队时间，见《C侧接入代码说明_20260806.md》5.2） |
| `efficiency_gain_pct` | FR-15 | ✅ 一致（`(cav−idm)/idm`） |
| `avg_delay_time` | FR-18 | ✅ 一致（秒；新口径 = 信号排队 + 低速 <5km/h，不含计划性等待） |
| `throughput` | FR-18 | ✅ 一致（到达车辆数） |
| `n_trips` / `avg_travel_time_idm` / `avg_travel_time_cav` | 本次新增 | 🆕 补充 FR-12 通行时间主指标 |
| `od_stats["src\|dst"]` | 本次新增 | 🆕 O-D 对维度，供 A 与 `union_pack.vehicle_paths`（静态 Dijkstra）宏微对照 |

### 3. A 成员的消费方式（新）

```bash
# 由 C/组长执行（依赖 F 合入 trip 钩子）
cd 项目目录
python compare_cav.py --n-people 2000 --n-vehicles 300 --n-ticks 7200 --n-hours 2
# 产出: 项目目录/data/micro_validation_results.json
# 另: main.py run 的 union_pack 会附带 cav_stats(实时) + micro_validation_results(离线)
```

A 侧核对点（协作 4/8 闭环）：
- `od_stats` 中每个 O-D 对 ↔ `union_pack.vehicle_paths` 中同 src/dst 的静态 `travelTime`；
- 差值 = 宏观规划未预见的微观时延；CAV 模式下差值显著缩小即宏微对齐验证通过。

---

## 三、不受本次归档影响的项

- `algorithms/vehicle_access.py`（FR-13 车辆准入）与 `algorithms/dp_noise.py`（差分隐私）：**与 CavSimulator 无关**，仍为待实现空文件（任务 C 后续项）；
- `cav_mas/`、`cav_mas_merge/` 两套演示实验：独立交付物，继续保留在 C 工作区，不参与集成。

---

## 四、归档操作记录

- `工作区/C Simmey/algorithms/agents.py`、`cav_sim.py` → 移入 `工作区/C Simmey/algorithms/_archived/`（见该目录内 README）；
- 项目目录侧无任何 import 依赖它们（已 grep 确认），归档不影响集成流水线。
