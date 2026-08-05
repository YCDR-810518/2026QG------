# -*- coding: utf-8 -*-
"""gen_week2.py —— 生成第 2 份 7 天训练数据 + 拼接 14 天训练文件（成员 F）

产出（相对本文件所在目录的 data/）：
- week2/people.csv, vehicles.csv, density_series.csv
    2026-08-10(周一) ~ 08-16(周日)，4000人/300车，peak，random_state=2026
- combined/people.csv, vehicles.csv, density_series.csv
    首周(08-03~08-09, seed=42) + week2 拼接为 14 天；week2 的 tick/birth_tick +403200(=7×57600)
    供李世涵 train.py 直接训练：DATA_PATH 指向 data/combined/density_series.csv

依赖：numpy, pandas（Python 3.10+）
用法：python gen_week2.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

from flow_data_generator import FlowDataGenerator

WEEK2_SEED = 2026          # 与首周 seed=42 不同，避免数据逐日相同
DAY_SEC = 57600            # 每天模拟 16h = 57600 tick
WEEK_TICKS = 7 * DAY_SEC   # 403200，week2 拼接时的 tick 偏移量
START_DATE = "2026-08-10"  # 周一


def to_csv_offset(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    print(f"  write -> {path}  ({len(df)} rows)")


def main():
    out_root = Path(__file__).resolve().parent / "data"

    # ---------- 1. 生成 week2（独立 7 天） ----------
    print("== [1/3] 生成 week2（2026-08-10 ~ 08-16, seed=2026） ==")
    gen = FlowDataGenerator(
        n_people=4000, n_vehicles=300, density_level="peak",
        n_days=7, day_profiles=("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
        start_date=START_DATE, random_state=WEEK2_SEED, data_dir=out_root,
    )
    ds = gen.generate()
    gen.to_csv("week2")
    print(f"  week2 people: {ds.people['birth_tick'].size}  vehicles: {ds.vehicles['birth_tick'].size}")

    # ---------- 2. 拼接 14 天 -> combined/ ----------
    print("== [2/3] 拼接 14 天 -> combined/ ==")
    combined = out_root / "combined"

    for name, tick_col, dtype in (
            ("density_series.csv", "tick", {"node_id": str}),
            ("people.csv", "birth_tick", {}),
            ("vehicles.csv", "birth_tick", {})):
        w1 = pd.read_csv(out_root / name, dtype=dtype)
        w2 = pd.read_csv(out_root / "week2" / name, dtype=dtype)
        w2[tick_col] = w2[tick_col] + WEEK_TICKS
        to_csv_offset(combined / name, pd.concat([w1, w2], ignore_index=True))

    # combined 的 people/vehicles id 重排为全局连续（people 0..N-1，vehicles 从 N 继续）
    for name in ("people.csv", "vehicles.csv"):
        df = pd.read_csv(combined / name)
        df["id"] = np.arange(len(df))
        df.to_csv(combined / name, index=False, encoding="utf-8")
        print(f"  renumber -> {combined / name}  ({len(df)} rows, id 0..{len(df)-1})")
    n_people = len(pd.read_csv(combined / "people.csv"))
    df_v = pd.read_csv(combined / "vehicles.csv")
    df_v["id"] = np.arange(n_people, n_people + len(df_v))
    df_v.to_csv(combined / "vehicles.csv", index=False, encoding="utf-8")
    print(f"  renumber vehicles -> id {n_people}..{n_people + len(df_v) - 1}")

    # ---------- 3. 校验 ----------
    print("== [3/3] 校验 ==")
    df = pd.read_csv(combined / "density_series.csv", dtype={"node_id": str})
    df["ts"] = pd.to_datetime(df["timestamp"])
    print(f"  density_series 行数: {len(df)}  (期望 819840)")
    print(f"  时间范围: {df['ts'].min()} ~ {df['ts'].max()}")
    print(f"  tick 范围: {df['tick'].min()} ~ {df['tick'].max()}")
    print(f"  节点数: {df['node_id'].nunique()}  (期望 61)")
    dup = df.duplicated(["tick", "node_id"]).sum()
    print(f"  (tick, node_id) 重复: {dup}  (期望 0)")
    print(f"  level 分布: {df['level'].value_counts().to_dict()}")
    print("done ->", combined)


if __name__ == "__main__":
    main()
