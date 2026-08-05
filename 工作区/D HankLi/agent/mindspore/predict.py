# -*- coding: utf-8 -*-
"""
predict.py — 密度预测推理脚本（MindSpore 版本）
================================================
在 VSCode 中直接点击右上角「运行」按钮即可执行。

流程：加载 train.py 训练好的模型 → 读取当前数据 → 取最近一段历史
      → 预测未来密度 → 输出 Dict[node_id, 密度]

交付给成员C的格式：Dict[str, float]
    例如：{"G01": 0.85, "P01": 0.62, "N02": 0.31, ...}
"""

import os
import json

import numpy as np
import pandas as pd

from model import DensityPredictor
from data_config import LEVEL_MAP, GATE_STATUS_MAP, SIGNAL_STATUS_MAP

# ============================================================
# 配置区（只需要改这里）
# ============================================================

# 1. 当前数据文件路径（右键文件 → 复制路径 → 粘贴到这里）
#    注意：必须和 train.py 训练时用的是同一个数据文件（节点名一致）。
DATA_PATH = r"D:\Documents\大学行政文件\大一下\2026QG暑期中期考核\项目目录\data\engine_snapshot.csv"

# 2. 训练产出的模型目录 与 归一化参数文件（MindSpore train.py 运行后自动生成）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "checkpoints", "density_model")
PREPROCESSOR_PATH = os.path.join(BASE_DIR, "checkpoints", "preprocessor.json")

# 3. 未来 30 秒密度怎么汇总成一个数（PRED_HORIZON=3 个时间步 = 30 秒）
#    "max"   = 未来 30 秒里的峰值密度（推荐：用于提前判断"即将爆满"）
#    "mean"  = 未来 30 秒平均密度
#    "first" = 未来第 1 个时间步的密度
PRED_AGG = "max"

# 4. 节点ID → 区域名 的映射（可选）。
#    不填或没匹配到的节点，输出直接用节点ID作为 key。
NODE_NAME_MAP = {}

# 5. 计算设备（None 自动检测：GPU/Ascend/CPU）
DEVICE = None

