# -*- coding: utf-8 -*-
"""
train_predictor.py — 密度预测模型训练脚本（PyTorch 版）
================================================================
忠实复刻 D 的 agent/mindspore/train.py 逻辑，仅将训练框架从 MindSpore 换成
PyTorch（原因：RTX 5070 显卡 MindSpore 无法训练，PyTorch 可用 GPU）。

数据处理 / 切分 / 归一化 / 窗口 / 评估全部与 D 原逻辑一致：
  读 CSV → 加时间特征 → 按整天比例切分(0.15/0.15) → Min-Max(只用 train 集)
  → 切窗口(6→3) → 训练(100 epochs) → 评估 → 保存

用法：
    python train_predictor.py --data <训练CSV> [--out <输出目录>]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

from model import DensityPredictor
from data_config import (
    LEVEL_MAP,
    GATE_STATUS_MAP,
    SIGNAL_STATUS_MAP,
    STATE_FEATURES,
    TIME_FEATURES,
    ALL_FEATURES,
    N_STATE,
    DENSITY_COL_IDX,
    NORMALIZE_COLS,
)

# ============================================================
# 配置（与 D 原逻辑一致，不改）
# ============================================================
TICK_SECONDS = 10      # 每个 tick 的秒数（用于换算展示）
WINDOW_SIZE = 6        # 过去 6 tick = 60 秒
PRED_HORIZON = 3       # 未来 3 tick = 30 秒
MODEL_TYPE = "tsmixer"
DEVICE = "cuda"        # torch 训练用 GPU（5070）

EPOCHS = 100
BATCH_SIZE = 32
LR = 1e-3
PATIENCE = 10

VAL_RATIO = 0.15       # 验证集天数占比（D 原值）
TEST_RATIO = 0.15      # 测试集天数占比（D 原值）

DENSITY_LEVEL_BINS = [0.3, 0.6, 0.9]
ACC_TOLERANCE = 0.05


# ============================================================
# 评估指标（与 D 原逻辑一致）
# ============================================================

def mae(y_true, y_pred):
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true, y_pred):
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mape(y_true, y_pred, eps=1e-6):
    return float(np.mean(np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), eps)) * 100)


def r2(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return float(1.0 - ss_res / ss_tot)


def density_to_level(density):
    return np.digitize(density, bins=DENSITY_LEVEL_BINS) + 1


def level_accuracy(y_true, y_pred):
    return float(np.mean(density_to_level(y_true) == density_to_level(y_pred)))


def tolerance_accuracy(y_true, y_pred, tol=ACC_TOLERANCE):
    return float(np.mean(np.abs(y_true - y_pred) < tol))


# ============================================================
# 工具函数（与 D 原逻辑一致）
# ============================================================

def make_time_features(tick_times: pd.Series) -> np.ndarray:
    hour = tick_times.dt.hour.to_numpy().astype(np.float32)
    dow = tick_times.dt.dayofweek.to_numpy().astype(np.float32)
    return np.stack([
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
        np.sin(2 * np.pi * dow / 7),
        np.cos(2 * np.pi * dow / 7),
    ], axis=-1)


def pivot_by_node(df: pd.DataFrame, ticks, node_ids, col):
    piv = df.pivot_table(index="tick", columns="node_id", values=col)
    piv = piv.reindex(index=ticks, columns=node_ids)
    return piv.fillna(0.0).to_numpy(dtype=np.float32)


def make_windows(X, density_idx):
    """滑动窗口：X (T, N, F) -> X_windows (n, N, W, F) / y_windows (n, N, H)。
    与 D 原逻辑一致（含 transpose 顺序）。"""
    T = X.shape[0]
    samples, targets = [], []
    for i in range(T - WINDOW_SIZE - PRED_HORIZON + 1):
        samples.append(X[i: i + WINDOW_SIZE])
        targets.append(X[i + WINDOW_SIZE: i + WINDOW_SIZE + PRED_HORIZON, :, density_idx])
    X_windows = np.array(samples, dtype=np.float32).transpose(0, 2, 1, 3)
    y_windows = np.array(targets, dtype=np.float32).transpose(0, 2, 1)
    return X_windows, y_windows


# ============================================================
# 主流程
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="DensityPredictor 训练（PyTorch 版，复刻 D 逻辑）")
    ap.add_argument("--data", required=True, help="训练 CSV 路径")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints", "density_model_v2"),
                    help="输出目录（模型/preprocessor/评估报告）")
    args = ap.parse_args()

    DATA_PATH = args.data
    OUTPUT_DIR = args.out
    print("=" * 60)
    print("密度预测模型训练（PyTorch / 复刻 D 逻辑）")
    print("=" * 60)

    # ---------- 1. 加载数据 ----------
    print(f"\n[1/7] 加载数据：{DATA_PATH}")
    if not os.path.exists(DATA_PATH):
        print("错误：文件不存在，请检查 --data 路径。")
        return
    df = pd.read_csv(DATA_PATH, dtype={"node_id": str})
    print(f"      总行数：{len(df)}")

    # ---------- 2. 预处理 ----------
    print("\n[2/7] 预处理：字符串列编码、解析时间、排序 ...")
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
    X_state = np.zeros((n_ticks, n_nodes, N_STATE), dtype=np.float32)
    for f, col in enumerate(STATE_FEATURES):
        X_state[:, :, f] = pivot_by_node(df, ticks, node_ids, col)

    tick_to_ts = df.groupby("tick")["ts"].first().reindex(ticks)
    time_feat = make_time_features(tick_to_ts)
    X_time = np.repeat(time_feat[:, None, :], n_nodes, axis=1)

    X_raw = np.concatenate([X_state, X_time], axis=-1).astype(np.float32)
    print(f"      特征矩阵形状：{X_raw.shape}")

    # ---------- 4. 按整天切分 训练/验证/测试 ----------
    print("\n[4/7] 按整天比例切分 训练/验证/测试 ...")
    dates = tick_to_ts.dt.date
    unique_days = sorted(set(dates))
    n_days = len(unique_days)
    print(f"      数据范围：{unique_days[0]} ~ {unique_days[-1]}（{n_days} 天）")
    if n_days < 3:
        print("错误：数据不足 3 天，无法切分训练/验证/测试。")
        return
    n_val = max(1, round(n_days * VAL_RATIO))
    n_test = max(1, round(n_days * TEST_RATIO))
    while n_val + n_test >= n_days:
        if n_val > n_test:
            n_val -= 1
        else:
            n_test -= 1
    n_train = n_days - n_val - n_test

    train_days = unique_days[:n_train]
    val_days = unique_days[n_train: n_train + n_val]
    test_days = unique_days[n_train + n_val:]
    print(f"      训练 {n_train} 天（{train_days[0]} ~ {train_days[-1]}）｜ "
          f"验证 {n_val} 天（{val_days[0]} ~ {val_days[-1]}）｜ "
          f"测试 {n_test} 天（{test_days[0]} ~ {test_days[-1]}）")

    train_mask = dates.isin(train_days).to_numpy()
    val_mask = dates.isin(val_days).to_numpy()
    test_mask = dates.isin(test_days).to_numpy()
    train_raw = X_raw[train_mask]
    val_raw = X_raw[val_mask]
    test_raw = X_raw[test_mask]

    # ---------- 5. Min-Max 归一化（只用 train 集统计）----------
    print("\n[5/7] Min-Max 归一化（只用 train 集统计）...")
    norm_min = {c: float(train_raw[:, :, c].min()) for c in NORMALIZE_COLS}
    norm_max = {c: float(train_raw[:, :, c].max()) for c in NORMALIZE_COLS}

    def normalize(X):
        Xn = X.copy()
        for c in NORMALIZE_COLS:
            lo, hi = norm_min[c], norm_max[c]
            Xn[:, :, c] = (Xn[:, :, c] - lo) / (hi - lo + 1e-8)
        return Xn

    train_norm = normalize(train_raw)
    val_norm = normalize(val_raw)
    test_norm = normalize(test_raw)
    print(f"      density 列 norm_min={norm_min[0]:.4f} norm_max={norm_max[0]:.4f}")

    # ---------- 6. 切窗口 ----------
    print("\n[6/7] 切窗口（WINDOW_SIZE=6 → PRED_HORIZON=3）...")
    X_train, y_train = make_windows(train_norm, DENSITY_COL_IDX)
    X_val, y_val = make_windows(val_norm, DENSITY_COL_IDX)
    X_test, y_test = make_windows(test_norm, DENSITY_COL_IDX)
    print(f"      train: X{X_train.shape} y{y_train.shape} ｜ "
          f"val: X{X_val.shape} ｜ test: X{X_test.shape}")

    # ---------- 7. 训练（PyTorch，GPU）----------
    print(f"\n[7/7] 开始训练（PyTorch {MODEL_TYPE}，设备 {DEVICE}，{EPOCHS} epochs）...")
    predictor = DensityPredictor(
        window_size=WINDOW_SIZE,
        pred_horizon=PRED_HORIZON,
        model_type=MODEL_TYPE,
        device=DEVICE,
    )
    predictor.fit(
        X_train, y_train,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        lr=LR,
        val_split=0.0,                      # 不用内部切分，用按天划分的验证集
        validation_data=(X_val, y_val),
        patience=PATIENCE,
        feature_names=ALL_FEATURES,
        verbose=True,
    )

    # ---------- 评估（测试集，反归一化到真实密度）----------
    print("\n-- 测试集评估 --")
    y_pred_norm = predictor.predict(X_test)

    def inverse_density(y_norm):
        lo, hi = norm_min[DENSITY_COL_IDX], norm_max[DENSITY_COL_IDX]
        return y_norm * (hi - lo + 1e-8) + lo

    y_pred = np.clip(inverse_density(y_pred_norm), 0.0, None)
    y_true = inverse_density(y_test)

    metrics = {
        "MAE": mae(y_true, y_pred),
        "RMSE": rmse(y_true, y_pred),
        "MAPE": mape(y_true, y_pred),
        "R2": r2(y_true, y_pred),
        "LevelAcc": level_accuracy(y_true, y_pred),
        "TolAcc": tolerance_accuracy(y_true, y_pred),
    }
    print(f"  全局指标: {json.dumps(metrics, ensure_ascii=False)}")

    per_node = {}
    for n in range(n_nodes):
        per_node[node_ids[n]] = {
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
        "feature_columns": ALL_FEATURES,
        "state_feature_count": N_STATE,
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
            "framework": "pytorch",
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
    print("训练完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
