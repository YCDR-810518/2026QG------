# MindSpore 版本 — AI 模型模块

> 与 `d_mindspore/`（PyTorch 版）逻辑完全一致，仅将底层框架从 PyTorch 替换为 MindSpore。
> 原 PyTorch 代码保留不动。

## 目录结构

```
mindspore/
├── model.py          ← 模型类定义（DensityPredictor / CongestionDetector）
├── train.py          ← 训练脚本（数据预处理 + 训练主循环 + 评估）
├── predict.py        ← 预测脚本（当前时刻 → 未来密度）
├── alter_predict.py  ← 指定时段预测脚本（周一早10点 / 周五晚6点等）
├── alert.py          ← 预警脚本（异常检测 + 分级 + 入库JSON）
├── service.py        ← 预测+预警封装服务（供成员F定时调用）
├── data_config.py    ← 数据格式集中配置
└── requirements.txt  ← 依赖
```

## 安装依赖

MindSpore 分 CPU / GPU / Ascend 版本，按目标机器选择：

```bash
# CPU 版
pip install mindspore==2.2.14

# GPU 版（需 NVIDIA GPU + CUDA）
pip install mindspore-gpu==2.2.14

# 其他版本见官方指南
# https://www.mindspore.cn/install
```

同时需要 numpy / pandas / requests：

```bash
pip install "numpy<2" pandas requests
```

## 设备自动检测（CPU / GPU / Ascend 兼容）

`model.py` 在加载时自动检测可用设备，**无需改代码**：

| 环境 | 自动选择 |
|---|---|
| 装了 GPU 版 + 有 NVIDIA GPU | GPU |
| 装了 Ascend 版 + 有昇腾设备 | Ascend |
| 装了 CPU 版 | CPU |

**手动强制指定**（可选）：设环境变量 `MINDSPORE_DEVICE`：

```bash
# Windows CMD
set MINDSPORE_DEVICE=CPU
python train.py

# Linux / Mac
MINDSPORE_DEVICE=GPU python train.py
```

也可在 `train.py` / `predict.py` 的配置区设 `DEVICE = "gpu"` 或 `"cpu"` 强制指定。

## 与 PyTorch 版的差异

| 项 | PyTorch 版 | MindSpore 版 |
|---|---|---|
| 模型权重文件 | `model_state.pt` | `model_state.ckpt` |
| 网络基类 | `nn.Module` | `nn.Cell` |
| 前向方法 | `forward()` | `construct()` |
| 训练循环 | DataLoader + optimizer.step | 手写 batch 循环 + `ops.value_and_grad` |
| 设备 | cuda/mps/cpu | 自动检测（GPU / Ascend / CPU） |
| LayerNorm | `nn.LayerNorm(n_features)` | `nn.LayerNorm((n_features,))` |

## 用法

1. 训练：打开 `train.py`，确认 `DATA_PATH`，点 VSCode「运行」
2. 预测：打开 `predict.py`，点运行
3. 指定时段预测：打开 `alter_predict.py`，点运行
4. 预警：打开 `alert.py`，点运行
5. 给F的服务：`service.py`（`from service import SecurityService`）

所有脚本的 `DATA_PATH` / 超参都在文件顶部「配置区」，直接改后运行即可。

## 训练输出（checkpoints/）

```
checkpoints/
├── density_model/
│   ├── model_state.ckpt   MindSpore 权重
│   └── config.json        模型超参（含 framework: "mindspore"）
├── preprocessor.json      归一化参数
└── evaluation_report.json 评估报告
```

> 注意：MindSpore 版和 PyTorch 版的模型权重格式不通用，必须用各自的 train.py 分别训练。
