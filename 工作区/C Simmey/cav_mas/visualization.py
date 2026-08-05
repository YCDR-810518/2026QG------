# -*- coding: utf-8 -*-
"""visualization.py —— CAV+MAS 对比实验的全部绘图输出（拆分自 CAV+MAS.py 第 4 节）

包含四类输出：
    plot_travel_time_box  通行时间箱线图（FR-15 主指标）
    plot_speed_delay_bar  平均速度 / 滞留时间柱状图
    plot_space_time       时空轨迹图（按车速着色）
    make_animation        双面板车辆运动动画 GIF

所有函数显式接收 topo（LShapeTopology），不依赖任何全局常量。
"""
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_SIGN_X = 15.0    # 限速牌相对拐点的横向偏移 m
_SIGN_Y = 16.0    # 限速牌纵向位置 m


# ===========================================================================
def plot_travel_time_box(logs_idm, logs_cav, out_dir):
    """通行时间箱线图（FR-15 主指标）。"""
    data = [
        [l["travel_time"] for l in logs_idm if l["arrived"]],
        [l["travel_time"] for l in logs_cav if l["arrived"]],
    ]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    bp = ax.boxplot(data, tick_labels=["IDM 对照组（无 CAV）", "CAV 实验组（车联网协同）"],
                    patch_artist=True, widths=0.5)
    bp["boxes"][0].set_facecolor("#f4a261")
    bp["boxes"][1].set_facecolor("#2a9d8f")
    ax.set_ylabel("通行时间 / s")
    ax.set_title("有无 CAV 的车辆通行时间对比", pad=15)
    y_min = min(min(data[0]), min(data[1]))
    y_max = max(max(data[0]), max(data[1]))
    ax.set_ylim(y_min - 15, y_max + 10)
    for i, (name, stats) in enumerate(zip(["IDM", "CAV"], data)):
        ax.text(i + 1, min(stats) - 8, f"均值 {sum(stats) / len(stats):.0f}s",
                ha="center", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "travel_time_box.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def plot_speed_delay_bar(stats_idm, stats_cav, out_dir):
    """平均速度与滞留时间柱状图（FR-15 辅助指标）。"""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].bar(["IDM", "CAV"], [stats_idm["speed_kmh"], stats_cav["speed_kmh"]],
                color=["#f4a261", "#2a9d8f"], width=0.5)
    axes[0].set_ylabel("平均行程速度 / km/h")
    axes[0].set_title("平均速度对比")
    axes[0].grid(axis="y", alpha=0.3)
    axes[1].bar(["IDM", "CAV"], [stats_idm["delay_mean"], stats_cav["delay_mean"]],
                color=["#f4a261", "#2a9d8f"], width=0.5)
    axes[1].set_ylabel("平均滞留时间 / s")
    axes[1].set_title("滞留时间对比")
    axes[1].grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "speed_delay_bar.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def plot_space_time(topo, frames_idm, frames_cav, out_dir):
    """时空轨迹图：横轴时间、纵轴里程，轨迹按车速着色（红=停 绿=行），
    深红水平段=停车排队，纵轴标注图书馆/工一/教一地标。"""
    last_active = max(
        [i for i in range(len(frames_idm)) if frames_idm[i]]
        + [i for i in range(len(frames_cav)) if frames_cav[i]]
    ) + 20

    landmarks = [(0.0, "图书馆"), (topo.len_a, "工一（弯道）"), (topo.total_length, "教一")]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharey=True, constrained_layout=True)
    notes = ["", "编队平滑通过，全程不停车"]
    for ax, frames, title, note in zip(
        axes, [frames_idm, frames_cav],
        ["IDM 对照组：弯道瓶颈致拥堵回溢（停车波堵至入口）",
         "CAV 实验组：编队平滑通过弯道"],
        notes,
    ):
        by_vid = {}
        for t, fr in enumerate(frames):
            for vid, s, v in fr:
                # 假设 vid 格式为 "car_00", "car_01" 等，通过判断最后一位来抽样
                if int(vid.split('_')[-1]) % 2 == 0:
                    by_vid.setdefault(vid, []).append((t, s, v))
        segs, cols = [], []
        for pts in by_vid.values():
            pts = np.array(pts)
            for i in range(len(pts) - 1):
                segs.append([(pts[i, 0], pts[i, 1]), (pts[i + 1, 0], pts[i + 1, 1])])
                cols.append(float((pts[i, 2] + pts[i + 1, 2]) / 2))
        if segs:
            lc = LineCollection(segs, cmap="RdYlGn",
                                norm=plt.Normalize(0.0, topo.v_road),
                                linewidths=1.0,  # 变细
                                alpha=0.7,       # 增加透明度，避免糊成一团
                                zorder=3)
            lc.set_array(cols)
            ax.add_collection(lc)
        if note:
            ax.text(6, topo.total_length - 30, note, fontsize=10, fontweight="bold",
                    color="#2a9d8f", ha="left", zorder=5)
        # 1. 绘制地标及文字（统一靠左对齐，并添加白色半透明背景防遮挡）
        for s0, name in landmarks:
            ax.axhline(s0, color="#888888", ls=":", lw=0.9, zorder=1)

            # 动态调整 Y 坐标：如果是最顶部的教一，文字往下放一点防出界；其他的放在线上方
            y_txt = s0 - 15 if s0 >= topo.total_length - 10 else s0 + 4

            # 统一把地标名字画在左侧 (x=2)
            ax.text(2, y_txt, f"{name} {s0:.0f}m", ha="left",
                    va="bottom", fontsize=9, color="#333333", zorder=5,
                    bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=1.5))

        # 2. 绘制弯道限速区及文字（将文字移到粉色区域的右上角，避开左侧的"工一"）
        ax.axhspan(topo.limit_lo, topo.limit_hi, color="r", alpha=0.12, zorder=2)
        ax.axhline(topo.limit_lo, ls="--", color="r", lw=0.8, zorder=2)
        ax.axhline(topo.limit_hi, ls="--", color="r", lw=0.8, zorder=2)

        # 将 x 坐标设为 last_active - 5（靠右），y 坐标设为 limit_hi - 8（靠上限速边界）
        ax.text(last_active - 5, topo.limit_hi - 8, "弯道限速区", color="r", fontsize=9,
                ha="right", zorder=4,
                bbox=dict(facecolor="white", alpha=0.6, edgecolor="none", pad=1))
        ax.set_xlim(0, last_active)
        ax.set_ylim(-6, topo.total_length + 6)
        ax.set_xlabel("时间 / s")
        ax.set_ylabel("里程 / m")
        ax.set_title(title)
        ax.grid(alpha=0.3)
        ax.text(0.99, 0.02, "坡度=车速｜红=停车(<1m/s)｜绿=行驶",
                transform=ax.transAxes, ha="right", fontsize=8.5, color="#444444",
                bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=2),
                zorder=6)
    fig.colorbar(ScalarMappable(norm=plt.Normalize(0.0, topo.v_road), cmap="RdYlGn"),
                 ax=axes, shrink=0.75, label="速度 m/s", pad=0.02)
    fig.savefig(os.path.join(out_dir, "space_time.png"), dpi=150)
    plt.close(fig)


