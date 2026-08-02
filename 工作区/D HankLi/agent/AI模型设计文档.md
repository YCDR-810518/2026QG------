# AI模型设计文档

> 负责人：成员D（MindSpore AI / 预测与预警）
>
> 对应需求：FR-21（人流密度预测）、FR-22（异常检测）、FR-23（安全预警与应急响应）、FR-24（ModelArts部署）、FR-25（昇腾集成）
>
> 版本：v0.1（草稿）｜ 日期：2026-08-01

---

## 一、概述

本模块是园区安全智能调控平台的AI核心，承担三个职能：

1. **预测**：基于历史人流数据，预测未来各节点的人流密度变化趋势
2. **检测**：实时识别异常拥堵、人员滞留、门闸状态异常三类风险事件
3. **预警与响应**：对异常事件分级，生成处置建议，通过后端API写入数据库供前端展示

技术选型：全部模型基于 **MindSpore** 框架构建，训练完成后部署至 **华为ModelArts**，推理侧适配 **昇腾（Ascend）** 硬件。对外接口统一遵循 **sklearn 风格**（`fit` / `predict` / `transform`），与项目整体算法规范一致。

---

## 二、数据说明

### 2.1 数据来源

训练数据由成员F的 `FlowDataGenerator` 仿真引擎生成，核心字段如下：

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp` | datetime | 时间戳，采样间隔建议 1 分钟 |
| `node_id` | int/str | 园区拓扑节点ID（十字路口、道路分歧点、上下车热点等） |
| `pedestrian_density` | float | 该节点当前人流密度（人/m²） |
| `vehicle_count` | int | 该节点当前车辆数 |
| `gate_status` | int | 门闸状态码（0=关闭, 1=正常开放, 2=限流, 3=故障） |
| `gate_flow_rate` | float | 门闸当前通行速率（人/分钟） |

### 2.2 数据预处理

- **缺失值处理**：用前向填充（forward fill）补齐短时缺失；连续缺失超过10个采样点则标记为传感器离线，生成告警事件
- **归一化**：密度与流量字段做 Min-Max 归一化（按节点独立归一化以保留节点间差异）
- **序列化**：为时序建模构造滑动窗口样本（窗口长度 W，预测步长 H）

### 2.3 训练/验证/测试划分

按时间顺序切割，避免未来信息泄露：

| 集合 | 比例 | 用途 |
|---|---|---|
| 训练集 | 前70% 时间段 | 模型参数训练 |
| 验证集 | 中间15% 时间段 | 超参调优、早停判断 |
| 测试集 | 末尾15% 时间段 | 最终评估 |

### 2.4 兜底方案

若成员F的生成器延期交付，D先用**手工合成数据**启动开发：使用 NumPy 生成带周期性（早/晚高峰）和随机扰动的人流序列，保证训练管道随时可跑。待F交付正式数据后做格式对齐即可切换。

---

## 三、人流密度预测模型 `DensityPredictor`

### 3.1 任务定义

给定过去 T 个时刻（默认 T=60，即过去60分钟）各节点的人流密度序列，预测未来 H 个时刻（默认 H=10，即未来10分钟）的密度值。

```
输入: X ∈ R^(N × T × F)
      N = 节点数, T = 历史窗口长度, F = 特征维度（密度 + 车辆数 + 时间特征）
输出: Y ∈ R^(N × H)
      H = 预测步长
