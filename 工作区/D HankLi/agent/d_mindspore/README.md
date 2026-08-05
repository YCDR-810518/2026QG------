# D成员 — AI模型模块（model.py）

> **项目**：基于MindSpore的园区安全智能调控平台
>
> **负责人**：成员D（MindSpore AI / 预测与预警）
>
> **当前状态**：框架已就绪，等待成员F的训练数据后启动训练

---

## 一、模块概述

`model.py` 是AI模型模块的核心文件，包含两个主要类：

| 类名                   | 功能                               | 对应需求 |
| ---------------------- | ---------------------------------- | -------- |
| `DensityPredictor`   | 人流/车流密度时序预测              | FR-21    |
| `CongestionDetector` | 异常拥堵 / 人员滞留 / 门闸异常检测 | FR-22    |

两个类均遵循 sklearn 风格接口（`fit` / `predict` / `save` / `load`），与项目整体算法规范一致。底层使用 PyTorch（后续可迁移至 MindSpore）。

---

## 二、数据格式

模型接收的数据来自成员F的仿真引擎，经滑动窗口预处理后喂入。

### 2.1 原始字段（成员F生成）

| 字段                | 类型     | 说明                                         |
| ------------------- | -------- | -------------------------------------------- |
| `timestamp`       | datetime | 时间戳，采样间隔 1 分钟                      |
| `node_id`         | int/str  | 园区拓扑节点ID                               |
| `people_density`  | float    | 该节点当前人流密度（人/m²）                 |
| `vehicle_density` | float    | 该节点当前车流密度（辆/m²）                 |
| `vehicle_count`   | int      | 该节点当前车辆数                             |
| `gate_status`     | int      | 门闸状态码（0=关闭, 1=正常, 2=限流, 3=故障） |
| `gate_flow_rate`  | float    | 门闸当前通行速率（人/分钟）                  |
| `level`           | int      | 密度等级                                     |

### 2.2 特征列顺序（model.py 默认）

| 列索引 | 特征                | 用途                                             |
| ------ | ------------------- | ------------------------------------------------ |
| 0      | `people_density`  | 人流密度（密度预测目标 + 拥堵/滞留检测核心特征） |
| 1      | `vehicle_density` | 车流密度（辅助预测特征）                         |
| 2      | `gate_status`     | 门闸状态码（门闸异常检测）                       |
| 3      | `gate_flow_rate`  | 门闸流速（门闸异常检测 + 辅助预测）              |
| 4      | `vehicle_count`   | 车辆数（辅助预测特征）                           |
| 5      | `level`           | 密度等级（辅助预测特征，可用于加权损失）         |

> **注意**：若成员F交付数据时列顺序与此不同，修改 `CongestionDetector` 初始化参数中的 `*_feature_idx` 即可，无需改动代码逻辑。

### 2.3 DensityPredictor 的输入形状

```
X: (n_samples, n_nodes, window_size, n_features)
   n_samples    = 滑动窗口切割后的样本数
   n_nodes      = 园区拓扑节点数
   window_size  = 历史窗口长度（默认 60 分钟）
   n_features   = 特征维度（默认 6）

y: (n_samples, n_nodes, pred_horizon)
   pred_horizon = 预测步长（默认 10 分钟）
```

### 2.4 CongestionDetector 的输入形状

```
fit 阶段:
  X: (n_timesteps, n_nodes, n_features)   历史正常数据

predict 阶段:
  X_current: (n_recent_timesteps, n_nodes, n_features)  多帧近期数据
             或 (n_nodes, n_features)                   单帧数据
  X_pred:    (n_nodes, pred_horizon), optional          DensityPredictor输出
  gate_status: (n_nodes,), optional                     门闸状态码（覆盖X_current中的值）
```

---

## 三、DensityPredictor 使用说明

### 3.1 初始化

```python
from model import DensityPredictor

dp = DensityPredictor(
    window_size=60,      # 历史窗口（分钟）
    pred_horizon=10,     # 预测步长（分钟）
    hidden_dim=64,       # 隐层维度
    num_layers=2,        # TSMixer 层数
    dropout=0.1,         # Dropout
    model_type="tsmixer",  # 或 "gru"（备选）
    # device="cuda:0"    # 可指定设备，默认自动选择
)
```

### 3.2 训练

```python
dp.fit(X_train, y_train,
       epochs=100,
       batch_size=32,
       lr=1e-3,
       val_split=0.15,
       patience=10,
       feature_names=["people_density", "vehicle_density",
                       "gate_status", "gate_flow_rate",
                       "vehicle_count", "level"],
       verbose=True)
```

