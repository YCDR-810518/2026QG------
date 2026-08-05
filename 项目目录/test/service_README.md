# service.py — 预测 + 预警封装服务（PyTorch 版）

给成员F用的：CSV 持续追加数据 → 定时预测密度并预警 → 预警 POST 到后端。

## 必须放在同一目录的文件

`service.py` 依赖以下文件，**必须保持在同一目录**：

```
d_mindspore/
├── service.py            ← 这个文件
├── model.py              ← 模型类定义（DensityPredictor / CongestionDetector）
├── data_config.py        ← 数据格式集中配置（列名 / 字符串编码映射）
└── checkpoints/
    ├── density_model/
    │   ├── model_state.pt   ← PyTorch 训练好的权重
    │   └── config.json
    └── preprocessor.json    ← 归一化参数
```

> `checkpoints/` 由 `train.py` 训练后自动生成。缺了 model.py / data_config.py 会 import 报错，缺了 checkpoints 会加载失败。

---

## 用法（F 侧）

```python
from service import SecurityService

svc = SecurityService.from_config(
    csv_path=r"D:\path\to\engine_snapshot.csv",  # F 持续写入的 CSV
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

## 时间粒度

F 引擎现在是 **10 秒一个采样点**（1 tick = 10 秒）。

- **输入**：最近 `window_size=6` 个时间步 = 60 秒
- **输出**：未来 `pred_horizon=3` 个时间步 = 30 秒

---

## 控制台每轮会看到什么

`run_loop()` 每轮打印类似：

```
[14:32:00] 执行一次预测+预警 ...
  预测时段：2026-08-03 14:33:00 ~ 2026-08-03 14:33:30（未来3个时间步）
  预测节点密度峰值 Top3：canteen_1=0.96, gate_south=0.88, cross_zh_mid=0.74
  预警 1 条 | 已提交后端 1 条
    [L2] congestion @ canteen_1 密度=0.96 建议=门闸限流50%
```

---

## F 实时获取预测 + 预警结果的方式

### 方式一：F 自己控制节奏（推荐）

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

### 方式二：F 读返回结构

- `result["predictions"]["density_stats"]` → `{node_id: 峰值密度}`，车辆准入/门闸策略可直接消费
- `result["alerts"]` → 预警事件列表
- `result["posted"]` → 已提交到后端的记录（含后端生成的 alertId）

> `check_alerts()` 每轮都重新读 CSV，F 写完数据后调用即可拿到最新结果，**无需重启服务**。

---

## 后端接口变了怎么改

所有后端配置都在 `from_config(...)` 的构造参数里，后端改动只需改参数：

### 1. 后端地址变了

```python
svc = SecurityService.from_config(
    csv_path=...,
    backend_base="http://新地址:新端口",
)
```

### 2. 登录 / 预警路径变了

`service.py` 内部拼的是 `backend_base + "/api/v1/admin/login"` 和 `backend_base + "/api/v1/security/alerts/create"`。路径变了就手动覆盖：

```python
svc = SecurityService.from_config(...)
svc.login_url = "http://.../api/v1/auth/login"
svc.alert_api = "http://.../api/v1/alerts/create"
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

改 `service.py` 里的 `_classify()` 方法，它组装 POST 到后端的 JSON 字段。

### 5. 后端返回 `40401 节点不存在`

说明 `node_id` 编码和后端数据库不一致。在 `service.py` 顶部 `NODE_NAME_MAP` 配置映射：

```python
NODE_NAME_MAP = {"canteen_1": "zone_canteen", "gate_south": "gate_south_01"}
```

---

## 所需依赖库

| 库 | 版本要求 | 用途 |
|---|---|---|
| `torch` | 任意（建议 ≥2.0） | 模型推理（加载 .pt 权重） |
| `numpy` | 任意（推荐 <2 或随 torch 环境） | 数据计算 |
| `pandas` | 任意 | 读 CSV、时间处理 |
| `requests` | 任意 | 调用后端登录/预警 API |

安装命令（进入 torch 环境）：

```bash
pip install torch numpy pandas requests
```

> 注意：本文件是 **PyTorch 版**，请用装有 torch 的环境运行（如 `torch-gpu`）。
> MindSpore 版在 `../mindspore/service.py`，两者接口一致，按环境选择即可。

---

## 其他说明

- **每次调用重新读 CSV 取最新数据**，F 追加数据后无需重启
- **检测器基线**：启动时用 CSV 历史前 75% 拟合一次，之后不重拟
- **数据不足自动跳过**：CSV 帧数不够时 `skipped=True` 并打印原因，不抛异常、不中断循环
- **测试模式**：`demo_mode=True` 时即使没有真实预警，也会构造一条演示预警发给后端（联调用）
- **node_id 编码**：若后端返回 `40401`，按上文第 5 点配置映射