```

### 3.2 模型选型：TSMixer-Lite（推荐）

#### 选型理由

对几种候选方案做了对比：

| 方案 | 优点 | 缺点 | 是否采纳 |
|---|---|---|---|
| **LSTM** | 时序建模经典方案，MindSpore原生支持好 | 长序列训练慢，梯度问题需调参 | 备选 |
| **GRU** | 比LSTM轻量，参数更少 | 表达能力略弱于LSTM | 不推荐 |
| **Transformer** | 长程依赖建模强，可并行训练 | 数据量小时易过拟合，训练不稳定 | 不推荐 |
| **TSMixer（轻量版）** | 全MLP结构，训练快，无注意力/递归组件，对中等规模时序数据效果好，近期SOTA趋势 | MindSpore社区支持较少，需手写 | ✅ **推荐** |

最终选择 TSMixer-Lite 的理由：

1. 园区人流数据有明显的时间周期性（早晚高峰、午餐时段），MLP的跨时间混合已足够捕获
2. 3000人规模仿真对应节点数有限（预计50-200个），参数量不宜过大，MLP结构天然轻量
3. 训练速度快，迭代周期短，符合本项目紧凑的8天开发窗口
4. GRU/LSTM 作为备选方案保留，若TSMixer效果不达预期可快速切换

#### TSMixer 结构设计

```
TSMixer-Lite 整体结构：

输入层: [Batch, N_nodes, T, F]
   │
   ├─ 时间混合块 (Time Mixing MLP)
   │    Transpose → [Batch, N_nodes, F, T]
   │    Dense(T → T) + GELU + Dropout
   │    Transpose → [Batch, N_nodes, T, F]
   │
   ├─ 特征混合块 (Feature Mixing MLP)
   │    Dense(F → hidden) + GELU + Dropout
   │    Dense(hidden → F)
   │
   └─ 残差连接 (Residual: x + TimeMix(x)  → x + FeatureMix(x))
   
   重复 L 层后 → 投影头 → Y_pred
```

| 超参数 | 推荐值 | 说明 |
|---|---|---|
| 层数 L | 2 | 节点数不大，浅层足矣 |
| 隐藏维度 | 64 | 在容量和速度间平衡 |
| 历史窗口 T | 60 | 过去60分钟 |
| 预测步长 H | 10 | 未来10分钟 |
| Dropout | 0.1 | 轻正则 |
| 激活函数 | GELU | MLP-Mixer标配 |

### 3.3 备选方案：GRU（简化兜底）

若 TSMixer 在 MindSpore 上遇到兼容性问题，退至 GRU：

```python
DensityNet (GRU version):
    nn.GRU(input_size=F, hidden_size=64, num_layers=2, dropout=0.1)
    → 取最后时刻隐状态
    → nn.Dense(64, H)  # 直接输出未来H步