训练过程自动执行：训练/验证集按时间顺序切分→Adam优化→ReduceLROnPlateau→早停→恢复最佳权重。

可通过 `dp._training_history` 查看训练曲线数据。

### 3.3 预测

```python
y_pred = dp.predict(X_test)
# y_pred.shape == (n_samples, n_nodes, pred_horizon)
# 即每个样本、每个节点、未来10分钟的密度预测值
```

### 3.4 保存与加载

```python
# 保存（产出 model_state.pt + config.json）
dp.save("./checkpoints/density_v1/")

# 加载
dp_loaded = DensityPredictor.load("./checkpoints/density_v1/")
```

---

## 三、train.py 训练脚本

`train.py` 提供完整的训练管道：数据加载 → 节点透视 → 归一化 → 滑动窗口 → 训练 → 评估 → 保存。

### 用法

在 VSCode 中打开 `train.py`，修改顶部的 `DATA_PATH` 为成员F的 7 天数据文件路径，然后点击右上角「运行」按钮即可：

```python
# train.py 顶部配置区，只需改这一个变量
DATA_PATH = r"D:\QG\QG2026暑假训练营\中期考核\工作区\F zdzdzdzdz\data\density_series.csv"
```

其他配置（窗口大小、预测步长、模型类型、训练轮数等）也集中在文件顶部的「配置区」，改完直接运行，不需要命令行参数。

### 特征列约定（对齐成员F的 density_series.csv）

| 列索引 | 字段 | 说明 |
|---|---|---|
| 0 | `density` | 综合密度（预测目标，0~1+） |
| 1 | `people` | 该节点人数 |
| 2 | `vehicles` | 该节点车辆数 |
| 3 | `gate_status` | 门闸状态码（0/1/2） |
| 4 | `gate_flow_rate` | 门闸通行速率 |
| 5 | `level` | 密度等级（low→1, medium→2, high→3, critical→4） |
| 6 | `hour_sin` / `hour_cos` | 一天中的小时（周期性编码） |
| 7 | `dow_sin` / `dow_cos` | 星期几（周一→0 … 周日→6，周期性编码） |

> 时间特征（6~9）由 `timestamp` 自动派生，帮助模型识别"几点/周几"的人流规律（成员F数据按星期几调整人流）。

`density`、`people`、`vehicles`、`gate_flow_rate` 四列做 Min-Max 归一化；`gate_status`、`level` 是类别列，时间特征是 sin/cos（值域 [-1,1]），都不归一化。

### 按整天比例自动切分

数据自动检测共有多少天（≈几周），按 `VAL_RATIO` / `TEST_RATIO` 比例分配训练/验证/测试（按时间顺序），不固定最后几天：

```python
VAL_RATIO = 0.15   # 验证集天数占比
TEST_RATIO = 0.15  # 测试集天数占比
```

例：14 天数据（2 周）→ 训练 10 天、验证 2 天、测试 2 天。更多周数据（3 周、4 周…）无需改代码，自动按比例切分。

### 产出文件

```
checkpoints/
├── density_model/
│   ├── model_state.pt    PyTorch 权重
│   └── config.json       模型超参与配置
├── preprocessor.json     归一化参数（predict.py 阶段加载用）
└── evaluation_report.json 评估报告（含全局+逐节点四项指标）
```

### 评估指标

| 指标 | 公式 | 说明 |
|---|---|---|
| **MAE** | `(1/n) Σ |y - ŷ|` | 平均预测偏差（直观，单位与密度一致） |
| **RMSE** | `√[(1/n) Σ (y - ŷ)²]` | 对大误差更敏感 |
| **MAPE** | `(1/n) Σ |(y - ŷ)/y| × 100%` | 百分比误差（加 ε 平滑防除零） |
| **R²** | `1 - Σ(y-ŷ)² / Σ(y-ȳ)²` | 解释方差比例，越接近1越好 |

评估在**原始密度单位**上进行：训练/预测在归一化空间，逆归一化后才算指标。

---

## 四、CongestionDetector 使用说明

### 4.1 初始化

```python
from model import CongestionDetector

cd = CongestionDetector(
    density_percentile=95.0,     # 拥堵判定分位数
    loitering_duration=5,        # 滞留最小持续帧数
    loitering_rate_threshold=0.01,  # 滞留密度变化率阈值
    sigma_threshold=2.0,         # 拥堵确认 σ 倍数
    gate_flow_sigma=3.0,         # 门闸流速异常 σ 倍数
    density_feature_idx=0,       # 密度列索引
    gate_status_feature_idx=2,   # 门闸状态列索引
    gate_flow_feature_idx=3,     # 门闸流速列索引
)
```

