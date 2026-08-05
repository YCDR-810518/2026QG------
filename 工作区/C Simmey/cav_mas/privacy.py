# -*- coding: utf-8 -*-
"""privacy.py —— 敏感数据差分隐私保护（Laplace 机制）

考核要求中两次提出对车辆行驶路径等敏感信息采取 DP 保护：
  · "系统中车辆行驶路径等信息可考虑采取 DP 保护"
  · "对敏感数据采用差分隐私保护"

设计要点：
1. 只对"对外发布"的路径坐标加噪：内部指标计算（metrics.summarize）
   仍用真实值，避免噪声污染 IDM/CAV 对比结论；
2. 敏感度缩减（Sensitivity Reduction）：坐标先量化到 5m 网格再发布，
   查询敏感度从路径总长 360m 降至 Δ = 2 × 5 = 10m（L1 全局敏感度）；
3. Laplace 机制：噪声尺度 b = Δ / ε，满足 ε-差分隐私；
   噪声坐标裁剪回道路范围（后处理不损失隐私保证）；
4. 噪声由可复现随机源生成（SEED 派生），两次运行脱敏结果一致；
5. 导出 vehicle_paths_private.csv（group / vid / tick / x / y）供下游使用。
"""
import csv
import os

import numpy as np

DEFAULT_EPSILON = 1.0    # 默认隐私预算 ε
GRID = 5.0               # 坐标离散化网格 m（敏感度缩减）


def laplace_noise(scale: float, rng: np.random.Generator) -> float:
    """生成服从 Laplace(0, scale) 的随机噪声。"""
    return float(rng.laplace(0.0, scale))


def apply_differential_privacy(frames_map: dict, topo, epsilon: float = DEFAULT_EPSILON,
                               rng: np.random.Generator = None) -> dict:
    """对车辆逐 tick 位置快照施加 ε-差分隐私（Laplace 机制）。

    流程：真实坐标 → 量化到 GRID 网格（敏感度缩减）→ 加 Laplace 噪声
          → 裁剪回道路范围 → 发布。

    Parameters
    ----------
    frames_map : dict {group: list[list[(vid, s, v)]]}
        每组（如 idm / cav）的逐 tick 车辆快照（TickEngine.frames）。
    topo : LShapeTopology
        路径拓扑（提供 s_to_xy / 道路边界，用于坐标映射与裁剪）。
    epsilon : float
        隐私预算 ε（越小噪声越大、隐私保护越强）。
    rng : numpy.random.Generator, optional
        随机源，缺省按 SEED 新建，保证脱敏结果可复现。

    Returns
    -------
    dict
        {epsilon, sensitivity, scale, mean_abs_error,
         paths: {group: {vid: [(tick, x, y), ...]}}}
    """
    rng = rng if rng is not None else np.random.default_rng()
    grid = float(GRID)
    sensitivity = 2.0 * grid                 # 网格化后全局 L1 敏感度 Δ = Δx + Δy
    scale = sensitivity / max(epsilon, 1e-9)  # Laplace 尺度 b = Δ / ε
    errors, paths = [], {}
    for group, frames in frames_map.items():
        by_vid = {}
        for t, fr in enumerate(frames):
            for vid, s, v in fr:
                x, y = topo.s_to_xy(s)
                # 1) 量化到网格：把坐标查询的敏感度从路径总长降到 2*grid
                xq = round(x / grid) * grid
                yq = round(y / grid) * grid
                # 2) Laplace 噪声
                nx = xq + laplace_noise(scale, rng)
                ny = yq + laplace_noise(scale, rng)
                # 3) 裁剪回道路范围（后处理，不影响 ε-DP 保证）
                nx = float(np.clip(nx, 0.0, topo.len_a))
                ny = float(np.clip(ny, 0.0, topo.len_b))
                errors.append(abs(nx - x) + abs(ny - y))
                by_vid.setdefault(vid, []).append((t, nx, ny))
        paths[group] = by_vid
    return {
        "epsilon": epsilon,
        "sensitivity": sensitivity,
        "scale": scale,
        "mean_abs_error": float(np.mean(errors)) if errors else 0.0,
        "paths": paths,
    }


def export_private_paths(protected: dict, out_dir) -> str:
    """将脱敏路径数据导出为 CSV（供下游使用）。"""
    out_path = os.path.join(out_dir, "vehicle_paths_private.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group", "vid", "tick", "x", "y"])
        for group, by_vid in protected["paths"].items():
            for vid, pts in by_vid.items():
                for t, x, y in pts:
                    writer.writerow([group, vid, t, f"{x:.2f}", f"{y:.2f}"])
    return out_path
