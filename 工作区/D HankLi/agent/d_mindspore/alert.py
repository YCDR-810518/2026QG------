# -*- coding: utf-8 -*-
"""
alert.py — 安全预警与应急响应脚本（含 DensityPredictor 联动）
===============================================================
在 VSCode 中直接点击右上角「运行」按钮即可执行。

流程：加载 DensityPredictor + CongestionDetector 基线 → 读最新数据
      → 用最近60帧预测未来10分钟 → 检测最近10帧 + 用预测确认拥堵
      → 分级(L0~L4) + 补字段 → 打印入库 JSON + 保存文件

交付给后端(成员B)：标准预警事件 JSON，通过 POST 入库
    event_id / timestamp / level / type / node_id / node_name
    current_density / threshold_density / predicted_duration_min
    suggested_action / status

联动原理：
    CongestionDetector 的拥堵检测有两级条件 ——
      1) 当前密度 > 历史 P95（统计异常）
      2) 当前密度 > 预测值 + 2σ（相对预期异常）
    传入 X_pred 后第二级生效：预测说"这个高峰是正常的会回落"，
    就不算异常，减少误报。
"""

import os
import json
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from model import CongestionDetector, DensityPredictor
from data_config import LEVEL_MAP, GATE_STATUS_MAP, SIGNAL_STATUS_MAP

# ============================================================
# 配置区（只需要改这里）
# ============================================================

# 1. 数据文件路径（和 train.py / predict.py 保持一致）
DATA_PATH = r"D:\QG\QG2026暑假训练营\中期考核\工作区\D HankLi\agent\engine_snapshot.csv"

# 2. 模型 / 归一化参数目录（train.py 运行后自动生成）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "checkpoints", "density_model")
PREPROCESSOR_PATH = os.path.join(BASE_DIR, "checkpoints", "preprocessor.json")

# 3. 后端接口（成员B接口文档）
#    POST /api/v1/admin/login             登录，拿 token
#    POST /api/v1/security/alerts/create  预警入库，需带 Authorization: Bearer <token>
BACKEND_BASE = "http://192.168.1.114:8100"            # 后端服务地址（成员B提供）
LOGIN_URL = BACKEND_BASE + "/api/v1/admin/login"
ALERT_API = BACKEND_BASE + "/api/v1/security/alerts/create"
LOGIN_USERNAME = "ZTZT"                            # 登录账号（成员B提供）
LOGIN_PASSWORD = "Zzt20070124"                     # 登录密码（成员B提供）

# 调试模式开关
# True ：即使当前没检测到真实预警，也构造一条演示预警发给后端，验证链路是否通
# False：只有检测到真实预警才发送
DEMO_MODE = True

# 4. 检测器参数（特征列索引对齐当前数据列序）
DENSITY_FEATURE_IDX = 0   # density
GATE_STATUS_FEATURE_IDX = 3  # gate_status
GATE_FLOW_FEATURE_IDX = 4    # gate_flow_rate

# 5. 用最近多少帧做检测（多帧才能检测滞留）
RECENT_FRAMES = 10

# 6. 时间粒度（与 train.py 保持一致）：1 tick = TICK_SECONDS 秒
#    引擎现在 10 秒一个采样点；F 要求前 60 秒预测后 30 秒
TICK_SECONDS = 10

# 节点ID → 区域名（可选，不填就用节点ID本身）
NODE_NAME_MAP = {}


# ============================================================
# 数据加载
# ============================================================