```

### 3.4 辅助特征工程

| 特征 | 编码方式 | 说明 |
|---|---|---|
| 星期几 | One-hot (7维) | 区分工作日/周末模式 |
| 小时 | One-hot (24维) 或 sin/cos 编码 | 捕获早晚高峰 |
| 是否用餐时段 | 二值 (11:30-13:00, 17:30-19:00) | 午餐/晚餐人流激增 |
| 节点类型 | Embedding | 十字路口/分歧点/停车热点 |

### 3.5 训练方案

#### 损失函数

**MSE（均方误差）** 作为主损失：

```
L = (1 / (N × H)) * Σ (y_true - y_pred)²
```

对于高密度节点的预测偏差，考虑加权 MSE（密度越高，预测准确性越关键）：

```
L_weighted = (1 / (N × H)) * Σ w_i * (y_true_i - y_pred_i)²
w_i = 1 + α * y_true_i  （α=1.0，使高密度预测偏差惩罚加倍）
```

#### 优化器与学习率

| 配置项 | 值 |
|---|---|
| 优化器 | Adam |
| 初始学习率 | 1e-3 |
| 学习率策略 | ReduceLROnPlateau（patience=5, factor=0.5） |
| Batch Size | 32 |
| 最大 Epoch | 100（配合早停） |
| 早停 | 验证损失连续10 epoch不降即停止 |

#### 训练流程

```
1. 从 data/ 加载仿真CSV（或手工合成数据）
2. 预处理（归一化 → 滑动窗口 → 划分训练/验证/测试）
3. 创建 MindSpore Dataset 迭代器
4. 初始化 DensityNet + Adam优化器
5. 训练循环：每个epoch遍历训练集 → 计算loss → 反向传播 → 验证集评估
6. 保存最优模型 checkpoint（基于验证损失）
7. 在测试集上做最终评估并记录指标
```

### 3.6 评估指标

| 指标 | 公式 | 用途 |
|---|---|---|
| **MAE** | `(1/n) Σ |y - ŷ|` | 平均预测偏差（直观，单位与人流密度一致） |
| **RMSE** | `√[(1/n) Σ (y - ŷ)²]` | 对大误差更敏感，关注极端预测 |
| **MAPE** | `(1/n) Σ |(y - ŷ)/y| × 100%` | 百分比误差（密度低时可能不稳定，加 ε 平滑） |
| **R²** | `1 - Σ(y-ŷ)² / Σ(y-ȳ)²` | 解释方差比例，越接近1越好 |

**验收标准：**

- MAE < 0.05 人/m²（归一化尺度）
- RMSE < 0.08 人/m²
- 测试集 R² > 0.85

### 3.7 接口设计（sklearn 风格）

```python
class DensityPredictor:
    """
    人流密度预测模型
    
    Parameters
    ----------
    window_size : int, default=60
        历史窗口长度（分钟）
    pred_horizon : int, default=10
        预测步长（分钟）
    hidden_dim : int, default=64
        隐藏层维度
    num_layers : int, default=2
        TSMixer层数
    """
    
    def __init__(self, window_size=60, pred_horizon=10, 
                 hidden_dim=64, num_layers=2):
        ...
    
    def fit(self, X, y, **fit_params):
        """
        训练密度预测模型
        
        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_nodes, window_size, n_features)
            历史特征序列
        y : np.ndarray, shape (n_samples, n_nodes, pred_horizon)
            目标密度值
        **fit_params : dict
            epochs, batch_size, val_split, verbose
        
        Returns
        -------
        self : DensityPredictor
        """
        ...
    
    def predict(self, X):
        """
        预测未来人流密度
        
        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_nodes, window_size, n_features)
        
        Returns
        -------
        y_pred : np.ndarray, shape (n_samples, n_nodes, pred_horizon)
        """
        ...
    
    def save(self, path):
        """保存模型权重与配置"""
        ...
    
    @classmethod
    def load(cls, path):
        """加载已训练模型"""
        ...
```

---

## 四、异常检测模块 `CongestionDetector`

### 4.1 任务定义

输入各节点实时及预测的密度数据、门闸状态，输出三类异常事件。

### 4.2 三类异常检测方案

#### 4.2.1 异常拥堵检测

**方法：统计阈值 + 预测残差**

```
流程：
1. 对每个节点，统计历史密度分布（拟合正态分布或直接取分位数）
2. 实时密度超过历史 P95 分位数 → 标记为"拥堵候选"
3. 结合 DensityPredictor 预测值：实际值超过预测值 2σ → 确认拥堵
4. 输出：拥堵节点ID、当前密度、超出阈值百分比、预计持续时间（取自预测曲线回落至阈值以下的时间点）
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| 历史分位阈值 | P95 | 超过该值即疑似拥堵 |
| 预测残差 σ 倍数 | 2.0 | 超出预测2σ确认真异常 |
| 持续帧数 | 3帧（3分钟） | 连续异常才告警，过滤瞬时抖动 |

#### 4.2.2 滞留人员监测

**方法：节点停留时长分析**

