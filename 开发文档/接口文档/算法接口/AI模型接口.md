# AI 模型接口

> 对应需求：FR-21（人流密度预测）、FR-22（异常检测：异常拥堵、滞留人员、门闸状态异常）
>
> 说明：本文件涉及的类为神经网络模型，**对外接口 sklearn 风格，内部实现使用 MindSpore**（详见《变量及接口命名规范.md》附录 C）。

## DensityPredictor

- **所在模块**：待补，如 `models/density.py`
- **对外接口**：sklearn 风格（内部 MindSpore）
- **对应需求**：FR-21

### 构造参数（不加下划线）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| 待补 | 待补 | 待补 | 待补 |

### 方法说明

| 方法 | 签名 | 返回 |
|---|---|---|
| fit | fit(X, y=None) | self |
| predict | predict(X) | ndarray，预测密度 |

### 学习属性（尾下划线 _）

| 属性 | 说明 |
|---|---|
| n_features_in_ | 输入特征数 |

### 使用示例

待补

## CongestionDetector

- **所在模块**：待补，如 `models/anomaly.py`
- **对外接口**：sklearn 风格（内部 MindSpore）
- **对应需求**：FR-22

### 构造参数（不加下划线）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| 待补 | 待补 | 待补 | 待补 |

### 方法说明

| 方法 | 签名 | 返回 |
|---|---|---|
| fit | fit(X) | self |
| fit_predict | fit_predict(X, y=None) | ndarray，异常标记 |

### 学习属性（尾下划线 _）

| 属性 | 说明 |
|---|---|
| anomaly_scores_ | 各样本异常得分 |

### 使用示例

待补
