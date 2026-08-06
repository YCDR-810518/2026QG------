# -*- coding: utf-8 -*-
"""visualization.py —— 60° 夹角双路汇入单车道(CAV+MAS)对比实验绘图输出

    plot_travel_time_box     通行时间箱线图
    plot_speed_delay_bar     平均速度 / 滞留时间柱状图
    plot_space_time          双车道-时空轨迹图(A 橙 / B 蓝,汇合点横线)
    plot_queue_throughput    汇合区排队长度时序 + 汇合点吞吐量对比
    make_animation           双面板车辆运动动画 GIF(风格仿照 cav_mas_animation.gif:
                             白底 + 淡灰路缘线 + 黄色虚线中线 + 绿色箭头地标 +
                             车辆按速度 RdYlGn 着色 + 色条)
"""
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.patches import Arc

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_COLOR_A = "#f4a261"   # 主车道 A
_COLOR_B = "#2a9d8f"   # 汇入车道 B
_EDGE = "#f2f2f2"      # 路缘线(淡灰,仿 cav_mas_animation.gif)
_CENTER = "#e8c200"    # 车道中线(黄色虚线)
_GREEN = "#2e7d32"     # 地标箭头/文字


# ===========================================================================
def plot_travel_time_box(logs_idm, logs_cav, out_dir):
    """通行时间箱线图(FR-15 主指标)。"""
    data = [
        [l["travel_time"] for l in logs_idm if l["arrived"]],
        [l["travel_time"] for l in logs_cav if l["arrived"]],
    ]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    bp = ax.boxplot(data, tick_labels=["IDM 对照组（主路优先）", "CAV 实验组（时隙协同）"],
                    patch_artist=True, widths=0.5)
    bp["boxes"][0].set_facecolor(_COLOR_A)
    bp["boxes"][1].set_facecolor(_COLOR_B)
    ax.set_ylabel("通行时间 / s")
    ax.set_title("60° 夹角双路汇入单车道：有无 CAV 的车辆通行时间对比", pad=15)
    y_min = min(min(data[0]), min(data[1]))
    y_max = max(max(data[0]), max(data[1]))
    ax.set_ylim(y_min - 15, y_max + 10)
    for i, stats in enumerate(data):
        ax.text(i + 1, min(stats) - 8, f"均值 {sum(stats) / len(stats):.0f}s",
                ha="center", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "merge_travel_time_box.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def plot_speed_delay_bar(stats_idm, stats_cav, out_dir):
    """平均速度与滞留时间柱状图(FR-15 辅助指标)。"""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].bar(["IDM", "CAV"], [stats_idm["speed_kmh"], stats_cav["speed_kmh"]],
                color=[_COLOR_A, _COLOR_B], width=0.5)
    axes[0].set_ylabel("平均行程速度 / km/h")
    axes[0].set_title("平均速度对比")
    axes[0].grid(axis="y", alpha=0.3)
    axes[1].bar(["IDM", "CAV"], [stats_idm["delay_mean"], stats_cav["delay_mean"]],
                color=[_COLOR_A, _COLOR_B], width=0.5)
    axes[1].set_ylabel("平均滞留时间 / s")
    axes[1].set_title("滞留时间对比")
    axes[1].grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "merge_speed_delay_bar.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def plot_space_time(topo, frames_idm, frames_cav, out_dir):
    """双车道时空图:横轴时间、纵轴里程;A 车道橙色、B 车道蓝色;
    汇合点 s_merge 处横线,排队的水平深色段清晰可见。"""
    last_active = max(
        [i for i in range(len(frames_idm)) if frames_idm[i]]
        + [i for i in range(len(frames_cav)) if frames_cav[i]]
    ) + 20

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True, constrained_layout=True)
    notes = ["主路优先：汇入车排队停车", "时隙协同：到点即插不停车"]
    for ax, frames, title, note in zip(
        axes, [frames_idm, frames_cav],
        ["IDM 对照组：60° 汇合口停车排队（B 车道）", "CAV 实验组：时隙分配平滑汇合"],
        notes,
    ):
        by_vid = {}
        for t, fr in enumerate(frames):
            for vid, lane, s, v in fr:
                by_vid.setdefault(vid, []).append((t, s, v, lane))
        for vid, pts in by_vid.items():
            pts = np.array(pts, dtype=object)
            lane = pts[0, 3]
            color = _COLOR_A if lane == "A" else _COLOR_B
            ax.plot(pts[:, 0].astype(float), pts[:, 1].astype(float),
                    color=color, lw=1.0, alpha=0.65, zorder=3)
        if note:
            ax.text(6, topo.total_length - 20, note, fontsize=10, fontweight="bold",
                    color=_COLOR_B, ha="left", zorder=5)
        # 汇合点与车道分界
        ax.axhline(topo.s_merge, color="#d43a2f", ls="--", lw=1.2, zorder=2)
        ax.text(2, topo.s_merge + 4, "60° 汇合点", color="#d43a2f", fontsize=9, zorder=5,
                bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=1))
        ax.set_xlim(0, last_active)
        ax.set_ylim(-6, topo.total_length + 6)
        ax.set_xlabel("时间 / s")
        ax.set_ylabel("里程 / m")
        ax.set_title(title)
        ax.grid(alpha=0.3)
        ax.text(0.99, 0.02, "橙色=A车道 蓝色=B车道",
                transform=ax.transAxes, ha="right", fontsize=8.5, color="#444444",
                bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=2),
                zorder=6)
    fig.savefig(os.path.join(out_dir, "merge_space_time.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def plot_queue_throughput(topo, queue_idm, queue_cav, m_idm, m_cav, out_dir):
    """汇合区排队长度时序 + 汇合点吞吐量对比。"""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    t_idm = np.arange(len(queue_idm))
    t_cav = np.arange(len(queue_cav))
    axes[0].plot(t_idm, queue_idm, color=_COLOR_A, lw=1.4, label="IDM 对照组")
    axes[0].plot(t_cav, queue_cav, color=_COLOR_B, lw=1.4, label="CAV 实验组")
    axes[0].set_xlabel("时间 / s")
    axes[0].set_ylabel("汇合区排队车辆数")
    axes[0].set_title("60° 汇合区实时排队长度(低速车数)")
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    labels = ["IDM", "CAV"]
    tp = [m_idm["throughput_per_min"], m_cav["throughput_per_min"]]
    bars = axes[1].bar(labels, tp, color=[_COLOR_A, _COLOR_B], width=0.5)
    for b, val in zip(bars, tp):
        axes[1].text(b.get_x() + b.get_width() / 2, val + 0.2, f"{val:.1f}",
                     ha="center", fontsize=10)
    axes[1].set_ylabel("汇合点吞吐量 / 辆/min")
    axes[1].set_title("汇合点通过率对比")
    axes[1].grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "merge_queue_throughput.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def _draw_scene(ax, topo):
    """绘制 60° 夹角双路汇入单车道的道路线网(仿 cav_mas_animation.gif):
    淡灰路缘线 + 黄色虚线中线 + 绿色方向箭头与地标 + 60° 夹角弧线。"""
    hw = topo.half
    (mx, my) = topo.merge_xy
    (ax0, ay0) = topo.entry_a
    (bx0, by0) = topo.entry_b
    (ex, ey) = topo.exit_xy
    nx, ny = -topo.u_b[1], topo.u_b[0]          # B 路法向(垂直偏移方向)

    # ---- 路缘线(淡灰实线) ----
    ax.plot([ax0, mx], [ay0 + hw, my + hw], color=_EDGE, lw=3, zorder=2)
    ax.plot([ax0, mx], [ay0 - hw, my - hw], color=_EDGE, lw=3, zorder=2)
    ax.plot([bx0 + nx * hw, mx + nx * hw], [by0 + ny * hw, my + ny * hw],
            color=_EDGE, lw=3, zorder=2)
    ax.plot([bx0 - nx * hw, mx - nx * hw], [by0 - ny * hw, my - ny * hw],
            color=_EDGE, lw=3, zorder=2)
    ax.plot([mx, ex], [my + hw, ey + hw], color=_EDGE, lw=3, zorder=2)
    ax.plot([mx, ex], [my - hw, ey - hw], color=_EDGE, lw=3, zorder=2)
    # ---- 车道中线(黄色虚线) ----
    ax.plot([ax0, mx], [ay0, my], "--", color=_CENTER, lw=2.2, zorder=3,
            solid_capstyle="round", dash_capstyle="round")
    ax.plot([bx0, mx], [by0, my], "--", color=_CENTER, lw=2.2, zorder=3,
            solid_capstyle="round", dash_capstyle="round")
    ax.plot([mx, ex], [my, ey], "--", color=_CENTER, lw=2.2, zorder=3,
            solid_capstyle="round", dash_capstyle="round")

    # ---- 60° 夹角弧线标注(汇合点,两路来向 120°~180°) ----
    r = 20.0
    ax.add_patch(Arc((mx, my), 2 * r, 2 * r, theta1=120.0, theta2=180.0,
                     color="#d43a2f", lw=2.0, zorder=4))
    th = np.deg2rad(150.0)
    ax.text(mx + (r + 6) * np.cos(th), my + (r + 6) * np.sin(th), "60°",
            ha="center", va="center", fontsize=12, fontweight="bold",
            color="#d43a2f", zorder=5)

    # ---- 行进方向箭头(绿色) ----
    for p1, p2 in [((120.0, 0.0), (150.0, 0.0)),
                   (tuple(topo.entry_b + 40.0 * topo.u_b),
                    tuple(topo.entry_b + 70.0 * topo.u_b)),
                   ((230.0, 0.0), (260.0, 0.0))]:
        ax.annotate("", xy=p2, xytext=p1,
                    arrowprops=dict(arrowstyle="-|>", color=_GREEN, lw=3.5))

    # ---- 地标文字(绿色粗体) ----
    ax.text(ax0, ay0 - 14, "入口A", ha="center", fontsize=11,
            fontweight="bold", color=_GREEN)
    ax.text(bx0 - 20, by0 + 12, "入口B", ha="center", fontsize=11,
            fontweight="bold", color=_GREEN)
    ax.text(mx, my + 16, "汇合点", ha="center", fontsize=11,
            fontweight="bold", color=_GREEN)
    ax.text(ex + 12, ey - 14, "出口", ha="center", fontsize=11,
            fontweight="bold", color=_GREEN)


# ===========================================================================
def make_animation(topo, frames_idm, frames_cav, out_path):
    """双面板车辆运动动画(风格仿照 cav_mas_animation.gif):
    左右两面板,线状车道(淡灰路缘线+黄色虚线中线)+ 绿色箭头地标 +
    60° 夹角弧线 + 车辆散点按速度 RdYlGn 着色 + 色条。"""
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.8), constrained_layout=True)
    fig.patch.set_facecolor("#ffffff")
    norm = plt.Normalize(0.0, topo.v_road)
    cmap = plt.get_cmap("RdYlGn")
    scatters = []

    for ax, title in zip(axes, ["IDM 对照组（主路优先）", "CAV 实验组（时隙协同）"]):
        ax.set_facecolor("#ffffff")
        _draw_scene(ax, topo)
        # 四周留白,道路不贴画框
        ax.set_xlim(-45, topo.exit_xy[0] + 55)
        ax.set_ylim(-38, topo.entry_b[1] + 48)
        ax.set_aspect("equal")
        ax.set_title(title)
        ax.axis("off")

        sc = ax.scatter([], [], s=130, c=[], cmap=cmap, norm=norm,
                        edgecolors="#1b1b1b", linewidths=0.9, zorder=4)
        scatters.append(sc)

    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, shrink=0.75,
                 label="速度 m/s", pad=0.02)

    last_nonempty = max(
        [i for i in range(len(frames_idm)) if frames_idm[i]]
        + [i for i in range(len(frames_cav)) if frames_cav[i]]
    ) + 10
    step = 2  # 每 2 tick 一帧
    idxs = list(range(0, min(len(frames_idm), len(frames_cav), last_nonempty), step))

    def update(i):
        for sc, frames in zip(scatters, (frames_idm, frames_cav)):
            fr = frames[i]
            if fr:
                pos = np.array([topo.s_to_xy(r[2], r[1]) for r in fr])
                col = np.array([r[3] for r in fr])
            else:
                pos = np.empty((0, 2))
                col = np.empty(0)
            sc.set_offsets(pos)
            sc.set_array(col)
        axes[0].set_title(f"IDM 对照组（主路优先）  t={i}s")
        axes[1].set_title(f"CAV 实验组（时隙协同）  t={i}s")
        return scatters

    anim = FuncAnimation(fig, update, frames=idxs, interval=200, blit=False)
    anim.save(out_path, writer="pillow", fps=6)
    plt.close(fig)