```
流程：
1. 追踪同一节点的人流密度连续性
2. 当某节点密度持续高于基线但流速（density变化率）持续低于阈值 → 人群滞留
3. 对比相邻节点密度变化：若相邻节点密度正常波动但该节点滞涨 → 确认滞留
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| 低流速阈值 | < 0.01 人/m²/分钟 | 密度几乎不变 |
| 持续时间 | > 5分钟 | 长时间无流动 |
| 邻域对比窗口 | 相邻节点 | 确认非全局停滞 |

#### 4.2.3 门闸状态异常检测

**方法：规则 + 统计**

```
流程：
1. 规则检测：gate_status == 3（故障）→ 立即告警
2. 规则检测：gate_flow_rate == 0 但 gate_status != 0 → 疑似传感器故障
3. 统计检测：gate_flow_rate 低于历史同期 3σ → 门闸效率异常（可能半故障）
4. 交叉验证：门闸两侧密度差持续增大但 flow_rate 未变化 → 门闸未正常响应人流压力
```

### 4.3 接口设计（sklearn 风格）

```python
class CongestionDetector:
    """
    异常检测器
    
    Parameters
    ----------
    density_percentile : float, default=95.0
        拥堵判定分位数
    loitering_duration : int, default=5
        滞留判定最小持续帧数
    sigma_threshold : float, default=2.0
        残差异常阈值（σ倍数）
    """
    
    def __init__(self, density_percentile=95.0, loitering_duration=5,
                 sigma_threshold=2.0):
        ...
    
    def fit(self, X, **fit_params):
        """
        拟合异常检测阈值（计算历史统计量）
        
        Parameters
        ----------
        X : np.ndarray, shape (n_timesteps, n_nodes, n_features)
            历史正常数据用于计算基线
        
        Returns
        -------
        self : CongestionDetector
        """
        ...
    
    def predict(self, X_current, X_pred=None, gate_status=None):
        """
        检测异常事件
        
        Parameters
        ----------
        X_current : np.ndarray, shape (n_nodes, n_features)
            当前时刻各节点数据
        X_pred : np.ndarray, shape (n_nodes, pred_horizon), optional
            DensityPredictor的预测输出，用于拥堵验证
        gate_status : np.ndarray, shape (n_gates,), optional
            各门闸状态码
        
        Returns
        -------
        events : list of dict
            [{"type": "congestion", "node_id": ..., "severity": ..., 
              "detail": {...}}, ...]
        """
        ...
```

### 4.4 评估方案

异常检测属于无监督/半监督任务，评估方式：

- **人工标注验证**：从仿真数据中手动注入已知异常（如在特定时刻将密度值翻倍、将门闸状态改为故障），验证检测器召回率和精确率
- **指标**：Precision、Recall、F1-score（以注入的异常为 ground truth）
- **验收标准**：Recall > 0.90, Precision > 0.80（允许少量误报，但尽量不漏报）

---

## 五、安全预警与应急响应模块

### 5.1 预警分级

| 级别 | 名称 | 触发条件 | 颜色 |
|---|---|---|---|
| **L0** | 正常 | 无异常事件 | 绿 |
| **L1** | 关注 | 单个节点密度超 P85 但未达拥堵标准 | 蓝 |
| **L2** | 预警 | 确认拥堵 / 检测到人员滞留 / 门闸速率异常 | 黄 |
| **L3** | 严重 | 多节点同时拥堵（≥3个）/ 门闸故障 / 拥堵持续超过10分钟 | 橙 |
| **L4** | 紧急 | 核心节点（园区入口/主干道）严重拥堵 / 存在安全隐患（如拥挤踩踏风险）| 红 |

### 5.2 处置逻辑

| 级别 | 自动处置 | 建议人工处置 |
|---|---|---|
| L0 | — | — |
| L1 | 提高该节点采样频率 | — |
| L2 | 生成预警事件并入库；调DensityPredictor评估趋势 | 管理员查看前端预警面板 |
| L3 | 推送告警至大屏；通知成员C调整车辆准入策略（提高准入阈值）；通知成员F调整门闸策略（限流） | 调度安保人员至现场 |
| L4 | 推送紧急告警至大屏+飞书通知；触发成员F门闸最大限流；通知成员C拒入所有外来车辆；建议疏散路径（基于成员A最短路径，避开拥堵节点） | 启动应急预案，疏散人群 |

### 5.3 输出格式（入库用）

按成员B的预警接口格式输出：

```json
{
    "event_id": "EVT-20260805-001",
    "timestamp": "2026-08-05T14:32:00",
    "level": "L2",
    "type": "congestion",
    "node_id": "N042",
    "node_name": "食堂东侧十字路口",
    "current_density": 2.35,
    "threshold_density": 1.80,
    "predicted_duration_min": 8,
    "suggested_action": "门闸限流50%",
    "status": "active"
}
```

### 5.4 接口设计

```python
class AlertManager:
    """
    预警与应急响应管理器
    
    Parameters
    ----------
    api_endpoint : str
        成员B的预警入库API地址
    """
    
    def __init__(self, api_endpoint):
        ...
    
    def classify(self, events):
        """
        对CongestionDetector产出的异常事件进行分级
        
        Parameters
        ----------
        events : list of dict
        
        Returns
        -------
        classified : list of dict（附加level字段）
        """
        ...
    
    def respond(self, classified_events):
        """
        根据分级执行处置逻辑（入库、推送到前端、通知其他模块）
        
        Parameters
        ----------
        classified_events : list of dict
        
        Returns
        -------
        response_log : dict
        """
        ...
    
    def suggest_evacuation(self, blocked_nodes, topology):
        """
        拥堵时的疏散路径建议（调用成员A的最短路径，避开拥堵节点）
        
        Parameters
        ----------
        blocked_nodes : list
            拥堵节点列表
        topology : TrafficNetwork
            成员A的交通拓扑网络
        
        Returns
        -------
        routes : list of dict
        """
        ...
