# -*- coding: utf-8 -*-
"""
alter_predict.py — 指定时段密度预测脚本
========================================
在 VSCode 中直接点击右上角「运行」按钮即可执行。

作用：预测指定时段（如"周一早10点"、"周五晚6点"）未来 30 秒的各节点密度。

与 predict.py 的区别：
  - predict.py 只预测数据末尾的"当前"时刻（取最后 6 个时间步=60秒做输入）
  - alter_predict.py 从数据中选取任意目标时段，取该时段之前 6 个时间步（60秒）做输入，
    预测该时段之后 3 个时间步（30秒）。支持一次配置多个时段。

流程：加载模型 → 读取数据 → 构造特征 → 对每个目标时段取前 6 个时间步
      → 预测未来 3 个时间步 → 汇总成多个 density_stats
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

# 1. 数据文件路径（F 给的任意一份 density_series.csv，格式相同）
DATA_PATH = r"D:\QG\QG2026暑假训练营\中期考核\项目目录\data\engine_snapshot.csv"

# 2. 训练产出的模型目录 与 归一化参数文件（train.py 运行后自动生成）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "checkpoints", "density_model")
PREPROCESSOR_PATH = os.path.join(BASE_DIR, "checkpoints", "preprocessor.json")

# 3. 要预测的目标时段列表（想加时段就复制一行）
#    weekday : 0=周一, 1=周二, 2=周三, 3=周四, 4=周五, 5=周六, 6=周日
#    hour    : 小时（6~21，数据每天只有 06:00~22:00）
#    label   : 这个时段的名称（会显示在输出里）
#    预测逻辑：取数据中"最后一个"匹配该 weekday+hour 的时刻，用其前 6 个时间步（60秒）
#    预测其后 3 个时间步（30秒）
PREDICT_TIMES = [
    {"weekday": 0, "hour": 10, "label": "周一早10点"},
    {"weekday": 4, "hour": 18, "label": "周五晚6点"},
    # {"weekday": 6, "hour": 15, "label": "周日下午3点"},
    # {"weekday": 2, "hour": 12, "label": "周三中午12点"},
]

# 4. 未来 30 秒密度怎么汇总成一个数（PRED_HORIZON=3 个时间步 = 30 秒）
#    "max"   = 未来 30 秒里的峰值密度（推荐：用于提前判断"即将爆满"）
#    "mean"  = 未来 30 秒平均密度
#    "first" = 未来第 1 个时间步的密度
PRED_AGG = "max"

# 5. 节点ID → 区域名 的映射（可选）
#    例如 {"canteen_1": "zone_canteen", "gate_east": "gate_east_01"}；
#    不填或没匹配到的节点，输出直接用节点ID作为 key。
NODE_NAME_MAP = {}

# 6. 计算设备（None 自动选：cuda > mps > cpu）
DEVICE = None

# 7. 时间粒度（与 train.py 保持一致）：1 tick = TICK_SECONDS 秒
#    引擎现在 10 秒一个采样点
TICK_SECONDS = 10

# 星期名称（用于显示）
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ============================================================
# 工具函数
# ============================================================

def build_feature_matrix(df, ticks, node_ids, state_features, state_feature_count):
    """
    构造完整特征矩阵 (n_ticks, n_nodes, n_features)。
    状态特征来自 CSV，时间特征(hour/dow sin/cos)由 timestamp 派生。
    """
    n_ticks, n_nodes = len(ticks), len(node_ids)
    n_features = len(state_features) + 4

    # ---- 状态特征 ----
    X_state = np.zeros((n_ticks, n_nodes, state_feature_count), dtype=np.float32)
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
    X_time = np.repeat(time_feat[:, None, :], n_nodes, axis=1)

    # ---- 拼接 ----
    X_raw = np.concatenate([X_state, X_time], axis=-1).astype(np.float32)
    return X_raw, tick_to_ts


def normalize_by_pp(X, norm_min, norm_max):
    """用训练时保存的 min/max 归一化（只归一化配置过的列）。"""
    X = X.copy()
    for c in norm_min.keys():
        lo, hi = norm_min[c], norm_max[c]
        X[:, :, c] = (X[:, :, c] - lo) / (hi - lo + 1e-8)
    return X


def aggregate_density(y_pred, agg):
    """把 (节点, 预测步长) 聚合成每个节点一个数。"""
    if agg == "max":
        return y_pred.max(axis=1)
    elif agg == "mean":
        return y_pred.mean(axis=1)
    elif agg == "first":
        return y_pred[:, 0]
    else:
        raise ValueError(f"PRED_AGG 只支持 max/mean/first，收到 {agg}")


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("指定时段密度预测（alter_predict）")
    print("=" * 60)

    # ---------- 1. 加载模型和归一化参数 ----------
    print(f"\n[1/5] 加载模型：{MODEL_DIR}")
    if not os.path.exists(os.path.join(MODEL_DIR, "model_state.pt")):
        print("错误：没找到训练好的模型，请先运行 train.py 训练。")
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

    node_ids = pp["node_ids"]
    norm_min = {int(k): v for k, v in pp["norm_min"].items()}
    norm_max = {int(k): v for k, v in pp["norm_max"].items()}
    density_col_idx = pp["density_col_idx"]
    state_feature_count = pp.get("state_feature_count", len(pp["feature_columns"]))
    state_features = pp["feature_columns"][:state_feature_count]
    window_size = predictor.window_size
    pred_horizon = predictor.pred_horizon

    # ---------- 2. 读取数据 ----------
    print(f"\n[2/5] 读取数据：{DATA_PATH}")
    if not os.path.exists(DATA_PATH):
        print("错误：文件不存在，请检查 DATA_PATH。")
        return
    df = pd.read_csv(DATA_PATH, dtype={"node_id": str})
    print(f"      总行数：{len(df)}")

    # 节点一致性检查
    data_nodes = sorted(df["node_id"].unique())
    missing_nodes = [n for n in node_ids if n not in data_nodes]
    if missing_nodes:
        print("错误：当前数据文件的节点与训练时不一致！")
        print(f"  训练时节点数：{len(node_ids)}，当前数据节点数：{len(data_nodes)}")
        print(f"  缺少训练节点（前10个）：{missing_nodes[:10]}")
        print("  请把 DATA_PATH 改成与训练时同节点的数据文件。")
        return

    # ---------- 3. 预处理 ----------
    print("\n[3/5] 预处理：字符串列编码、解析时间、构造特征 ...")
    df["level"] = df["level"].map(LEVEL_MAP).fillna(0).astype(int)
    df["gate_status"] = df["gate_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["door_status"] = df["door_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["signal_status"] = df["signal_status"].map(SIGNAL_STATUS_MAP).fillna(0).astype(int)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["node_id", "tick"]).reset_index(drop=True)

    ticks = sorted(df["tick"].unique())
    X_raw, tick_to_ts = build_feature_matrix(
        df, ticks, node_ids, state_features, state_feature_count
    )
    print(f"      特征矩阵：{X_raw.shape[0]} 个时间点 × {X_raw.shape[1]} 个节点 × {X_raw.shape[2]} 个特征")
    print(f"      时间范围：{tick_to_ts.iloc[0]} ~ {tick_to_ts.iloc[-1]}")

    # 归一化整个矩阵
    X_norm = normalize_by_pp(X_raw, norm_min, norm_max)

    # ---------- 4. 对每个目标时段做预测 ----------
    print(f"\n[4/5] 共配置 {len(PREDICT_TIMES)} 个目标时段 ...")

    results = []
    for item in PREDICT_TIMES:
        wd = item["weekday"]
        hr = item["hour"]
        label = item.get("label", f"周{WEEKDAY_NAMES[wd]} {hr}点")

        # 找到数据中最后一个匹配 weekday+hour 的时刻
        mask = (tick_to_ts.dt.dayofweek == wd) & (tick_to_ts.dt.hour == hr)
        matched_ticks = tick_to_ts[mask].index

        if len(matched_ticks) == 0:
            print(f"  [跳过] {label}（周{WEEKDAY_NAMES[wd]} {hr}:00）：数据中没有这个时段的记录")
            continue

        target_tick = matched_ticks[-1]
        target_ts = tick_to_ts[target_tick]
        idx = ticks.index(target_tick)

        # 检查窗口和预测范围是否越界
        if idx < window_size:
            print(f"  [跳过] {label}：目标时刻 {target_ts} 之前不足 {window_size} 个时间步（{window_size*TICK_SECONDS}秒）历史")
            continue
        if idx + pred_horizon > len(ticks):
            print(f"  [跳过] {label}：目标时刻 {target_ts} 之后不足 {pred_horizon} 个时间步（{pred_horizon*TICK_SECONDS}秒）")
            continue

        # 取窗口 [idx-W, idx) 作为历史，预测 [idx, idx+H)
        window_raw = X_norm[idx - window_size : idx]                 # (窗口, 节点, 特征)
        X_input = window_raw.transpose(1, 0, 2)[None]                # (1, 节点, 窗口, 特征)

        y_pred_norm = predictor.predict(X_input)                     # (1, 节点, 预测步长)

        # 逆归一化 + 裁剪
        lo, hi = norm_min[density_col_idx], norm_max[density_col_idx]
        y_pred = y_pred_norm * (hi - lo + 1e-8) + lo
        y_pred = np.clip(y_pred[0], 0.0, None)                       # (节点, 预测步长)

        # 聚合
        agg_values = aggregate_density(y_pred, PRED_AGG)

        # 组装 density_stats
        density_stats = {}
        for i, node in enumerate(node_ids):
            key = NODE_NAME_MAP.get(node, node)
            density_stats[key] = float(round(agg_values[i], 4))

        # 预测时间段
        hist_start = tick_to_ts[ticks[idx - window_size]]
        hist_end = tick_to_ts[ticks[idx - 1]]
        pred_start = target_ts
        pred_end = target_ts + pd.Timedelta(seconds=pred_horizon * TICK_SECONDS)
        agg_name = {"max": "峰值", "mean": "平均", "first": "第1步"}.get(PRED_AGG, PRED_AGG)

        result = {
            "label": label,
            "target_time": str(target_ts),
            "history_window": f"{hist_start} ~ {hist_end}",
            "prediction_period": {
                "start": str(pred_start),
                "end": str(pred_end),
                "aggregation": PRED_AGG,
                "aggregation_label": agg_name,
            },
            "density_stats": density_stats,
        }
        results.append(result)

        print(f"\n  [{label}] 目标时刻：{target_ts}")
        print(f"      历史窗口：{hist_start} ~ {hist_end}")
        print(f"      预测时段：{pred_start} ~ {pred_end}（取{agg_name}密度）")

    # ---------- 5. 汇总输出 ----------
    if len(results) == 0:
        print("\n[5/5] 没有任何时段成功预测，请检查 PREDICT_TIMES 配置。")
        return

    print("\n[5/5] 汇总输出 ...")
    print("\n" + "=" * 60)
    print("预测结果（每个时段一个 density_stats）")
    print("=" * 60)
    for r in results:
        print(f"\n  [{r['label']}] {r['target_time']}（{r['prediction_period']['aggregation_label']}）")
        for k, v in r["density_stats"].items():
            print(f"    {k:<18}: {v}")
    print("\n" + "=" * 60)

    # 保存 JSON
    out_path = os.path.join(BASE_DIR, "checkpoints", "alter_prediction.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    output = {
        "generated_at": str(pd.Timestamp.now()),
        "note": f"指定时段密度预测，每个时段用其前 {window_size} 个时间步（{window_size*TICK_SECONDS}秒）预测未来 {pred_horizon} 个时间步（{pred_horizon*TICK_SECONDS}秒）",
        "num_predictions": len(results),
        "predictions": results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n预测结果已保存：{out_path}")


if __name__ == "__main__":
    main()