def load_data():
    """
    读取 CSV，构造与 train.py 一致的完整特征矩阵 (n_ticks, n_nodes, 10)。
    10 维 = 6 状态特征 + 4 时间特征，并按 preprocessor 的节点顺序对齐。
    返回 (X_raw, X_norm, node_ids, density_col_idx)
      X_raw  : 未归一化
      X_norm : 已按训练时 min/max 归一化
    """
    df = pd.read_csv(DATA_PATH, dtype={"node_id": str})
    df["level"] = df["level"].map(LEVEL_MAP).fillna(0).astype(int)
    df["gate_status"] = df["gate_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["door_status"] = df["door_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
    df["signal_status"] = df["signal_status"].map(SIGNAL_STATUS_MAP).fillna(0).astype(int)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["node_id", "tick"]).reset_index(drop=True)

    ticks = sorted(df["tick"].unique())
    with open(PREPROCESSOR_PATH, "r", encoding="utf-8") as f:
        pp = json.load(f)

    node_ids = pp["node_ids"]
    feature_columns = pp["feature_columns"]                  # 10 个特征名
    state_feature_count = pp.get("state_feature_count", len(feature_columns))
    state_features = feature_columns[:state_feature_count]   # 前 6 个状态特征
    norm_min = {int(k): v for k, v in pp["norm_min"].items()}
    norm_max = {int(k): v for k, v in pp["norm_max"].items()}
    density_col_idx = pp["density_col_idx"]

    # 节点一致性检查
    data_nodes = sorted(df["node_id"].unique())
    missing = [n for n in node_ids if n not in data_nodes]
    if missing:
        raise ValueError(f"数据节点与训练时不一致，缺少: {missing[:10]}")

    n_t, n_n, n_f = len(ticks), len(node_ids), len(feature_columns)

    # ---- 状态特征（10 列） ----
    X_state = np.zeros((n_t, n_n, state_feature_count), dtype=np.float32)
    for f, col in enumerate(state_features):
        piv = df.pivot_table(index="tick", columns="node_id", values=col)
        X_state[:, :, f] = piv.reindex(index=ticks, columns=node_ids).fillna(0.0).to_numpy(dtype=np.float32)

    # ---- 时间特征（4 列） ----
    tick_to_ts = df.groupby("tick")["ts"].first().reindex(ticks)
    hour = tick_to_ts.dt.hour.to_numpy().astype(np.float32)
    dow = tick_to_ts.dt.dayofweek.to_numpy().astype(np.float32)
    time_feat = np.stack([
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
        np.sin(2 * np.pi * dow / 7),
        np.cos(2 * np.pi * dow / 7),
    ], axis=-1)
    X_time = np.repeat(time_feat[:, None, :], n_n, axis=1)

    # ---- 拼接 ----
    X_raw = np.concatenate([X_state, X_time], axis=-1).astype(np.float32)

    # ---- 归一化（只归 norm_min 里的列，即 0/1/2/4） ----
    X_norm = X_raw.copy()
    for c in norm_min.keys():
        lo, hi = norm_min[c], norm_max[c]
        X_norm[:, :, c] = (X_norm[:, :, c] - lo) / (hi - lo + 1e-8)

    return X_raw, X_norm, node_ids, density_col_idx, norm_min, norm_max, tick_to_ts


def classify(events, node_names):
    """
    对检测器产出的 events 分级并补字段。
    输入 events: 检测器原始输出（node_id 是数字索引）
    返回 list of dict：入库格式

    阈值/持续时间按事件类型映射：
      - congestion  : threshold_p95 → 阈值；predicted_duration_min → 持续
      - loitering   : rate_threshold → 阈值；sustained_frames → 持续
      - gate_anomaly: expected_flow/gate_flow_rate → 参考阈值；无持续
    """
    alerts = []
    for ev in events:
        node_idx = ev["node_id"]
        node_name = node_names[node_idx]

        # ---- 按 type 分级 + 取阈值/持续时间 ----
        if ev["type"] == "congestion":
            # 拥堵等级：L1 关注 / L2 预警 / L3 严重
            if ev.get("severity") == "L1":
                level = "L1"
                suggested = "关注该节点人流趋势"
                threshold = ev.get("threshold_p85", ev.get("threshold_p95", None))
            else:
                level = "L3" if ev.get("exceed_ratio", 1) >= 1.5 else "L2"
                suggested = "门闸限流50%"
                threshold = ev.get("threshold_p95", None)
            duration = ev.get("predicted_duration_min", None)
        elif ev["type"] == "loitering":
            level = "L3" if ev["severity"] == "L3" else "L2"
            suggested = "派员疏散滞留人群"
            threshold = ev.get("rate_threshold", None)      # 滞留速率阈值
            duration = ev.get("sustained_frames", None)     # 持续帧数
        elif ev["type"] == "gate_anomaly":
            if ev.get("subtype") == "hardware_fault":
                level = "L3"
                suggested = "紧急检修门闸设备"
            else:
                level = "L2"
                suggested = "检查门闸传感器与通行速率"
            threshold = ev.get("expected_flow", ev.get("gate_flow_rate", None))
            duration = None
        else:
            level = "L2"
            suggested = "待人工确认"
            threshold = None
            duration = None

        # ---- 预计持续兜底：检测器没算出时按等级给默认值（时间步） ----
        # L1 关注 / L2 预警默认 5 个时间步（=50秒），L3 严重默认 10 个时间步（=100秒）
        if duration is None:
            duration = 10 if level == "L3" else 5

        alert = {
            "event_id": f"EVT-{datetime.now():%Y%m%d}-{str(uuid.uuid4())[:4].upper()}",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "type": ev["type"],
            "node_id": node_names[node_idx],
            "node_name": NODE_NAME_MAP.get(node_names[node_idx], node_names[node_idx]),
            "current_density": ev.get("current_density", None),
            "threshold_density": threshold,
            "predicted_duration_min": duration,
            "suggested_action": suggested,
            "status": "active",
        }
        alerts.append(alert)

    return alerts