```

---

## 六、ModelArts 部署与昇腾集成

### 6.1 部署架构

```
本地开发（MindSpore + GPU/CPU）
    │
    ├─ 训练完成 → 导出 .mindir 模型文件
    │
    └─ ModelArts 线上部署
         ├─ 模型管理：上传 .mindir 至 ModelArts 模型仓库
         ├─ 推理服务：创建在线服务（Ascend 310/910）
         └─ API网关：RESTful 推理接口供后端调用
```

### 6.2 部署步骤

| 步骤 | 操作 | 产出 |
|---|---|---|
| 1 | 本地训练收敛后，`mindspore.export()` 导出 `.mindir` | `density_predictor.mindir` |
| 2 | 上传至OBS桶，在ModelArts中注册模型 | 模型版本记录 |
| 3 | 创建 Ascend 推理在线服务（1个实例，Ascend 310） | 推理 endpoint |
| 4 | 编写轻量推理脚本（`customize_service.py`，处理预处理+推理+后处理） | 推理镜像 |
| 5 | 测试推理接口（curl / Postman 发送请求，验证返回格式） | 联调通过 |
| 6 | 通知成员B获取推理API地址，集成至后端 | 前后端全链路 |

### 6.3 昇腾硬件=CAN小车 + CV 建模

#### 概念说明

本项目将昇腾 Atlas 200 DK 开发板视为"园区内自动驾驶小车"的计算单元，其上部署计算机视觉模型实现本地道路情况建模：

```
昇腾 Atlas 200 DK（小车端）
    ├─ 摄像头 → 实时视频流采集
    ├─ MindSpore Lite 推理 → 目标检测（行人/车辆/障碍物）
    ├─ 本地密度估算 → 上传密度数据至中心平台
    └─ V2X通信 → 接收中心平台下发的预警/路径规划指令
