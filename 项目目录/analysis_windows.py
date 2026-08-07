#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QG园区 density_series.csv 时空分析(Windows本地运行版)

运行方法(任选其一):
  python analysis_windows.py
  python analysis_windows.py "D:\\资料-study\\6-QG暑期考核\\1-QG园区\\项目目录\\data\\density_series.csv"

依赖:pip install pandas matplotlib numpy
输出:在CSV同目录下生成 QG_analysis_output/ 文件夹,含CSV表和PNG图表
"""
import os
import sys
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ---------- 路径 ----------
DEFAULT_CSV = r"D:\资料-study\6-QG暑期考核\1-QG园区\项目目录\data\density_series.csv"
SRC = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
OUT = os.path.join(os.path.dirname(SRC), "QG_analysis_output")
os.makedirs(OUT, exist_ok=True)

# ---------- 中文字体(Windows用微软雅黑) ----------
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

print("读取:", SRC)
df = pd.read_csv(SRC, dtype={'node_id': str})
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['date'] = df['timestamp'].dt.date
df['hour'] = df['timestamp'].dt.hour
df['dow'] = df['timestamp'].dt.dayofweek          # 0=周一
df['dow_name'] = df['timestamp'].dt.day_name()

dates = sorted(df['date'].unique())
print("日期范围:", dates[0], "->", dates[-1], "共", len(dates), "天")
print("每日星期:", df.groupby('date')['dow_name'].first().to_dict())
print("节点数:", df['node_id'].nunique(), "总行数:", len(df))

# 每tick瞬时全园总人数
tick = df.groupby(['timestamp', 'date', 'dow', 'hour'])['people'].sum().rename('total_people').reset_index()

# ---------- 逐小时 ----------
hourly = tick.groupby(['date', 'dow', 'hour'])['total_people'].agg(
    avg='mean', peak='max', person_ticks='sum').reset_index()
hourly['person_hours'] = hourly['person_ticks'] * 10 / 3600

workday = hourly[hourly['dow'].isin([0, 1, 2, 3, 4])]
sat = hourly[hourly['dow'] == 5]
sun = hourly[hourly['dow'] == 6]

h_profile = pd.DataFrame({'hour': range(6, 22)})
h_profile['wd_avg'] = h_profile['hour'].map(workday.groupby('hour')['avg'].mean())
h_profile['wd_peak'] = h_profile['hour'].map(workday.groupby('hour')['peak'].max())
h_profile['sat_avg'] = h_profile['hour'].map(sat.groupby('hour')['avg'].mean())
h_profile['sat_peak'] = h_profile['hour'].map(sat.groupby('hour')['peak'].max())
h_profile['sun_avg'] = h_profile['hour'].map(sun.groupby('hour')['avg'].mean())
h_profile['sun_peak'] = h_profile['hour'].map(sun.groupby('hour')['peak'].max())
h_profile['wd_personhours'] = h_profile['hour'].map(workday.groupby('hour')['person_hours'].mean())
h_profile['sat_personhours'] = h_profile['hour'].map(sat.groupby('hour')['person_hours'].mean())
h_profile['sun_personhours'] = h_profile['hour'].map(sun.groupby('hour')['person_hours'].mean())
h_profile.to_csv(os.path.join(OUT, 'hourly_profile.csv'), index=False)
print("\n===== 逐小时人数(全园瞬时总人数) =====")
print(h_profile.round(1).to_string(index=False))

# ---------- 地点集中度与密度 ----------
loc = df.groupby('node_id').agg(
    people_sum=('people', 'sum'),
    people_avg=('people', 'mean'),
    people_max=('people', 'max'),
    vehicles_avg=('vehicles', 'mean'),
    density_avg=('density', 'mean'),
    density_max=('density', 'max'),
).reset_index()
loc['person_hours'] = loc['people_sum'] * 10 / 3600
loc = loc.sort_values('people_avg', ascending=False).reset_index(drop=True)
loc['rank'] = loc.index + 1
loc.to_csv(os.path.join(OUT, 'location_ranking.csv'), index=False)
print("\n===== 地点排名(按平均在场人数) Top20 =====")
print(loc.head(20)[['rank', 'node_id', 'people_avg', 'people_max',
                    'person_hours', 'density_avg', 'density_max']].round(3).to_string(index=False))

# ---------- 时段×地点 高峰分析 ----------
loc_hour = df.groupby(['node_id', 'hour'])['people'].agg(['mean', 'max']).reset_index()
peak_hour = loc_hour.loc[loc_hour.groupby('node_id')['mean'].idxmax()]
peak_hour = peak_hour.rename(columns={'mean': 'peak_avg', 'max': 'peak_max', 'hour': 'peak_hour'})
peak_hour = peak_hour.sort_values('peak_avg', ascending=False)
peak_hour.to_csv(os.path.join(OUT, 'location_peak_hour.csv'), index=False)
print("\n===== 各地点最拥挤时段 =====")
print(peak_hour.head(15).round(1).to_string(index=False))

# ---------- 日×小时 热力图 ----------
pivot = tick.pivot_table(index='date', columns='hour', values='total_people', aggfunc='mean')
pivot = pivot.reindex(columns=sorted(pivot.columns))
pivot.to_csv(os.path.join(OUT, 'day_hour_heatmap.csv'))
print("\n===== 日×小时平均人数矩阵 =====")
print(pivot.round(1).to_string())

# ---------- 图表 ----------
def style_ax(ax):
    ax.grid(alpha=0.3, linestyle='--')

fig, axes = plt.subplots(2, 2, figsize=(15, 10))
ax = axes[0, 0]
ax.plot(h_profile['hour'], h_profile['wd_avg'], 'o-', color='#2c7fb8', label='工作日(周一~周五)平均')
ax.plot(h_profile['hour'], h_profile['sat_avg'], 's-', color='#f03b20', label='周六平均')
ax.plot(h_profile['hour'], h_profile['sun_avg'], '^-', color='#31a354', label='周日平均')
ax.set_xlabel('小时'); ax.set_ylabel('平均在场总人数')
ax.set_title('逐小时全园总人数(瞬时平均)')
ax.set_xticks(range(6, 22)); ax.legend(); style_ax(ax)

ax = axes[0, 1]
ax.plot(h_profile['hour'], h_profile['wd_personhours'], 'o-', color='#2c7fb8', label='工作日')
ax.plot(h_profile['hour'], h_profile['sat_personhours'], 's-', color='#f03b20', label='周六')
ax.plot(h_profile['hour'], h_profile['sun_personhours'], '^-', color='#31a354', label='周日')
ax.set_xlabel('小时'); ax.set_ylabel('人·小时')
ax.set_title('逐小时累计人流量(人·小时)')
ax.set_xticks(range(6, 22)); ax.legend(); style_ax(ax)

top15 = loc.head(15)
ax = axes[1, 0]
ypos = np.arange(len(top15))[::-1]
ax.barh(ypos, top15['people_avg'], color='#2c7fb8')
ax.set_yticks(ypos); ax.set_yticklabels(top15['node_id'], fontsize=8)
ax.set_xlabel('平均在场人数')
ax.set_title('平均人数最高的15个地点')
for y, v, d in zip(ypos, top15['people_avg'], top15['density_avg']):
    ax.text(v, y, f' {v:.1f}  (密度{d:.2f})', va='center', fontsize=8)
style_ax(ax)

ax = axes[1, 1]
im = ax.imshow(pivot.values, aspect='auto', cmap='YlOrRd')
ax.set_yticks(range(len(pivot.index)))
ax.set_yticklabels([str(d)[5:] for d in pivot.index], fontsize=8)
ax.set_xticks(range(len(pivot.columns)))
ax.set_xticklabels([f'{h}:00' for h in pivot.columns], fontsize=8)
ax.set_title('日×小时 平均总人数热力图')
plt.colorbar(im, ax=ax)

fig.suptitle('QG园区人流时空分析', fontsize=14)
fig.tight_layout(rect=[0, 0, 1, 0.97])
fig.savefig(os.path.join(OUT, 'overview.png'), dpi=110)
plt.close(fig)

print("\n[完成] 输出目录:", OUT)
