# -*- coding: utf-8 -*-
"""
train.py — 密度预测模型训练脚本（MindSpore 版本，支持多日多周数据）
===================================================================
在 VSCode 中直接点击右上角「运行」按钮即可执行。

流程：读 CSV → 加时间特征 → 按整天比例切分 → 归一化 → 切窗口 → 训练 → 评估 → 保存

说明：与原 PyTorch 版逻辑完全一致，仅底层模型换成 MindSpore（CPU 训练）。
     数据处理 / 切分 / 评估全部用 numpy / pandas，与框架无关。

适配成员F的多日数据（1 周 / 2 周 / 更多周均可）：
  - 每天 06:00-22:00，tick 跨天连续
  - 按星期几调整人流（周一早强、周五晚强、周末休闲）
  - 自动检测数据共多少天、约几周，按比例切分训练/验证/测试
  - 加入 hour/dow 时间特征，帮助模型识别"几点/周几"的人流规律
"""

import os
import json

import numpy as np
import pandas as pd

from model import DensityPredictor
from data_config import (
    RAW_COLUMNS,
    DENSITY_COL,
    DENSITY_COL_IDX,
    LEVEL_MAP,
    GATE_STATUS_MAP,
    SIGNAL_STATUS_MAP,
    STATE_FEATURES,
    TIME_FEATURES,
    ALL_FEATURES,
    N_STATE,
    NORMALIZE_COLS,
)

# ============================================================
# 配置区（只需要改这里）
# ============================================================

# 训练数据路径（F 的引擎快照，10 秒一个采样点）
DATA_PATH = r"D:\Documents\大学行政文件\大一下\2026QG暑期中期考核\项目目录\data\density_series.csv"

# 模型存档位置（默认放在本文件所在目录的 checkpoints 文件夹里）
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")

# 超参数
# 时间粒度说明：F 引擎现在是 10 秒一个采样点（1 tick = 10 秒）。
# F 要求：用前 60 秒数据预测后 30 秒。
# WINDOW_SIZE / PRED_HORIZON 单位是"时间步数"（tick 数）：
#   WINDOW_SIZE=6   → 过去 6 tick = 60 秒
#   PRED_HORIZON=3  → 未来 3 tick = 30 秒
TICK_SECONDS = 10      # 每个 tick 的秒数（用于换算展示）
WINDOW_SIZE = 6      # 用过去多少"时间步"的历史做预测（=60秒）
PRED_HORIZON = 3     # 预测未来多少"时间步"（=30秒）
MODEL_TYPE = "tsmixer"  # 可选 "tsmixer" 或 "gru"
DEVICE = "GPU"        # 设备：None 自动检测（GPU/Ascend/CPU），也可写 "cpu"/"gpu"

EPOCHS = 100         # 最多训练多少轮
BATCH_SIZE = 32
LR = 1e-3            # 学习率
PATIENCE = 10        # 验证集连续多少轮不下降就提前停止

# 切分方式：按"整天"比例自动切分（推荐）
# 数据自动检测共有多少天，按以下比例分配 训练/验证/测试（按时间顺序）
# 例：14 天数据（2 周）→ 训练 10 天、验证 2 天、测试 2 天
VAL_RATIO = 0.15   # 验证集天数占总天数的比例
TEST_RATIO = 0.15  # 测试集天数占总天数的比例

# 密度 → 等级 的阈值（与数据说明一致）
DENSITY_LEVEL_BINS = [0.3, 0.6, 0.9]

# 预测准确率容差
ACC_TOLERANCE = 0.05

# 增量训练开关（配合 F 的"先正常数据训练、再极端数据续训"流程）
#   False（默认）：每次训练从零开始，清空参数重建模型
#   True：若 checkpoints/density_model 已有模型，先加载旧参数，在其基础上继续训练
#     - 第一步：用正常数据训练（INCREMENTAL=False，生成基础模型）
#     - 第二步：把 DATA_PATH 换成极端数据，INCREMENTAL=True 再跑一次（续训）
INCREMENTAL = True


# ============================================================
# 评估指标
# ============================================================

def mae(y_true, y_pred):
    """平均绝对误差"""
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true, y_pred):
    """均方根误差"""
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mape(y_true, y_pred, eps=1e-6):
    """平均绝对百分比误差（分母加 eps 防止除零）"""
    return float(np.mean(np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), eps)) * 100)


def r2(y_true, y_pred):
    """决定系数 R²"""
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return float(1.0 - ss_res / ss_tot)


