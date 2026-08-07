# -*- coding: utf-8 -*-
"""alert_replay_check.py —— 预警规则回放验收脚本（只读，不修改任何文件）

用现有 data/engine_timeseries.csv 按新版检测规则重放 SecurityService 的
检测流程（检测器拟合 → 逐帧 detect），并断言：

  1. 不乱报：拥堵事件密度必须落在对应绝对档位
             （L1∈[0.3,0.6)、L2∈[0.6,0.9)、L3≥0.9），滞留事件密度 ≥ 0.5
  2. 不漏报：节点密度连续 ≥ confirm_frames 帧达到 0.3/0.6 时，该节点
             "报警中"状态必须已建立（= 已发过对应等级事件）
  3. 不重复：事件触发时该 (节点, 类型) 不得处于同等级报警中
             （仅允许升级触发新事件）
  4. 边界：  密度 < 0.3 的节点帧不产生任何拥堵/滞留事件

用法：python simulation/alert_replay_check.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model import CongestionDetector  # noqa: E402
from service import SecurityService  # noqa: E402

CSV_PATH = _HERE.parent / "data" / "engine_timeseries.csv"
PREPROCESSOR = _HERE / "checkpoints" / "preprocessor.json"

DENSITY_FEATURE_IDX = 0
GATE_STATUS_FEATURE_IDX = 3
GATE_FLOW_FEATURE_IDX = 4
RECENT_FRAMES = 10
HIST_RATIO = 0.75
SEV_RANK = {"L1": 1, "L2": 2, "L3": 3}


def build_service():
    """构造一个不加载预测模型的最小 SecurityService（只复用数据读取/特征构造）。"""
    svc = object.__new__(SecurityService)
    svc.csv_path = str(CSV_PATH)
    with open(PREPROCESSOR, "r", encoding="utf-8") as f:
        svc.pp = json.load(f)
    svc.node_ids = svc.pp["node_ids"]
    svc.state_feature_count = svc.pp.get(
        "state_feature_count", len(svc.pp["feature_columns"]))
    svc.state_features = svc.pp["feature_columns"][: svc.state_feature_count]
    svc.norm_min = {int(k): v for k, v in svc.pp["norm_min"].items()}
    svc.norm_max = {int(k): v for k, v in svc.pp["norm_max"].items()}
    svc.density_col_idx = svc.pp["density_col_idx"]
    return svc


def replay(scenario_name, fit_slice, svc, df, ticks, X_raw):
    """按场景回放检测器，返回 (events_by_step, episode_snapshots, errors, summary)。"""
    errors = []
    cd = CongestionDetector(
        density_feature_idx=DENSITY_FEATURE_IDX,
        gate_status_feature_idx=GATE_STATUS_FEATURE_IDX,
        gate_flow_feature_idx=GATE_FLOW_FEATURE_IDX,
    )
    cd.fit(X_raw[fit_slice])

    n_nodes = cd.n_nodes_
    density = X_raw[:, :, DENSITY_FEATURE_IDX]  # (T, N) 原始密度
    l1, l2, l3 = cd.abs_levels

    events_by_step = []
    eps_active = []          # 每步 (node,type) → 报警中等级
    steps = range(RECENT_FRAMES - 1, len(X_raw))

    for t in steps:
        pre_active = {k: v["severity"] for k, v in cd._active_episodes.items()}
        X_recent = X_raw[t - RECENT_FRAMES + 1: t + 1]
        events = cd.predict(X_recent, X_pred=None)
        for e in events:
            e["step"] = t
            e["tick"] = int(ticks[t])
            e["node_name"] = svc.node_ids[e["node_id"]]
            key = (e["node_id"], e["type"])
            # 断言 3：不重复（同等级报警中不得触发；仅允许升级）
            if key in pre_active:
                if SEV_RANK[e["severity"]] <= SEV_RANK[pre_active[key]]:
                    errors.append(
                        f"[{scenario_name}] step{t} {e['node_name']} {e['type']}"
                        f" {e['severity']} 在 {pre_active[key]} 报警中重复触发")
        events_by_step.append(events)
        eps_active.append(dict(cd._active_episodes))

    density_t = density[RECENT_FRAMES - 1:]  # 对齐 steps

    for i, t in enumerate(steps):
        eps = eps_active[i]
        for n in range(n_nodes):
            d = float(density_t[i, n])
            name = svc.node_ids[n]
            # 断言 1：不乱报（等级与绝对档位一致 / 滞留密度下限）
            for e in events_by_step[i]:
                if e["node_id"] != n:
                    continue
                ed = float(e["current_density"])
                if e["type"] == "congestion":
                    if e["severity"] == "L1":
                        ok = l1 <= ed < l2
                    elif e["severity"] == "L2":
                        ok = l2 <= ed < l3
                    else:
                        ok = ed >= l3
                    if not ok:
                        errors.append(
                            f"[{scenario_name}] step{t} {name} {e['severity']}"
                            f" 密度 {ed:.4f} 与档位不符")
                elif e["type"] == "loitering":
                    if ed < cd.loitering_density_min:
                        errors.append(
                            f"[{scenario_name}] step{t} {name} 滞留事件"
                            f" 密度 {ed:.4f} < 下限 {cd.loitering_density_min}")

        # 断言 2：不漏报（片段级）——每段"密度 ≥ th 且长度 ≥ confirm_frames"
        # 的拥堵期（低谷 < confirm_frames 帧视为同一期，覆盖退出/重确认窗口）
        # 内必须触发过至少一条该节点该档位及以上事件。
        steps_list = list(steps)
        fired = {n: [] for n in range(n_nodes)}  # node → 事件 step 列表（L1+）
        for i, t in enumerate(steps_list):
            for e in events_by_step[i]:
                if e["type"] == "congestion":
                    fired[e["node_id"]].append(i)
        for n in range(n_nodes):
            name = svc.node_ids[n]
            for th, label in ((l1, "L1"), (l2, "L2")):
                above = density_t[:, n] >= th
                periods = []
                s = None
                for i in range(len(above)):
                    if above[i]:
                        if s is None:
                            s = i
                    else:
                        if s is not None:
                            periods.append((s, i - 1))
                            s = None
                if s is not None:
                    periods.append((s, len(above) - 1))
                # 合并低谷 < confirm_frames 帧的相邻期（同一拥堵期）
                merged = []
                for ps, pe in periods:
                    if merged and ps - merged[-1][1] - 1 < cd.confirm_frames:
                        merged[-1] = (merged[-1][0], pe)
                    else:
                        merged.append((ps, pe))
                for ps, pe in merged:
                    # 片段内最长连续 ≥th 子段（低谷合并后可能有断续）
                    run_len = best = 0
                    for i in range(ps, pe + 1):
                        if above[i]:
                            run_len += 1
                            best = max(best, run_len)
                        else:
                            run_len = 0
                    if best < cd.confirm_frames:
                        continue  # 无连续 confirm_frames 帧达阈，不要求
                    evs = [i for i in fired[n] if ps <= i <= pe]
                    if not evs:
                        errors.append(
                            f"[{scenario_name}] {name} 密度 ≥{label}({th})"
                            f" 持续 {pe - ps + 1} 帧（step {ps}~{pe}）但无报警（疑似漏报）")
    return events_by_step, errors


def summarize(scenario_name, events_by_step, svc):
    from collections import Counter
    cnt = Counter()
    per_node = {}
    for evs in events_by_step:
        for e in evs:
            cnt[(e["type"], e["severity"])] += 1
            per_node.setdefault((e["node_name"], e["type"]), []).append(
                (e["severity"], e["current_density"], e["tick"]))
    print(f"\n== {scenario_name} ==")
    if not cnt:
        print("  0 条事件")
    for (typ, sev), n in sorted(cnt.items()):
        print(f"  {sev} {typ}: {n} 条")
    for (name, typ), rows in sorted(per_node.items()):
        sevs = " -> ".join(f"{s}@{d:.2f}(t{tick})" for s, d, tick in rows[:6])
        print(f"  {name}: {sevs}")
    return cnt


def main():
    if not CSV_PATH.exists():
        print(f"CSV 不存在: {CSV_PATH}")
        return 1
    svc = build_service()
    df = svc._read_csv()
    X_raw, X_norm, tick_to_ts = svc._build_feature_matrix(df)
    ticks = sorted(df["tick"].unique())
    print(f"CSV: {len(ticks)} 帧 × {len(svc.node_ids)} 节点 | "
          f"密度范围 {X_raw[:, :, 0].min():.4f} ~ {X_raw[:, :, 0].max():.4f}")

    n = X_raw.shape[0]
    scenarios = [
        ("稳态基线（前75%拟合）", slice(0, int(n * HIST_RATIO))),
        ("冷启动基线（前3帧拟合）", slice(0, min(3, n))),
    ]
    all_errors = []
    for name, fit_slice in scenarios:
        events_by_step, errors = replay(name, fit_slice, svc, df, ticks, X_raw)
        summarize(name, events_by_step, svc)
        all_errors.extend(errors)

    # ---- 回归：日志场景（tick 89400 时 canteen≈1.04 应报 L3，修复前为 0 条） ----
    last_tick = ticks[-1]
    tail = df[df["tick"] == last_tick]
    hot = tail.sort_values("density", ascending=False).head(3)
    hot_names = set(hot["node_id"])
    print("\n== 回归检查：末帧热节点 ==")
    for _, r in hot.iterrows():
        print(f"  {r['node_id']}: density={r['density']:.3f} level={r['level']}")

    # 用稳态场景的检测器完整回放一遍，检查末帧附近热点是否已报警
    cd = CongestionDetector(
        density_feature_idx=DENSITY_FEATURE_IDX,
        gate_status_feature_idx=GATE_STATUS_FEATURE_IDX,
        gate_flow_feature_idx=GATE_FLOW_FEATURE_IDX,
    )
    cd.fit(X_raw[: int(n * HIST_RATIO)])
    last_events = {}
    for t in range(RECENT_FRAMES - 1, n):
        events = cd.predict(X_raw[t - RECENT_FRAMES + 1: t + 1], X_pred=None)
        for e in events:
            last_events.setdefault(svc.node_ids[e["node_id"]], []).append(e)
        # 仅保留最近 RECENT_FRAMES 步内的事件（回归只关心当前热点时段）
        if t >= n - RECENT_FRAMES:
            last_events = {
                k: [e for e in v if e.get("step", t) >= n - RECENT_FRAMES]
                for k, v in last_events.items()
            }
    reg_ok = True
    for name in sorted(hot_names):
        evs = last_events.get(name, [])
        sevs = sorted({e["severity"] for e in evs})
        if sevs:
            print(f"  [OK] {name} 最近 {RECENT_FRAMES} 帧内已触发 {sevs}，"
                  f"密度={evs[-1]['current_density']:.3f}")
        else:
            print(f"  [FAIL] {name} 密度≈1.0 但最近 {RECENT_FRAMES} 帧内无预警（潜在漏报）")
            all_errors.append(f"回归检查失败：{name} 末帧热点无预警")
            reg_ok = False

    print("\n" + "=" * 60)
    if all_errors:
        print(f"共 {len(all_errors)} 处不符合项：")
        for msg in all_errors[:20]:
            print("  -", msg)
        return 1
    print("全部断言通过 ✓（不乱报 / 不漏报 / 不重复）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