# ===========================================================================
def make_animation(topo, frames_idm, frames_cav, out_path):
    """双面板车辆运动动画：线状车道（边缘白线+中心黄虚线）+ 点状车辆，保存 GIF。

    车道：先绘制 L 型两臂（水平臂 len_a、垂直臂 len_b 不等长）的道路标线；
    车辆：散点沿本向车道行驶，按速度着色（红=慢 绿=快）。
    """
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.5), constrained_layout=True)
    fig.patch.set_facecolor("#ffffff")
    norm = plt.Normalize(0.0, topo.v_road)
    cmap = plt.get_cmap("RdYlGn")
    scatters = []

    for ax, title in zip(axes, ["IDM 对照组（独立决策）", "CAV 实验组（车联网协同）"]):
        ax.set_facecolor("#ffffff")
        xs, ys = zip(*topo.waypoints)
        half = topo.lane_width / 2
        # 车道：白色边缘线
        ax.plot([0, topo.len_a], [half, half], color="#f2f2f2", lw=3, zorder=2)
        ax.plot([0, topo.len_a], [-half, -half], color="#f2f2f2", lw=3, zorder=2)
        ax.plot([topo.len_a - half, topo.len_a - half], [0, topo.len_b], color="#f2f2f2", lw=3, zorder=2)
        ax.plot([topo.len_a + half, topo.len_a + half], [0, topo.len_b], color="#f2f2f2", lw=3, zorder=2)
        # 车道：中心黄虚线（对向分隔）
        ax.plot(xs, ys, "--", color="#e8c200", lw=2.2, zorder=3,
                solid_capstyle="round", dash_capstyle="round")
        # 弯道限速牌 + 工一建筑标签（工一在牌下方）
        ax.scatter([topo.len_a + _SIGN_X], [_SIGN_Y], s=1000, c="#f5d74a", edgecolors="#d43a2f",
                   linewidths=3.5, marker="o", zorder=5)
        ax.text(topo.len_a + _SIGN_X, _SIGN_Y, "限速\n3m/s", ha="center", va="center", fontsize=11,
                fontweight="bold", color="#d43a2f", zorder=6, linespacing=1.1)
        ax.text(topo.len_a + _SIGN_X, 2, "工一", ha="center", va="top", fontsize=10,
                fontweight="bold", color="#8d6e63", zorder=6)
        # 图书馆 / 教一 标记
        ax.annotate("", xy=(26, 0), xytext=(4, 0),
                    arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=3.5))
        ax.annotate("", xy=(topo.len_a, topo.len_b + 18), xytext=(topo.len_a, topo.len_b - 4),
                    arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=3.5))
        ax.text(0, -13, "图书馆", ha="center", fontsize=11, fontweight="bold", color="#2e7d32")
        ax.text(topo.len_a, topo.len_b + 30, "教一", ha="center", fontsize=11,
                fontweight="bold", color="#2e7d32")
        # 【修改点 2】四周增加留白边界，让道路不要贴着画框
        ax.set_xlim(-35, topo.len_a + 60)
        ax.set_ylim(-35, topo.len_b + 60)
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
    idxs = list(range(0, min(len(frames_idm), last_nonempty), step))

    def update(i):
        for sc, frames in zip(scatters, (frames_idm, frames_cav)):
            fr = frames[i]
            if fr:
                pos = np.array([topo.s_to_xy(r[1]) for r in fr])
                col = np.array([r[2] for r in fr])
            else:
                pos = np.empty((0, 2))
                col = np.empty(0)
            sc.set_offsets(pos)
            sc.set_array(col)
        axes[0].set_title(f"IDM 对照组（独立决策）  t={i}s")
        axes[1].set_title(f"CAV 实验组（车联网协同）  t={i}s")
        return scatters

    anim = FuncAnimation(fig, update, frames=idxs, interval=200, blit=False)
    anim.save(out_path, writer="pillow", fps=6)
    plt.close(fig)