def density_to_level(density):
    """把密度值映射成等级数字（1=low, 2=medium, 3=high, 4=critical）"""
    return np.digitize(density, bins=DENSITY_LEVEL_BINS) + 1


def level_accuracy(y_true, y_pred):
    """等级准确率：预测等级与真实等级一致的比例"""
    return float(np.mean(density_to_level(y_true) == density_to_level(y_pred)))


def tolerance_accuracy(y_true, y_pred, tol=ACC_TOLERANCE):
    """误差容忍准确率：|预测 - 真实| < tol 的比例"""
    return float(np.mean(np.abs(y_true - y_pred) < tol))


# ============================================================
# 工具函数
# ============================================================

def make_time_features(tick_times: pd.Series) -> np.ndarray:
    """
    由每个 tick 对应的时刻生成时间特征。
    tick_times: pd.Series of datetime（index=tick，已按 tick 排序）

    返回：(n_ticks, 4) 的 [hour_sin, hour_cos, dow_sin, dow_cos]
      hour: 0-23 一天中的小时
      dow : 0-6  星期几（0=周一）
    """
    hour = tick_times.dt.hour.to_numpy().astype(np.float32)
    dow = tick_times.dt.dayofweek.to_numpy().astype(np.float32)
    return np.stack([
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
        np.sin(2 * np.pi * dow / 7),
        np.cos(2 * np.pi * dow / 7),
    ], axis=-1)