```

#### CV 模型选型

| 模型 | 用途 | 框架 |
|---|---|---|
| YOLOv5s（MindSpore版） | 行人/车辆检测与计数 | MindSpore Lite |
| DeepSORT（简化版） | 行人跨帧跟踪（用于计算流速和滞留） | Python + MindSpore |

#### 实施计划

| 日期 | 里程碑 |
|---|---|
| 8.3-8.4 | 下载 MindSpore 版 YOLOv5s 预训练权重，在本地完成推理验证 |
| 8.5-8.6 | 编写预处理/后处理脚本，实现"视频帧→检测框→密度估算"全链路 |
| 8.7 | 若有硬件则部署至 Atlas 200 DK；若无则编写部署说明文档 + 仿真模拟演示 |

#### 降级方案（无真硬件时）

若昇腾硬件未到位（常见于开发环境限制），执行以下降级：

1. 使用 `mindspore_vision` 在 **CPU/GPU** 上跑 YOLOv5s 推理（MindSpore Lite 的 CPU 后端兼容）
2. 用本地摄像头或预设视频文件替代实时视频流
3. 在文档中保留昇腾推理的完整部署步骤（作为《昇腾集成说明》的核心内容），演示代码逻辑完整可运行

### 6.4 GPU → Ascend 适配注意事项

| 差异点 | GPU | Ascend | 适配 |
|---|---|---|---|
| 混合精度 | AMP (fp16) | Ascend AMP (fp16) | 统一使用 MindSpore AMP 接口 |
| 数据格式 | NCHW | NCHW（一致） | 无需适配 |
| 算子兼容性 | 全算子 | 部分算子需替换 | 使用 `mindspore.ops` 标准算子 |
| 推理后端 | MindSpore GPU | MindSpore Lite Ascend | 导出 `.mindir` 时指定 Ascend 后端 |

---

## 七、模块目录结构

```
d_mindspore/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── density_net.py          # DensityNet 模型定义（TSMixer-Lite / GRU）
│   └── cv_models.py            # YOLOv5s 封装（昇腾CV部分）
├── predictors/
│   ├── __init__.py
│   └── density_predictor.py    # DensityPredictor（fit/predict/save/load）
├── detectors/
│   ├── __init__.py
│   └── congestion_detector.py  # CongestionDetector（fit/predict）
├── alert/
│   ├── __init__.py
│   └── alert_manager.py        # AlertManager（classify/respond）
├── deployment/
│   ├── export_mindir.py        # MindIR导出脚本
│   ├── customize_service.py    # ModelArts自定义推理服务
│   └── ascend_deploy.md        # 昇腾部署说明
├── utils/
│   ├── __init__.py
│   ├── data_loader.py          # 数据加载与预处理（滑动窗口等）
│   └── metrics.py              # MAE/RMSE/MAPE/R² 评估函数
└── tests/
    ├── test_density_predictor.py
    ├── test_congestion_detector.py
    └── test_alert_manager.py
```

---

## 八、风险评估与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| MindSpore TSMixer 实现调试耗时过长 | 中 | 中 | 备选 GRU 方案可 1 天内完成替换 |
| 成员F数据未按时交付 | 低 | 中 | 用手工合成数据启动训练，格式对齐后切换（已在2.4节兜底） |
| 昇腾硬件未到位 | 高 | 低 | 使用 CPU/GPU 跑通全链路，文档保留昇腾部署步骤（已在6.3节降级方案） |
| ModelArts Ascend 算子兼容性 | 中 | 中 | 提前用 `mindspore.ops` 标准算子，避免自定义算子；本地先做算子兼容性检查 |
| 训练过拟合（仿真数据变异性低） | 中 | 中 | 加大 Dropout + 早停；F 生成数据时加入噪声扰动 |

---

## 九、开发排期（成员D）

| 日期 | 任务 | 交付 |
|---|---|---|
| **8.1** | 搭建项目骨架（目录+接口定义），手工合成训练数据 | 脚手架可跑 |
| **8.2** | 数据加载与预处理管道；DensityNet 模型编码（TSMixer） | 训练管道就绪 |
| **8.3** | 接收F数据（或用手工数据），开始 DensityPredictor 训练 | 模型初版收敛 |
| **8.4** | CongestionDetector 编码+测试；DensityPredictor 调优评估 | 两模块编码完成 |
| **8.5** | AlertManager 预警分级+处置逻辑；与B联调预警入库API | 预警链路打通 |
| **8.6** | ModelArts部署+测试；与C联调预测驱动调度；CV模块验证 | 部署完成 |
| **8.7** | 昇腾集成文档+演示；全链路 Bug 修复；PPT 2-3页 | 文档+PPT完成 |
| **8.8 上午** | 配合全员最终测试与打包 | 最终交付 |

---

*文档草稿版本 v0.1，待与团队成员讨论后定稿。*
