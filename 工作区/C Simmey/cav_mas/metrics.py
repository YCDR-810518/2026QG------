# -*- coding: utf-8 -*-
"""metrics.py —— 实验指标汇总（拆分自 CAV+MAS.py 的 summarize）"""
import numpy as np


def summarize(logs: list) -> dict:
    """汇总指标：平均通行时间 / 平均速度 / 平均滞留 / 吞吐量。

    Parameters
    ----------
    logs : list[dict]
        TickEngine.run() 返回的全行程记录。

    Returns
    -------
    dict
        n/throughput：到达车辆数；
        tt_mean/tt_std：通行时间均值/标准差 s；
        speed_kmh：平均行程速度 km/h；
        delay_mean：平均滞留时间 s。
    """
    done = [log for log in logs if log["arrived"]]
    return {
        "n": len(done),
        "throughput": len(done),
        "tt_mean": float(np.mean([l["travel_time"] for l in done])),
        "tt_std": float(np.std([l["travel_time"] for l in done])),
        "speed_kmh": float(np.mean([l["avg_speed_kmh"] for l in done])),
        "delay_mean": float(np.mean([l["delay"] for l in done])),
    }
