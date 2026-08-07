# 归档说明（2026-08-06）

本目录存放 C 侧**已归档**的旧版微观仿真实现，不再维护、不参与集成流水线。

| 文件 | 原职责 | 归档原因 | 取代实现 |
|---|---|---|---|
| `agents.py` | C 自研 MAS 仿真本体（VehicleAgent / GateAgent / TickEngine） | 与 F 引擎存在两套实现漂移风险 | F 引擎 `simulation/engine.py::TickEngine` |
| `cav_sim.py` | `CavSimulator`（sklearn 风格 fit/predict，8.2 契约中的微观对比类） | 输出结构同构但口径不再与集成引擎一致；`__main__` 硬编码本机不存在的 `D:\code\...` 路径 | `项目目录/compare_cav.py`（基于 F 引擎 + trip 钩子） |

**契约承接**：8.2 接口文档中 C→A 的 `micro_validation_results` 契约由 `compare_cav.py` 完全承接，
映射细节见 `项目目录/包或模块的说明文件/CavSimulator契约映射说明_compare_cav替代.md`。

**不受影响项**：`../vehicle_access.py`、`../dp_noise.py`（仍为待实现空文件，属任务 C 后续项）；
`cav_mas/`、`cav_mas_merge/` 演示实验（独立交付物，继续保留）。