def login_get_token():
    """
    调用后端登录接口获取 Bearer token。
    返回 token 字符串；失败返回 None。
    """
    import requests
    try:
        resp = requests.post(
            LOGIN_URL,
            json={"username": LOGIN_USERNAME, "password": LOGIN_PASSWORD},
            timeout=5,
        )
        body = resp.json()
        if resp.status_code == 200 and body.get("code") == 0:
            token = body["data"]["token"]
            print(f"  登录成功，已获取 token（{token[:12]}...）")
            return token
        else:
            print(f"  登录失败：code={body.get('code')} msg={body.get('message')}")
            return None
    except Exception as e:
        print(f"  登录异常：{e}")
        return None


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("安全预警与应急响应（alert + 预测联动）")
    print("=" * 60)

    # ---------- 1. 加载数据 ----------
    print(f"\n[1/5] 加载数据：{DATA_PATH}")
    if not os.path.exists(DATA_PATH):
        print("错误：文件不存在，请检查 DATA_PATH。")
        return
    if not os.path.exists(PREPROCESSOR_PATH):
        print("错误：没找到 preprocessor.json，请先运行 train.py。")
        return

    X_raw, X_norm, node_ids, density_col_idx, norm_min, norm_max, tick_to_ts = load_data()
    n_ticks, n_nodes, n_feat = X_norm.shape
    print(f"      数据：{n_ticks} ticks × {n_nodes} 节点 × {n_feat} 特征")
    print(f"      最后时刻：{tick_to_ts.iloc[-1]}")

    # ---------- 2. 加载预测器 ----------
    print(f"\n[2/5] 加载预测模型：{MODEL_DIR}")
    if not os.path.exists(os.path.join(MODEL_DIR, "model_state.pt")):
        print("错误：没找到训练好的模型，请先运行 train.py 训练。")
        return
    dp = DensityPredictor.load(MODEL_DIR)
    window_size = dp.window_size
    pred_horizon = dp.pred_horizon
    print(f"      预测器加载成功（window={window_size}, horizon={pred_horizon}）")

    # ---------- 3. 预测未来密度 ----------
    print(f"\n[3/5] 用最近 {window_size} 个时间步（{window_size*TICK_SECONDS}秒）预测未来 {pred_horizon} 个时间步（{pred_horizon*TICK_SECONDS}秒）...")
    if n_ticks < window_size:
        print(f"错误：数据只有 {n_ticks} 个时间点，少于窗口长度 {window_size}。")
        return

    last_window = X_norm[-window_size:]                    # (窗口, 节点, 特征)
    X_input = last_window.transpose(1, 0, 2)[None]         # (1, 节点, 窗口, 特征)
    y_pred_norm = dp.predict(X_input)                      # (1, 节点, 预测步长)

    # 逆归一化，还原成真实密度
    lo, hi = norm_min[density_col_idx], norm_max[density_col_idx]
    y_pred = y_pred_norm * (hi - lo + 1e-8) + lo           # (1, 节点, 预测步长)
    y_pred = np.clip(y_pred[0], 0.0, None)                 # (节点, 预测步长)
    print(f"      预测完成，形状 (节点, 未来{pred_horizon}个时间步={pred_horizon*TICK_SECONDS}秒)")

    # ---------- 4. 拟合检测器 + 联动检测 ----------
    print("\n[4/5] 拟合 CongestionDetector 基线（用前 75% 历史）...")
    cd = CongestionDetector(
        density_feature_idx=DENSITY_FEATURE_IDX,
        gate_status_feature_idx=GATE_STATUS_FEATURE_IDX,
        gate_flow_feature_idx=GATE_FLOW_FEATURE_IDX,
    )
    X_hist = X_norm[: int(n_ticks * 0.75)]   # 前 75% 作为"正常"基线
    cd.fit(X_hist)
    print(f"      已为 {n_nodes} 个节点建立统计基线")

    print(f"\n[4/5] 检测最近 {RECENT_FRAMES} 帧（传入预测结果做拥堵确认）...")
    X_recent = X_norm[-RECENT_FRAMES:]       # 最近 RECENT_FRAMES 帧（归一化的完整特征）
    events = cd.predict(X_recent, X_pred=y_pred)   # ← 关键联动：传入预测
    print(f"      检测到 {len(events)} 个异常事件")

    # ---------- 5. 分级 + 组装入库 JSON ----------
    print("\n[5/5] 分级并组装预警事件 ...")
    alerts = classify(events, node_ids)

    # 若没有真实预警但开了调试模式，构造一条演示预警用于验证后端链路
    if len(alerts) == 0 and DEMO_MODE:
        print("\n  当前无真实预警（L0 正常）。")
        print("  DEMO_MODE=True，构造一条演示预警验证后端链路 ...")
        demo_node = node_ids[0]
        alerts = [{
            "event_id": f"EVT-{datetime.now():%Y%m%d}-DEMO",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": "L2",
            "type": "congestion",
            "node_id": demo_node,
            "node_name": NODE_NAME_MAP.get(demo_node, demo_node),
            "current_density": 0.9,
            "threshold_density": 0.8,
            "predicted_duration_min": 8,
            "suggested_action": "门闸限流50%",
            "status": "active",
        }]
        print(f"  演示预警 node_id={demo_node}（可在配置区改 node_id）")
    elif len(alerts) == 0:
        print("\n  当前无预警（L0 正常），未发送任何请求。")
        return

    print("\n" + "=" * 60)
    print("预警事件（交付给后端）")
    print("=" * 60)
    for a in alerts:
        print(f"\n  [{a['level']}] {a['type']} @ {a['node_id']}")
        print(f"      时间      : {a['timestamp']}")
        print(f"      当前密度  : {a['current_density']}")
        print(f"      阈值 P95  : {a['threshold_density']}")
        print(f"      预计持续  : {a['predicted_duration_min']} 分钟")
        print(f"      处置建议  : {a['suggested_action']}")
    print("\n" + "=" * 60)

    # 保存 JSON
    out_path = os.path.join(BASE_DIR, "checkpoints", "alerts.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)
    print(f"\n预警已保存：{out_path}")

    # 提交后端：先登录拿 token，再逐个 POST
    print("\n[提交后端] 先登录获取 token ...")
    token = login_get_token()

    if token is None:
        print("  登录失败，跳过提交（预警已保存到 alerts.json）。")
    else:
        import requests
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
        for a in alerts:
            try:
                resp = requests.post(ALERT_API, json=a, headers=headers, timeout=5)
                body = resp.json()
                if resp.status_code == 200 and body.get("code") == 0:
                    print(f"  [POST成功] {a['event_id']} → {body['data']['alertId']}")
                else:
                    print(f"  [POST失败] {a['event_id']} → code={body.get('code')} "
                          f"msg={body.get('message')}")
            except Exception as e:
                print(f"  [POST异常] {a['event_id']} → {e}")


if __name__ == "__main__":
    main()
