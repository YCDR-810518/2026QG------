# -*- coding: utf-8 -*-
"""
service.py — 预测 + 预警 封装服务（PyTorch 版本，供成员F调用）
==============================================================
把 density 预测和预警打包成一个可 import 的服务，供成员F在
仿真引擎里定时调用：CSV 不断追加数据 → 每隔 interval 秒预测并预警一次。

与 mindspore/service.py 功能完全一致，仅底层模型为 PyTorch（.pt 权重）。

典型用法（F 侧）：
    from service import SecurityService

    svc = SecurityService.from_config(
        csv_path=r"D:\\...\\density_series.csv",   # F 持续写入的 CSV
        backend_base="http://192.168.1.114:8100",  # 后端地址
        demo_mode=True,                            # 测试模式：无预警也发演示预警
        interval_seconds=60,                       # 轮询间隔
    )
    svc.run_loop()     # 阻塞式定时循环，Ctrl+C 停止

    # 或只跑一次：
    result = svc.check_alerts()   # 返回 {"predictions":..., "alerts":[...], "posted":...}

说明：
  - 预测：DensityPredictor 用最近 window_size 个时间步（60秒）预测未来 pred_horizon 个时间步（30秒）密度
  - 预警：CongestionDetector 用最近 RECENT_FRAMES 帧检测三类异常，
          传入预测结果做拥堵确认，分级后 POST 到后端
  - 检测器基线：构造时用 CSV 历史前 75% 拟合一次，之后不再重拟
  - CSV 增量：每次 check_alerts 都会重新读 CSV 取最新数据，无需重启
"""

import os
import json
import time
import uuid
from datetime import datetime

import numpy as np
import pandas as pd

from model import CongestionDetector, DensityPredictor
from data_config import LEVEL_MAP, GATE_STATUS_MAP, SIGNAL_STATUS_MAP

# ============================================================
# 配置（默认值，F 可通过 from_config 覆盖）
# ============================================================

# 检测器参数（特征列索引对齐当前数据列序）
DENSITY_FEATURE_IDX = 0    # density
GATE_STATUS_FEATURE_IDX = 3  # gate_status
GATE_FLOW_FEATURE_IDX = 4    # gate_flow_rate

# 用最近多少帧做检测（多帧才能检测滞留）
RECENT_FRAMES = 10

# 检测器基线用的历史比例（前 75% 作为"正常"基线）
HIST_RATIO = 0.75

# 时间粒度：1 tick = TICK_SECONDS 秒（引擎 10 秒一个采样点）
TICK_SECONDS = 10

# 节点ID → 区域名（可选；不填就用节点ID本身）
NODE_NAME_MAP = {}


# ============================================================
# 服务类
# ============================================================