### 4.2 拟合（建立统计基线）

```python
cd.fit(X_history)
# X_history: 历史"正常"数据，不含已知异常时段
# 拟合后可通过 cd.node_stats_ 查看每个节点的统计量
```

### 4.3 异常检测

```python
events = cd.predict(
    X_current,        # 近期数据（多帧或单帧）
    X_pred=y_pred,    # DensityPredictor 的输出（可选，但建议传入）
    gate_status=None  # 使用 X_current 中的状态字段
)

# 返回示例:
# [
#   {"type": "congestion", "node_id": 12, "severity": "L2",
#    "current_density": 3.52, "threshold_p95": 2.10, ...},
#   {"type": "gate_anomaly", "node_id": 5, "severity": "L3",
#    "subtype": "hardware_fault", ...},
# ]
```

三类检测逻辑：

| 类型             | 检测方法            | 触发条件                                           |
| ---------------- | ------------------- | -------------------------------------------------- |
| `congestion`   | 统计阈值 + 预测残差 | 密度超 P95 且超出预测值 2σ，持续 3 帧             |
| `loitering`    | 密度变化率追踪      | 连续 5 帧变化率 < 0.01                             |
| `gate_anomaly` | 规则 + 统计         | 状态码=3（故障）/ 流速=0 但开放 / 流速低于历史 3σ |

### 4.4 保存与加载

```python
# 保存（产出 statistics.json + config.json）
cd.save("./checkpoints/detector_v1/")

# 加载
cd_loaded = CongestionDetector.load("./checkpoints/detector_v1/")
```

---

## 五、目录结构

```
d_mindspore/
├── model.py          ← 模型类定义（DensityPredictor / CongestionDetector）
├── train.py          ← 训练脚本（数据预处理 + 训练主循环 + 评估指标）
├── predict.py        ← 推理脚本（待开发）
└── checkpoints/      ← 模型与检测器存档目录
    ├── density_model/    （DensityPredictor 存档）
    │   ├── model_state.pt
    │   └── config.json
    ├── preprocessor.json （归一化参数）
    └── evaluation_report.json （评估报告）
```

---

## 六、自检方法

在 `d_mindspore/` 目录下运行：

```bash
python model.py
```

会用合成数据跑一遍完整流程（DensityPredictor 训练+预测、CongestionDetector 拟合+检测），输出类似：

```
============================================================
DensityPredictor & CongestionDetector — 自检
============================================================

[1/4] 初始化 DensityPredictor (TSMixer) ...
  设备: cuda
  模型类型: tsmixer

[2/4] 生成合成数据并训练 ...
[3/4] 预测并检查形状 ...
  输入: (3, 5, 12, 5) → 输出: (3, 5, 4) ✓

[4/4] 初始化并测试 CongestionDetector ...
  检测到 2 个异常事件:
    - [L2] congestion @ node=4
    - [L3] gate_anomaly @ node=4

============================================================
自检通过 ✓
============================================================
```

---

## 七、已知问题与后续待办

| 事项             | 说明                                                             | 状态            |
| ---------------- | ---------------------------------------------------------------- | --------------- |
| 真实数据接入     | 等待成员F的`FlowDataGenerator` 交付                            | ⏳ 待数据       |
| train.py 开发    | 数据预处理管道（滑动窗口切分、归一化）、训练主循环、评估指标计算 | ✅ 已完成       |
| predict.py 开发  | 在线推理、预警事件上报至成员B API                                | ⏳ 待开发       |
| MindSpore 迁移   | 用`mindspore.nn` 替换 `torch.nn` 的对应模块                  | ⏳ 后续         |
| TSMixer 效果验证 | 若在真实数据上效果不佳，切换 GRU 备选方案                        | ⏳ 待训练后评估 |
| 特征列顺序对齐   | 与成员F确认数据交付格式，调整`*_feature_idx`                   | ⏳ 待F确认      |

---

## 八、合作成员对接清单

| 对接方 | 时机     | 内容                                         |
| ------ | -------- | -------------------------------------------- |
| 成员F  | 8.3 晚   | 接收`FlowDataGenerator` 训练数据，验证格式 |
| 成员B  | 8.5 下午 | 预警事件 POST 入库 API 联调                  |
| 成员C  | 8.6 上午 | 预测结果传输给 CAV 准入决策模块              |
