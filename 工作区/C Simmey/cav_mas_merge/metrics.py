# -*- coding: utf-8 -*-
"""metrics.py —— 汇合场景指标汇总(FR-15 量化体系)

通用指标:平均通行时间 / 平均速度 / 平均滞留 / 吞吐量
汇合专项指标:
    merge_throughput   汇合点吞吐量(辆/min,跨过汇合点的车流率)
    queue_mean/max     汇合区平均/峰值排队长度
    slot_dev_mean      时隙偏差(实际到达汇合点 vs 协调器分配时刻,仅 CAV)
"""
import numpy as np


def summarize(logs: list) -> dict:
    """通用指标汇总(与 cav_mas.metrics 对齐)。"""
    done = [log for log in logs if log["arrived"]]
    return {
        "n": len(done),
        "throughput": len(done),
        "tt_mean": float(np.mean([l["travel_time"] for l in done])),
        "tt_std": float(np.std([l["travel_time"] for l in done])),
        "speed_kmh": float(np.mean([l["avg_speed_kmh"] for l in done])),
        "delay_mean": float(np.mean([l["delay"] for l in done])),
    }


def merge_metrics(logs: list, crossings: list, queue_series: list) -> dict:
    """汇合专项指标。"""
    if not crossings:
        return {"throughput_per_min": 0.0, "queue_mean": 0.0, "queue_max": 0.0,
                "slot_dev_mean": 0.0, "slot_dev_std": 0.0}
    last_cross = max(c["cross_tick"] for c in crossings)
    duration_min = max(last_cross, 1) / 60.0
    devs = [l["slot_dev"] for l in logs if l["slot_dev"] is not None]
    return {
        "throughput_per_min": round(len(crossings) / duration_min, 2),
        "queue_mean": round(float(np.mean(queue_series)), 2) if queue_series else 0.0,
        "queue_max": int(max(queue_series)) if queue_series else 0,
        "slot_dev_mean": round(float(np.mean(devs)), 2) if devs else 0.0,
        "slot_dev_std": round(float(np.std(devs)), 2) if devs else 0.0,
    }