# 6. 时间粒度（与 train.py 保持一致）：1 tick = TICK_SECONDS 秒
#    引擎现在 10 秒一个采样点；F 要求前 60 秒预测后 30 秒
TICK_SECONDS = 10


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("密度预测推理（MindSpore）")
    print("=" * 60)

    # ---------- 1. 加载训练好的模型和归一化参数 ----------
    print(f"\n[1/5] 加载模型：{MODEL_DIR}")
    if not os.path.exists(os.path.join(MODEL_DIR, "model_state.ckpt")):
        print("错误：没找到训练好的 MindSpore 模型，请先运行 train.py 训练。")
        return
    predictor = DensityPredictor.load(MODEL_DIR, device=DEVICE)
    print(f"      模型加载成功（window={predictor.window_size}, "
          f"horizon={predictor.pred_horizon}, type={predictor.model_type})")

    print(f"[1/5] 加载归一化参数：{PREPROCESSOR_PATH}")
    if not os.path.exists(PREPROCESSOR_PATH):
        print("错误：没找到 preprocessor.json，请先运行 train.py 训练。")
        return
    with open(PREPROCESSOR_PATH, "r", encoding="utf-8") as f:
        pp = json.load(f)

    node_ids = pp["node_ids"]                    # 训练时的节点列表（顺序固定）
    norm_min = {int(k): v for k, v in pp["norm_min"].items()}
    norm_max = {int(k): v for k, v in pp["norm_max"].items()}
    density_col_idx = pp["density_col_idx"]
    state_feature_count = pp.get("state_feature_count", len(pp["feature_columns"]))
    window_size = predictor.window_size
    pred_horizon = predictor.pred_horizon

    # ---------- 2. 读取当前数据 ----------
    print(f"\n[2/5] 读取数据：{DATA_PATH}")
    if not os.path.exists(DATA_PATH):
        print("错误：文件不存在，请检查 DATA_PATH。")
        return
    df = pd.read_csv(DATA_PATH, dtype={"node_id": str})
    print(f"      总行数：{len(df)}")

    # ---------- 3. 预处理（与 train.py 相同） ----------
    print("\n[3/5] 预处理：字符串列编码、解析时间、排序、构造特征 ...")
    df["level"] = df["level"].map(LEVEL_MAP).fillna(0).astype(int)
    df["gate_status"] = df["gate_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["door_status"] = df["door_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["signal_status"] = df["signal_status"].map(SIGNAL_STATUS_MAP).fillna(0).astype(int)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["node_id", "tick"]).reset_index(drop=True)

    ticks = sorted(df["tick"].unique())
    feature_columns = pp["feature_columns"]      # 全部特征名（状态 + 时间）
    n_features = len(feature_columns)
    state_features = feature_columns[:state_feature_count]  # 前 N 列是状态特征

    # ---- 节点一致性检查 ----
    data_nodes = sorted(df["node_id"].unique())
    missing_nodes = [n for n in node_ids if n not in data_nodes]
    if missing_nodes:
        print("错误：当前数据文件的节点与训练时不一致！")
        print(f"  训练时节点数：{len(node_ids)}，当前数据节点数：{len(data_nodes)}")
        print(f"  缺少训练节点（前10个）：{missing_nodes[:10]}")
        print("  请把 DATA_PATH 改成训练时使用的同一个数据文件。")
        return

    # ---- 状态特征（空值填 0） ----
    X_state = np.zeros((len(ticks), len(node_ids), state_feature_count), dtype=np.float32)
    for f, col in enumerate(state_features):
        piv = df.pivot_table(index="tick", columns="node_id", values=col)
        piv = piv.reindex(index=ticks, columns=node_ids)
        X_state[:, :, f] = piv.fillna(0.0).to_numpy(dtype=np.float32)

    # ---- 时间特征 ----
    tick_to_ts = df.groupby("tick")["ts"].first().reindex(ticks)
    hour = tick_to_ts.dt.hour.to_numpy().astype(np.float32)
    dow = tick_to_ts.dt.dayofweek.to_numpy().astype(np.float32)
    time_feat = np.stack([
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
        np.sin(2 * np.pi * dow / 7),
        np.cos(2 * np.pi * dow / 7),
    ], axis=-1)
    X_time = np.repeat(time_feat[:, None, :], len(node_ids), axis=1)

    # ---- 拼接成完整特征矩阵 ----
    X_raw = np.concatenate([X_state, X_time], axis=-1).astype(np.float32)
    print(f"      数据形状：{len(ticks)} 个时间点 × {len(node_ids)} 个节点 × {n_features} 个特征")

    # ---------- 4. 取最近窗口并归一化、预测 ----------
    if len(ticks) < window_size:
        print(f"错误：数据只有 {len(ticks)} 个时间点，少于窗口长度 {window_size}。")
        return

    print(f"\n[4/5] 取最近 {window_size} 个时间点（{window_size*TICK_SECONDS}秒）作为当前状态，预测未来 {pred_horizon} 个时间步（{pred_horizon*TICK_SECONDS}秒）...")
    last_window_raw = X_raw[-window_size:]                          # (窗口, 节点, 特征)

    # 归一化（用训练时保存的 min/max）
    last_window = last_window_raw.copy()
    for c in norm_min.keys():
        lo, hi = norm_min[c], norm_max[c]
        last_window[:, :, c] = (last_window[:, :, c] - lo) / (hi - lo + 1e-8)

    # 转成 model 期望的形状：(样本1, 节点, 窗口, 特征)
    X_input = last_window.transpose(1, 0, 2)[None]                 # (1, 节点, 窗口, 特征)
    y_pred_norm = predictor.predict(X_input)                        # (1, 节点, 预测步长)

    # 逆归一化
    lo = norm_min[density_col_idx]
    hi = norm_max[density_col_idx]
    y_pred = y_pred_norm * (hi - lo + 1e-8) + lo
    y_pred = np.clip(y_pred[0], 0.0, None)                          # (节点, 预测步长)

    # ---------- 5. 汇总成 Dict[node_id, 密度] ----------
    print("\n[5/5] 汇总输出 ...")
    if PRED_AGG == "max":
        agg_values = y_pred.max(axis=1)
    elif PRED_AGG == "mean":
        agg_values = y_pred.mean(axis=1)
    elif PRED_AGG == "first":
        agg_values = y_pred[:, 0]
    else:
        raise ValueError(f"PRED_AGG 只支持 max/mean/first，收到 {PRED_AGG}")

    density_stats = {}
    for i, node in enumerate(node_ids):
        key = NODE_NAME_MAP.get(node, node)
        density_stats[key] = float(round(agg_values[i], 4))

    # 预测时间段
    last_ts = tick_to_ts.iloc[-1]
    pred_start = last_ts + pd.Timedelta(seconds=TICK_SECONDS)
    pred_end = last_ts + pd.Timedelta(seconds=pred_horizon * TICK_SECONDS)
    agg_name = {"max": "峰值", "mean": "平均", "first": "第1步"}.get(PRED_AGG, PRED_AGG)

    print("\n" + "=" * 60)
    print("预测结果（交付给成员C的 density_stats）")
    print("=" * 60)
    print(f"  预测时间段：{pred_start} ~ {pred_end}（取{agg_name}密度）")
    for k, v in density_stats.items():
        print(f"    {k:<16}: {v}")
    print("=" * 60)

    out_path = os.path.join(BASE_DIR, "checkpoints", "prediction.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    output = {
        "prediction_period": {
            "start": str(pred_start),
            "end": str(pred_end),
            "aggregation": PRED_AGG,
            "aggregation_label": agg_name,
            "note": f"预测未来 {pred_horizon} 个时间步（{pred_horizon*TICK_SECONDS}秒）各节点密度",
        },
        "density_stats": density_stats,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n预测结果已保存：{out_path}")


if __name__ == "__main__":
    main()
