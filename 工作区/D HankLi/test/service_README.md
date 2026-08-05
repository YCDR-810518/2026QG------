# service.py — 预测 + 预警封装服务

给成员F用的：CSV 持续追加数据 → 定时预测密度并预警 → 预警 POST 到后端。

## 必须放在同一目录的文件

`service.py` 依赖以下文件，**必须保持在同一目录**：

```
mindspore/
├── service.py            ← 这个文件
├── model.py              ← 模型类定义（DensityPredictor / CongestionDetector）
└── checkpoints/
    ├── density_model/
    │   ├── model_state.ckpt   ← MindSpore 训练好的权重
    │   └── config.json
    └── preprocessor.json      ← 归一化参数
```

> `checkpoints/` 由 `train.py` 训练后自动生成。缺了 model.py 会 import 报错，缺了 checkpoints 会加载失败。
> 运行环境需要 MindSpore（numpy<2）、numpy、pandas、requests。

---

## 用法（F 侧）

```python
from service import SecurityService

svc = SecurityService.from_config(
    csv_path=r"D:\path\to\density_series.csv",   # F 持续写入的 CSV
    backend_base="http://192.168.1.114:8100",    # 后端地址
    demo_mode=True,                               # True=无预警也发演示预警；False=只发真实预警
    interval_seconds=60,                          # 轮询间隔（秒）
)

svc.run_loop()   # 阻塞式循环，Ctrl+C 停止
```

### 只跑一次

```python
result = svc.check_alerts()
# result = {
#   "predictions": {"timestamp":..., "period":..., "density_stats": {node: 密度}},
#   "alerts": [ {...预警JSON...} ],
#   "posted": [ {"event_id":..., "alertId":...} ],
#   "skipped": False,   # True=本轮数据不足，跳过
#   "reason": "",
# }
```

---

## 控制台每轮会看到什么

`run_loop()` 每轮打印类似：

```
[14:32:00] 执行一次预测+预警 ...
  预测时段：2026-08-03 14:33:00 ~ 2026-08-03 14:42:00（未来10分钟）
  预测节点密度峰值 Top3：canteen_1=0.96, gate_south=0.88, cross_zh_mid=0.74
  预警 1 条 | 已提交后端 1 条
    [L2] congestion @ canteen_1 密度=0.96 建议=门闸限流50%
```

- **预测时段**：本次预测覆盖的未来 10 分钟区间
- **Top3 节点**：密度峰值最高的 3 个节点，一眼看到哪些区域最挤
- **预警明细**：每条预警的等级、类型、节点、密度、处置建议
- **提交状态**：成功 POST 后端的条数

---

## F 实时获取预测 + 预警结果的方式

F 不一定要用 `run_loop()` 阻塞循环，有两种方式拿结果：

### 方式一：F 自己控制节奏，每轮调用一次

F 在仿真主循环里，每个 tick 之后调用 `svc.check_alerts()`：

```python
# F 的仿真主循环
for tick in range(n_ticks):
    sim.step()                       # F 生成数据、写 CSV
    result = svc.check_alerts()      # 取预测 + 预警
    if not result["skipped"]:
        density_stats = result["predictions"]["density_stats"]   # 各节点预测密度
        alerts = result["alerts"]                                # 预警列表
        # F 在这里用 density_stats 做门闸调控 / 准入判断
```

这样 F 能**立即拿到每一轮的预测密度和预警**，不用等固定间隔。

### 方式二：F 用回调/返回值

`check_alerts()` 的返回结构固定：
- `result["predictions"]["density_stats"]` → `{node_id: 峰值密度}`，**F 的车辆准入、门闸策略可以直接消费**
- `result["alerts"]` → 预警事件列表，F 可自行展示或转存
- `result["posted"]` → 已提交到后端的记录（含后端生成的 alertId）

> 注意：`check_alerts()` 每轮都重新读 CSV。F 写完 CSV 后调用即可拿到最新数据，**无需重启服务**。

---

## 后端接口变了怎么改

所有后端配置都在 `from_config(...)` 或 `SecurityService(...)` 的构造参数里，后端改动只需改这些参数，**不用改代码**：

### 1. 后端地址变了

```python
svc = SecurityService.from_config(
    csv_path=...,
    backend_base="http://新地址:新端口",   # 只改这里
)
```

### 2. 登录接口路径变了

service.py 内部拼的是 `backend_base + "/api/v1/admin/login"`，`alert_api` 拼的是 `backend_base + "/api/v1/security/alerts/create"`。

如果路径变了，`from_config` 之后手动覆盖：

```python
svc = SecurityService.from_config(...)
svc.login_url = "http://.../api/v1/auth/login"          # 新登录路径
svc.alert_api = "http://.../api/v1/alerts/create"       # 新预警路径
```

### 3. 账号密码变了

```python
svc = SecurityService.from_config(
    ...,
    login_username="新账号",
    login_password="新密码",
)
```

### 4. 预警字段变了

后端如果新增/删减字段，改 `service.py` 里的 `_classify()` 方法（第 304 行附近），它组装 POST 到后端的 JSON 字段。字段名以 B 的接口文档为准。

### 5. 后端返回 `40401 节点不存在`

说明 `node_id` 编码和后端数据库不一致。在 service.py 顶部 `NODE_NAME_MAP` 配置映射：

```python
NODE_NAME_MAP = {"canteen_1": "zone_canteen", "gate_south": "gate_south_01"}
```

---

## 其他说明

- **每次调用重新读 CSV 取最新数据**，F 追加数据后无需重启
- **检测器基线**：启动时用 CSV 历史前 75% 拟合一次，之后不重拟
- **数据不足自动跳过**：CSV 帧数不够时 `skipped=True` 并打印原因，不抛异常、不中断循环
- **测试模式**：`demo_mode=True` 时即使没有真实预警，也会构造一条演示预警发给后端（联调用）
- **实时性**：F 用方式一（每轮调 `check_alerts()`）可以拿到与仿真同步的预测和预警；`run_loop()` 适合独立跑、不介入 F 主循环的场景

---

## 所需依赖库

| 库 | 版本要求 | 用途 |
|---|---|---|
| `mindspore` | ≥2.2（CPU 版） | 模型推理（加载 .ckpt） |
| `numpy` | **必须 <2**（如 1.26.x） | 数据计算（MindSpore 编译依赖 numpy 1.x） |
| `pandas` | 任意 | 读 CSV、时间处理 |
| `requests` | 任意 | 调用后端登录/预警 API |

安装命令：

```bash
pip install mindspore "numpy<2" pandas requests
```

> 注意：`numpy` 必须 <2，否则 MindSpore 运行时会报 `_ARRAY_API not found` / `Numpy init failed`。
> 推荐单独建一个 conda 环境（`conda create -n mindspore python=3.10`）避免和其他项目冲突。