def pivot_by_node(df: pd.DataFrame, ticks, node_ids, col):
    """把长表某一列透视成 (n_ticks, n_nodes)，按 ticks/node_ids 对齐。
    缺失值统一填 0（引擎快照中速率列大量空值）。"""
    piv = df.pivot_table(index="tick", columns="node_id", values=col)
    piv = piv.reindex(index=ticks, columns=node_ids)
    return piv.fillna(0.0).to_numpy(dtype=np.float32)


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("密度预测模型训练（MindSpore / 多日多周数据）")
    print("=" * 60)

    # ---------- 1. 加载数据 ----------
    print(f"\n[1/7] 加载数据：{DATA_PATH}")
    if not os.path.exists(DATA_PATH):
        print("错误：文件不存在，请检查 DATA_PATH 是否填写正确。")
        return
    df = pd.read_csv(DATA_PATH, dtype={"node_id": str})
    print(f"      总行数：{len(df)}")

    # ---------- 2. 预处理 ----------
    print("\n[2/7] 预处理：字符串列编码、解析时间、排序 ...")
    # 字符串枚举列 → 数值（编码成数值喂模型，空值填 0）
    df["level"] = df["level"].map(LEVEL_MAP).fillna(0).astype(int)
    df["gate_status"] = df["gate_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["door_status"] = df["door_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["signal_status"] = df["signal_status"].map(SIGNAL_STATUS_MAP).fillna(0).astype(int)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["node_id", "tick"]).reset_index(drop=True)

    ticks = sorted(df["tick"].unique())
    node_ids = sorted(df["node_id"].unique())
    n_ticks, n_nodes = len(ticks), len(node_ids)
    n_features = len(ALL_FEATURES)
    print(f"      时间点：{n_ticks}（{len(set(df['ts'].dt.date))} 天）｜ 节点：{n_nodes} ｜ 特征：{n_features}")

    # ---------- 3. 构造特征矩阵 ----------
    print("\n[3/7] 构造特征矩阵：状态特征 + 时间特征 ...")
    # 3a. 状态特征（来自 CSV）
    X_state = np.zeros((n_ticks, n_nodes, N_STATE), dtype=np.float32)
    for f, col in enumerate(STATE_FEATURES):
        X_state[:, :, f] = pivot_by_node(df, ticks, node_ids, col)

    # 3b. 时间特征（每个 tick 一个，广播到所有节点）
    tick_to_ts = df.groupby("tick")["ts"].first().reindex(ticks)
    time_feat = make_time_features(tick_to_ts)                    # (n_ticks, 4)
    X_time = np.repeat(time_feat[:, None, :], n_nodes, axis=1)    # (n_ticks, n_nodes, 4)

    # 3c. 拼接
    X_raw = np.concatenate([X_state, X_time], axis=-1).astype(np.float32)
    print(f"      特征矩阵形状：{X_raw.shape}")

    # ---------- 4. 按整天比例自动切分 训练/验证/测试 ----------
    print("\n[4/7] 自动检测数据天数/周数，按整天比例切分 ...")
    dates = tick_to_ts.dt.date
    unique_days = sorted(set(dates))
    n_days = len(unique_days)
    n_weeks = n_days / 7
    print(f"      日期序列：{unique_days[0]} ~ {unique_days[-1]}"
          f"（共 {n_days} 天 ≈ {n_weeks:.1f} 周）")

    # 按比例计算验证/测试天数（至少留 1 天），其余全给训练
    if n_days < 3:
        print("错误：数据不足 3 天，无法切分训练/验证/测试。")
        return
    n_val = max(1, round(n_days * VAL_RATIO))
    n_test = max(1, round(n_days * TEST_RATIO))
    # 保证训练集至少剩 1 天
    while n_val + n_test >= n_days:
        if n_val > n_test:
            n_val -= 1
        else:
            n_test -= 1
    n_train = n_days - n_val - n_test

    train_days = unique_days[:n_train]
    val_days = unique_days[n_train : n_train + n_val]
    test_days = unique_days[n_train + n_val :]

    train_mask = dates.isin(train_days).to_numpy()
    val_mask = dates.isin(val_days).to_numpy()
    test_mask = dates.isin(test_days).to_numpy()

    train_raw = X_raw[train_mask]
    val_raw = X_raw[val_mask]
    test_raw = X_raw[test_mask]
    print(f"      训练 {n_train} 天（{train_days[0]} ~ {train_days[-1]}）"
          f"｜ 验证 {n_val} 天（{val_days[0]} ~ {val_days[-1]}）"
          f"｜ 测试 {n_test} 天（{test_days[0]} ~ {test_days[-1]}）")
    print(f"      训练 {len(train_raw)} ticks ｜ 验证 {len(val_raw)} ticks ｜ 测试 {len(test_raw)} ticks")

    # ---------- 5. 归一化 ----------
    print("\n[5/7] 归一化（Min-Max，只在训练段上计算）...")
    norm_min = {c: float(train_raw[:, :, c].min()) for c in NORMALIZE_COLS}
    norm_max = {c: float(train_raw[:, :, c].max()) for c in NORMALIZE_COLS}

    def normalize(X):
        X = X.copy()
        for c in NORMALIZE_COLS:
            lo, hi = norm_min[c], norm_max[c]
            X[:, :, c] = (X[:, :, c] - lo) / (hi - lo + 1e-8)
        return X

    train_norm = normalize(train_raw)
    val_norm = normalize(val_raw)
    test_norm = normalize(test_raw)

    # ---------- 6. 滑动窗口 + 训练 ----------
    print("\n[6/7] 构造滑动窗口样本 ...")

    def make_windows(X):
        """
        输入：(时间, 节点, 特征)
        输出：X (样本, 节点, 窗口, 特征) 和 y (样本, 节点, 预测步长)
        """
        T, N, F = X.shape
        samples, targets = [], []
        for i in range(T - WINDOW_SIZE - PRED_HORIZON + 1):
            samples.append(X[i : i + WINDOW_SIZE])
            targets.append(X[i + WINDOW_SIZE : i + WINDOW_SIZE + PRED_HORIZON, :, DENSITY_COL_IDX])
        X_windows = np.array(samples, dtype=np.float32).transpose(0, 2, 1, 3)
        y_windows = np.array(targets, dtype=np.float32).transpose(0, 2, 1)
        return X_windows, y_windows

    X_train, y_train = make_windows(train_norm)
    X_val, y_val = make_windows(val_norm)
    X_test, y_test = make_windows(test_norm)
    print(f"      样本数：训练 {len(X_train)} ｜ 验证 {len(X_val)} ｜ 测试 {len(X_test)}")

    print(f"\n[6/7] 开始训练（MindSpore，模型：{MODEL_TYPE}，最多 {EPOCHS} 轮）...")
    predictor = DensityPredictor(
        window_size=WINDOW_SIZE,
        pred_horizon=PRED_HORIZON,
        model_type=MODEL_TYPE,
        device=DEVICE,
    )

    # 增量训练：若开关开启且存在已训练模型，先加载旧参数再续训
    if INCREMENTAL:
        model_dir = os.path.join(OUTPUT_DIR, "density_model")
        state_path = os.path.join(model_dir, "model_state.ckpt")
        if os.path.exists(state_path):
            print(f"      增量训练模式：加载已有模型 {model_dir}，在其基础上续训 ...")
            predictor = DensityPredictor.load(model_dir, device=DEVICE)
            # 校验新数据与已训练模型结构一致
            if predictor.window_size != WINDOW_SIZE or predictor.pred_horizon != PRED_HORIZON:
                print("错误：已有模型结构（window/horizon）与新配置不一致，无法增量训练。")
                print(f"      已有模型 window={predictor.window_size}, pred={predictor.pred_horizon}")
                print(f"      新配置   window={WINDOW_SIZE}, pred={PRED_HORIZON}")
                return
            init_from = True
        else:
            print("      增量训练模式开启，但未找到已训练模型，将从头训练。")
            init_from = False
    else:
        init_from = False

    if len(X_val) > 0:
        predictor.fit(
            X_train, y_train,
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            lr=LR,
            patience=PATIENCE,
            feature_names=ALL_FEATURES,
            validation_data=(X_val, y_val),
            init_from=init_from,
        )
    else:
        predictor.fit(
            X_train, y_train,
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            lr=LR,
            val_split=0.15,
            patience=PATIENCE,
            feature_names=ALL_FEATURES,
            init_from=init_from,
        )

    # ---------- 7. 评估 ----------
    print("\n[7/7] 评估测试集（指标为原始密度单位）...")
    y_pred_norm = predictor.predict(X_test)

    def inverse_density(y_norm):
        lo, hi = norm_min[DENSITY_COL_IDX], norm_max[DENSITY_COL_IDX]
        return y_norm * (hi - lo + 1e-8) + lo

    y_pred = np.clip(inverse_density(y_pred_norm), 0.0, None)
    y_true = inverse_density(y_test)

    metrics = {
        "MAE": mae(y_true, y_pred),
        "RMSE": rmse(y_true, y_pred),
        "MAPE(%)": mape(y_true, y_pred),
        "R2": r2(y_true, y_pred),
        "LevelAcc(%)": level_accuracy(y_true, y_pred) * 100,
        f"Acc<{ACC_TOLERANCE}(%)": tolerance_accuracy(y_true, y_pred) * 100,
    }
    print("  全局指标：")
    for k, v in metrics.items():
        print(f"    {k:<16}: {v:.6f}")

    per_node = {}
    for n, node in enumerate(node_ids):
        per_node[node] = {
            "MAE": mae(y_true[:, n, :], y_pred[:, n, :]),
            "R2": r2(y_true[:, n, :], y_pred[:, n, :]),
            "LevelAcc": level_accuracy(y_true[:, n, :], y_pred[:, n, :]),
        }
    worst = sorted(per_node.items(), key=lambda kv: -kv[1]["MAE"])[:5]
    print("\n  MAE 最高的 5 个节点：")
    for node, m in worst:
        print(f"    {node}: MAE={m['MAE']:.4f}  R2={m['R2']:.4f}  LevelAcc={m['LevelAcc']*100:.1f}%")

    # ---------- 保存 ----------
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    model_dir = os.path.join(OUTPUT_DIR, "density_model")
    predictor.save(model_dir)
    print(f"\n模型已保存：{model_dir}")

    norm_params = {
        "feature_columns": ALL_FEATURES,           # 全部特征（含时间特征）
        "state_feature_count": N_STATE,            # 前 N_STATE 列来自 CSV，后面是时间特征
        "node_ids": node_ids,
        "window_size": WINDOW_SIZE,
        "pred_horizon": PRED_HORIZON,
        "density_col_idx": DENSITY_COL_IDX,
        "norm_min": {str(k): v for k, v in norm_min.items()},
        "norm_max": {str(k): v for k, v in norm_max.items()},
    }
    with open(os.path.join(OUTPUT_DIR, "preprocessor.json"), "w", encoding="utf-8") as f:
        json.dump(norm_params, f, ensure_ascii=False, indent=2)
    print(f"归一化参数已保存：{OUTPUT_DIR}/preprocessor.json")

    report = {
        "config": {
            "data_path": DATA_PATH,
            "window_size": WINDOW_SIZE,
            "pred_horizon": PRED_HORIZON,
            "model_type": MODEL_TYPE,
            "epochs": EPOCHS,
            "batch_size": BATCH_SIZE,
            "lr": LR,
            "framework": "mindspore",
            "train_days": [str(d) for d in train_days],
            "val_days": [str(d) for d in val_days],
            "test_days": [str(d) for d in test_days],
        },
        "n_timesteps": n_ticks,
        "n_nodes": n_nodes,
        "n_train_samples": len(X_train),
        "n_val_samples": len(X_val),
        "n_test_samples": len(X_test),
        "global_metrics": metrics,
        "per_node_metrics": per_node,
    }
    with open(os.path.join(OUTPUT_DIR, "evaluation_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"评估报告已保存：{OUTPUT_DIR}/evaluation_report.json")

    print("\n" + "=" * 60)
    print("训练完成 ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