class SecurityService:
    """
    预测 + 预警封装服务（PyTorch 版）。

    Parameters
    ----------
    csv_path : str
        F 持续写入的 density_series.csv 路径。
    model_dir : str
        训练产出的模型目录（含 model_state.pt + config.json）。
    preprocessor_path : str
        归一化参数文件路径（preprocessor.json）。
    backend_base : str
        后端服务地址（如 http://192.168.1.114:8100）。
    demo_mode : bool
        True：即使没有真实预警也构造演示预警发送；False：只有真实预警才发。
    login_username / login_password : str
        后端登录账号密码（后端会变则改这里）。
    interval_seconds : float
        轮询间隔（秒）。
    """

    def __init__(
        self,
        csv_path: str,
        model_dir: str,
        preprocessor_path: str,
        backend_base: str = "",
        demo_mode: bool = True,
        login_username: str = "ZTZT",
        login_password: str = "Zzt20070124",
        interval_seconds: float = 60.0,
    ):
        self.csv_path = csv_path
        self.model_dir = model_dir
        self.preprocessor_path = preprocessor_path
        self.backend_base = backend_base.rstrip("/")
        self.login_url = self.backend_base + "/api/v1/admin/login" if self.backend_base else ""
        self.alert_api = self.backend_base + "/api/v1/security/alerts/create" if self.backend_base else ""
        self.demo_mode = demo_mode
        self.login_username = login_username
        self.login_password = login_password
        self.interval_seconds = interval_seconds

        # 加载模型与预处理器
        if not os.path.exists(model_dir):
            raise FileNotFoundError(f"模型目录不存在: {model_dir}")
        if not os.path.exists(preprocessor_path):
            raise FileNotFoundError(f"preprocessor.json 不存在: {preprocessor_path}")

        self.dp = DensityPredictor.load(model_dir)
        with open(preprocessor_path, "r", encoding="utf-8") as f:
            self.pp = json.load(f)

        self.node_ids = self.pp["node_ids"]
        self.norm_min = {int(k): v for k, v in self.pp["norm_min"].items()}
        self.norm_max = {int(k): v for k, v in self.pp["norm_max"].items()}
        self.density_col_idx = self.pp["density_col_idx"]
        self.state_feature_count = self.pp.get("state_feature_count", len(self.pp["feature_columns"]))
        self.state_features = self.pp["feature_columns"][: self.state_feature_count]
        self.window_size = self.dp.window_size
        self.pred_horizon = self.dp.pred_horizon

        # 检测器（基线在 load_and_fit_detector 里拟合）
        self.cd = None

    # ------------------------------------------------------------------
    # 构造器
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, csv_path, backend_base="", demo_mode=True,
                    interval_seconds=60.0, **kwargs):
        """从配置创建服务。模型路径默认取本文件同目录 checkpoints。"""
        base = os.path.dirname(os.path.abspath(__file__))
        model_dir = kwargs.pop("model_dir", os.path.join(base, "checkpoints", "density_model"))
        preprocessor_path = kwargs.pop(
            "preprocessor_path", os.path.join(base, "checkpoints", "preprocessor.json")
        )
        return cls(
            csv_path=csv_path,
            model_dir=model_dir,
            preprocessor_path=preprocessor_path,
            backend_base=backend_base,
            demo_mode=demo_mode,
            interval_seconds=interval_seconds,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # 数据读取与特征构造
    # ------------------------------------------------------------------

    def _read_csv(self):
        """读 CSV，字符串编码，校验节点一致，返回排好序的 df。"""
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"CSV 不存在: {self.csv_path}")
        df = pd.read_csv(self.csv_path, dtype={"node_id": str})
        # 字符串枚举列 → 数值（空值填 0）
        df["level"] = df["level"].map(LEVEL_MAP).fillna(0).astype(int)
        df["gate_status"] = df["gate_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
        df["door_status"] = df["door_status"].map(GATE_STATUS_MAP).fillna(0).astype(int)
        df["signal_status"] = df["signal_status"].map(SIGNAL_STATUS_MAP).fillna(0).astype(int)
        df["ts"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values(["node_id", "tick"]).reset_index(drop=True)

        data_nodes = sorted(df["node_id"].unique())
        missing = [n for n in self.node_ids if n not in data_nodes]
        if missing:
            raise ValueError(f"CSV 节点与训练时不一致，缺少: {missing[:10]}")
        return df

    def _build_feature_matrix(self, df):
        """构造 (n_ticks, n_nodes, n_features) 特征矩阵 + tick_to_ts。"""
        ticks = sorted(df["tick"].unique())
        n_t, n_n = len(ticks), len(self.node_ids)
        n_f = len(self.pp["feature_columns"])

        X_state = np.zeros((n_t, n_n, self.state_feature_count), dtype=np.float32)
        for f, col in enumerate(self.state_features):
            piv = df.pivot_table(index="tick", columns="node_id", values=col)
            piv = piv.reindex(index=ticks, columns=self.node_ids)
            X_state[:, :, f] = piv.fillna(0.0).to_numpy(dtype=np.float32)

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

        X_raw = np.concatenate([X_state, X_time], axis=-1).astype(np.float32)

        X_norm = X_raw.copy()
        for c in self.norm_min.keys():
            lo, hi = self.norm_min[c], self.norm_max[c]
            X_norm[:, :, c] = (X_norm[:, :, c] - lo) / (hi - lo + 1e-8)

        return X_raw, X_norm, tick_to_ts

    def load_and_fit_detector(self):
        """
        用 CSV 历史前 75% 拟合检测器基线。应在启动时调用一次。
        帧数不足时返回 None（调用方跳过本轮），而不是抛异常。
        """
        df = self._read_csv()
        _, X_norm, _ = self._build_feature_matrix(df)
        n_ticks = X_norm.shape[0]
        if n_ticks < 3:
            return None

        self.cd = CongestionDetector(
            density_feature_idx=DENSITY_FEATURE_IDX,
            gate_status_feature_idx=GATE_STATUS_FEATURE_IDX,
            gate_flow_feature_idx=GATE_FLOW_FEATURE_IDX,
        )
        X_hist = X_norm[: int(n_ticks * HIST_RATIO)]
        self.cd.fit(X_hist)
        return self.cd

    # ------------------------------------------------------------------
    # 预测
    # ------------------------------------------------------------------

    def predict(self):
        """
        读取 CSV 最新数据，预测未来 pred_horizon 个时间步（30秒）各节点密度。

        Returns
        -------
        dict: {"timestamp", "period", "density_stats": {node_id: float}}
        """
        y_pred, tick_to_ts, n_ticks = self._predict_raw()
        agg_values = y_pred.max(axis=1)   # 未来窗口峰值

        density_stats = {}
        for i, node in enumerate(self.node_ids):
            key = NODE_NAME_MAP.get(node, node)
            density_stats[key] = float(round(agg_values[i], 4))

        last_ts = tick_to_ts.iloc[-1]
        pred_start = last_ts + pd.Timedelta(seconds=TICK_SECONDS)
        pred_end = last_ts + pd.Timedelta(seconds=self.pred_horizon * TICK_SECONDS)

        return {
            "timestamp": str(last_ts),
            "period": {"start": str(pred_start), "end": str(pred_end)},
            "density_stats": density_stats,
        }

    def _predict_raw(self):
        """
        内部：读 CSV 最新数据，返回 (y_pred, tick_to_ts, n_ticks)。
        y_pred: (n_nodes, pred_horizon) 逆归一化后的密度预测。
        """
        df = self._read_csv()
        _, X_norm, tick_to_ts = self._build_feature_matrix(df)
        n_ticks = X_norm.shape[0]

        if n_ticks < self.window_size:
            raise ValueError(
                f"CSV 只有 {n_ticks} 帧，少于窗口 {self.window_size}，请先积累数据"
            )

        last_window = X_norm[-self.window_size:]
        X_input = last_window.transpose(1, 0, 2)[None]
        y_pred_norm = self.dp.predict(X_input)

        lo, hi = self.norm_min[self.density_col_idx], self.norm_max[self.density_col_idx]
        y_pred = y_pred_norm * (hi - lo + 1e-8) + lo
        y_pred = np.clip(y_pred[0], 0.0, None)     # (节点, 预测步长)
        return y_pred, tick_to_ts, n_ticks

    # ------------------------------------------------------------------
    # 预警
    # ------------------------------------------------------------------

    def _login_get_token(self):
        """登录后端拿 token。未配置后端返回 None。"""
        if not self.login_url:
            return None
        import requests
        try:
            resp = requests.post(
                self.login_url,
                json={"username": self.login_username, "password": self.login_password},
                timeout=5,
            )
            body = resp.json()
            if resp.status_code == 200 and body.get("code") == 0:
                return body["data"]["token"]
            print(f"  登录失败：code={body.get('code')} msg={body.get('message')}")
            return None
        except Exception as e:
            print(f"  登录异常：{e}")
            return None

    def _classify(self, events):
        """检测事件 → 入库格式（对齐后端接口字段）。
        阈值/持续时间按事件类型映射：
          - congestion  : threshold_p95 → 阈值；predicted_duration_min → 持续
          - loitering   : rate_threshold → 阈值；sustained_frames → 持续
          - gate_anomaly: expected_flow/gate_flow_rate → 参考阈值；无持续
        """
        alerts = []
        for ev in events:
            node_idx = ev["node_id"]
            node_id = self.node_ids[node_idx]

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

            alerts.append({
                "event_id": f"EVT-{datetime.now():%Y%m%d}-{str(uuid.uuid4())[:4].upper()}",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "level": level,
                "type": ev["type"],
                "node_id": node_id,
                "node_name": NODE_NAME_MAP.get(node_id, node_id),
                "current_density": ev.get("current_density", None),
                "threshold_density": threshold,
                "predicted_duration_min": duration,
                "suggested_action": suggested,
                "status": "active",
            })
        return alerts

    def check_alerts(self):
        """
        执行一次 预测 + 预警 + （可选）POST 后端。

        Returns
        -------
        dict: {"timestamp", "predictions", "alerts", "posted", "skipped", "reason"}
        数据不足时 skipped=True，其他字段为空。
        """
        if self.cd is None:
            fitted = self.load_and_fit_detector()
            if fitted is None:
                # 数据太少，基线都拟合不了 → 跳过本轮
                n_ticks = self._count_ticks()
                return {
                    "skipped": True,
                    "reason": f"CSV 帧数不足（{n_ticks} 帧），无法拟合检测器基线，跳过本轮",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "predictions": None, "alerts": [], "posted": [],
                }

        # 1. 读一次 CSV，构造特征矩阵（预测 + 检测共用同一份数据）
        df = self._read_csv()
        _, X_norm, tick_to_ts = self._build_feature_matrix(df)
        n_ticks = X_norm.shape[0]
        if n_ticks < self.window_size:
            # 帧数不足窗口 → 跳过本轮，不抛异常
            return {
                "skipped": True,
                "reason": f"CSV 只有 {n_ticks} 帧，少于窗口 {self.window_size}，跳过本轮",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "predictions": None, "alerts": [], "posted": [],
            }

        # 2. 预测（用于拥堵确认 + 交付）
        last_window = X_norm[-self.window_size:]
        X_input = last_window.transpose(1, 0, 2)[None]
        y_pred_norm = self.dp.predict(X_input)
        lo, hi = self.norm_min[self.density_col_idx], self.norm_max[self.density_col_idx]
        y_pred = np.clip(y_pred_norm * (hi - lo + 1e-8) + lo, 0.0, None)[0]  # (节点, 预测步长)

        # 组装交付 dict
        agg_values = y_pred.max(axis=1)
        density_stats = {}
        for i, node in enumerate(self.node_ids):
            key = NODE_NAME_MAP.get(node, node)
            density_stats[key] = float(round(agg_values[i], 4))
        last_ts = tick_to_ts.iloc[-1]
        pred = {
            "timestamp": str(last_ts),
            "period": {
                "start": str(last_ts + pd.Timedelta(seconds=TICK_SECONDS)),
                "end": str(last_ts + pd.Timedelta(seconds=self.pred_horizon * TICK_SECONDS)),
            },
            "density_stats": density_stats,
        }

        # 3. 检测（最近 RECENT_FRAMES 帧，传入预测做拥堵确认）
        X_recent = X_norm[-RECENT_FRAMES:]
        events = self.cd.predict(X_recent, X_pred=y_pred)

        # 5. 分级
        alerts = self._classify(events)

        # 6. demo 模式：无真实预警时构造一条演示预警
        if len(alerts) == 0 and self.demo_mode:
            demo_node = self.node_ids[0]
            alerts = [{
                "event_id": f"EVT-{datetime.now():%Y%m%d}-DEMO",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "level": "L2",
                "type": "congestion",
                "node_id": demo_node,
                "node_name": NODE_NAME_MAP.get(demo_node, demo_node),
                "current_density": 0.9,
                "threshold_density": 0.8,
                "predicted_duration_min": 5,   # L2 默认 5 个时间步（=50秒）
                "suggested_action": "门闸限流50%",
                "status": "active",
            }]

        # 7. POST 后端
        posted = []
        if alerts and self.alert_api:
            token = self._login_get_token()
            if token:
                import requests
                headers = {"Authorization": f"Bearer {token}",
                           "Content-Type": "application/json"}
                for a in alerts:
                    try:
                        resp = requests.post(self.alert_api, json=a, headers=headers, timeout=5)
                        body = resp.json()
                        if resp.status_code == 200 and body.get("code") == 0:
                            posted.append({"event_id": a["event_id"],
                                           "alertId": body["data"]["alertId"]})
                        else:
                            print(f"  [POST失败] {a['event_id']} → "
                                  f"code={body.get('code')} msg={body.get('message')}")
                    except Exception as e:
                        print(f"  [POST异常] {a['event_id']} → {e}")

        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "predictions": pred,
            "alerts": alerts,
            "posted": posted,
            "skipped": False,
            "reason": "",
        }

    def _count_ticks(self):
        """返回 CSV 当前有多少个唯一 tick（不构造特征矩阵，轻量）。"""
        if not os.path.exists(self.csv_path):
            return 0
        df = pd.read_csv(self.csv_path, usecols=["tick"])
        return int(df["tick"].nunique())

    # ------------------------------------------------------------------
    # 定时循环
    # ------------------------------------------------------------------

    def run_loop(self, interval_seconds: float = None):
        """
        定时循环：每隔 interval_seconds 秒执行一次 check_alerts。
        Ctrl+C 停止。interval 默认用构造时的 interval_seconds。
        """
        interval = interval_seconds or self.interval_seconds
        print(f"启动预测+预警循环：间隔 {interval} 秒，Ctrl+C 停止")
        print(f"  CSV: {self.csv_path}")
        print(f"  后端: {self.alert_api or '(未配置)'} | demo_mode: {self.demo_mode}")

        if self.cd is None:
            print("  首次拟合检测器基线 ...")
            self.load_and_fit_detector()

        while True:
            try:
                print(f"\n[{datetime.now():%H:%M:%S}] 执行一次预测+预警 ...")
                result = self.check_alerts()

                if result.get("skipped"):
                    # 数据不足，跳过本轮，继续等 CSV 累积
                    print(f"  跳过本轮：{result.get('reason', '')}")
                else:
                    pred = result["predictions"]
                    period = pred["period"]
                    stats = pred["density_stats"]
                    # 找出密度最高的 3 个节点，帮助直观看到预测结果
                    top3 = sorted(stats.items(), key=lambda kv: -kv[1])[:3]
                    top3_str = ", ".join(f"{k}={v}" for k, v in top3)

                    print(f"  预测时段：{period['start']} ~ {period['end']}（未来{self.pred_horizon}个时间步）")
                    print(f"  预测节点密度峰值 Top3：{top3_str}")
                    print(f"  预警 {len(result['alerts'])} 条 | 已提交后端 {len(result['posted'])} 条")
                    for a in result["alerts"]:
                        print(f"    [{a['level']}] {a['type']} @ {a['node_id']} "
                              f"密度={a.get('current_density')} 建议={a.get('suggested_action')}")
            except Exception as e:
                print(f"  本轮异常（跳过）：{e}")

            time.sleep(interval)


# ============================================================
# 直接运行：用默认配置启动循环（F 也可以 import 后自己控制）
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="预测+预警服务（供F调用，也可直接运行）")
    parser.add_argument("--csv", required=True, help="F 持续写入的 CSV 路径")
    parser.add_argument("--backend", default="", help="后端地址，如 http://192.168.1.114:8100")
    parser.add_argument("--demo", action="store_true", help="测试模式：无预警也发演示预警")
    parser.add_argument("--interval", type=float, default=60.0, help="轮询间隔秒")
    args = parser.parse_args()

    svc = SecurityService.from_config(
        csv_path=args.csv,
        backend_base=args.backend,
        demo_mode=args.demo,
        interval_seconds=args.interval,
    )
    svc.run_loop()
