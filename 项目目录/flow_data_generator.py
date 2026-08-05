# -*- coding: utf-8 -*-
"""FlowDataGenerator —— FR-17 随机人流/车辆数据生成器（成员 F）

字段对齐：
- people.csv      : id, birth_tick, src_node, dst_node, kind
- vehicles.csv    : id, birth_tick, src_node, dst_node, kind, is_internal
- density_series.csv : tick, timestamp, node_id, people, vehicles, density, level, gate_status, gate_flow_rate

OD 改造：节点时变到达强度曲线 node_curves + 类型级 OD 概率矩阵。
每 tick 某个节点成为"生成点"的概率 = curve_i(t) / sum(curve(t))，
从而可用概率控制每个数据的生成点，实现"某些节点在某时间段人流量大"。

依赖：numpy（Python 3.10+）
用法：
    gen = FlowDataGenerator(n_people=4000, n_vehicles=300, random_state=42)
    ds = gen.generate()         # 生成数据（内存）
    gen.to_csv()                # 写 data/
    gen.sample(tick)            # 引擎逐 tick 取流入实体
"""
import csv
import datetime
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# 拓扑：与《可视化的图.py》节点集一致（(id, name, type, x, y, has_light, has_gate)）
# ---------------------------------------------------------------------------
NODE_LIST = [
    ("gate_south", "南大门", "entrance", 65, 5, True, True),
    ("gate_west", "西门出口", "entrance", 8, 55, True, True),
    ("gate_east", "东门出口", "entrance", 92, 55, True, True),
    ("cross_zh_south", "中环西南路口", "road", 45, 12, True, False),
    ("cross_zh_mid", "中环天桥/通道口", "road", 40, 58, True, False),
    ("cross_zh_north", "中环西北路口", "road", 45, 80, True, False),
    ("rd_guanggong_1", "广工一路节点", "road", 85, 68, True, False),
    ("pedestrian_bridge", "天桥/下沉球场", "road", 35, 58, False, False),
    ("underpass", "广工通道", "road", 48, 58, False, False),
    ("admin_building", "行政楼/综合楼", "admin", 62, 12, False, False),
    ("auditorium", "大讲堂(圆弧报告厅)", "admin", 68, 8, False, False),
    ("library", "图书馆", "academic", 58, 38, False, True),
    ("gongchuanggu", "工创谷", "academic", 62, 48, False, False),
    ("teach_1", "教一", "academic", 78, 48, False, False),
    ("teach_2", "教二", "academic", 78, 56, False, False),
    ("teach_3", "教三", "academic", 68, 48, False, False),
    ("teach_4", "教四", "academic", 70, 56, False, False),
    ("teach_5", "教五", "academic", 62, 44, False, False),
    ("teach_6", "教六", "academic", 64, 56, False, False),
    ("large_classroom", "大教室(54/32)", "academic", 72, 52, False, False),
    ("eng_1", "工一", "lab", 72, 32, False, False),
    ("eng_2", "工二", "lab", 72, 28, False, False),
    ("eng_3", "工三", "lab", 72, 24, False, False),
    ("eng_4", "工四", "lab", 72, 20, False, False),
    ("exp_1", "实一", "lab", 82, 32, False, False),
    ("exp_2", "实二", "lab", 82, 28, False, False),
    ("exp_3", "实三", "lab", 82, 24, False, False),
    ("exp_4", "实四", "lab", 82, 20, False, False),
    ("science_hall", "理学馆", "lab", 82, 14, False, False),
    ("struct_center", "结构实验中心", "lab", 90, 32, False, False),
    ("env_inst", "环境生态研究院", "lab", 90, 26, False, False),
    ("biomed_inst", "生物医药学院", "lab", 90, 20, False, False),
    ("sports_gym", "体育馆", "sports", 32, 52, False, False),
    ("sports_swimming", "游泳馆", "sports", 32, 45, False, False),
    ("sports_fitness", "健身房/田径场", "sports", 42, 48, False, False),
    ("sports_cricket", "板球场", "sports", 12, 40, False, False),
    ("sports_tennis", "网球场", "sports", 22, 45, False, False),
    ("sports_volleyball", "排球场", "sports", 26, 38, False, False),
    ("sports_basketball_c", "篮球场C区", "sports", 28, 30, False, False),
    ("sports_basketball_b", "篮球场B区", "sports", 36, 30, False, False),
    ("sports_basketball_a", "篮球场A区", "sports", 42, 30, False, False),
    ("sports_football", "足球场", "sports", 48, 35, False, False),
    ("sports_training", "综合训练场", "sports", 48, 22, False, False),
    ("youth_center", "青年活动中心", "sports", 45, 52, False, False),
    ("west_dorm_13_16", "西十三~十六宿", "living", 10, 78, False, True),
    ("west_dorm_9_12", "西九~十二宿舍", "living", 20, 78, False, True),
    ("west_dorm_1_4", "西一~四宿舍", "living", 28, 78, False, True),
    ("west_dorm_5_8", "西五~八宿舍", "living", 22, 68, False, True),
    ("west_dorm_17_18", "西十七~十八宿", "living", 12, 65, False, True),
    ("canteen_3", "三饭堂", "living", 28, 62, False, False),
    ("canteen_4", "四饭堂", "living", 8, 60, False, False),
    ("west_express", "西区快递点", "living", 28, 72, False, False),
    ("east_dorm_12_14", "东十二~十四宿", "living", 48, 75, False, True),
    ("east_dorm_8_11", "东八~十一宿", "living", 58, 70, False, True),
    ("east_dorm_4_7", "东四~七宿舍", "living", 58, 62, False, True),
    ("east_dorm_1_3", "东一~三宿舍", "living", 68, 65, False, True),
    ("teacher_apt", "教师公寓", "living", 48, 85, False, True),
    ("hospital", "广工校医院", "living", 58, 92, False, False),
    ("supermarket", "校内超市", "living", 55, 86, False, False),
    ("canteen_1", "东区一饭", "living", 62, 86, False, False),
    ("canteen_2", "东区二饭", "living", 48, 68, False, False),
]

# ---------------------------------------------------------------------------
# 节点容量：按类型默认值 + 关键节点覆盖（density = people / capacity）
# ---------------------------------------------------------------------------
TYPE_CAPACITY = {
    "entrance": 150,
    "road": 80,
    "admin": 120,
    "academic": 120,
    "lab": 120,
    "sports": 150,
    "living": 150,
}
CAPACITY_OVERRIDES = {
    "canteen_1": 180, "canteen_2": 180, "canteen_3": 180, "canteen_4": 180,
    "west_dorm_13_16": 180, "west_dorm_9_12": 180, "west_dorm_1_4": 180,
    "west_dorm_5_8": 180, "west_dorm_17_18": 180,
    "east_dorm_12_14": 180, "east_dorm_8_11": 180, "east_dorm_4_7": 180,
    "east_dorm_1_3": 180,
}

# ---------------------------------------------------------------------------
# 类型级 OD 概率矩阵：P(dst_type | src_type)（dst != src 自动排除）
# ---------------------------------------------------------------------------
TYPE_OD = {
    "entrance": {"academic": 0.30, "living": 0.28, "sports": 0.14, "lab": 0.10, "admin": 0.10, "road": 0.08},
    "road":     {"living": 0.30, "academic": 0.25, "sports": 0.20, "admin": 0.10, "lab": 0.10, "road": 0.05},
    "admin":    {"living": 0.30, "academic": 0.25, "lab": 0.15, "sports": 0.10, "road": 0.20},
    "academic": {"living": 0.40, "academic": 0.25, "sports": 0.10, "lab": 0.10, "road": 0.15},
    "lab":      {"living": 0.40, "academic": 0.20, "lab": 0.15, "sports": 0.10, "road": 0.15},
    "sports":   {"living": 0.45, "academic": 0.15, "road": 0.25, "admin": 0.15},
    "living":   {"academic": 0.35, "living": 0.30, "sports": 0.15, "lab": 0.10, "road": 0.10},
}

# 停留时长（分钟）下/上界，按 dst 节点类型
DWELL_MIN = {
    "entrance": 3, "road": 1, "admin": 30, "academic": 90,
    "lab": 60, "sports": 60, "living": 90,
}
DWELL_MAX = {
    "entrance": 8, "road": 3, "admin": 90, "academic": 150,
    "lab": 120, "sports": 120, "living": 240,
}

# 出发前在 src 节点逗留（排队/过闸）时长（分钟），按 src 节点类型
WAIT_MIN = {
    "entrance": 2, "road": 1, "admin": 1, "academic": 1,
    "lab": 1, "sports": 1, "living": 1,
}
WAIT_MAX = {
    "entrance": 6, "road": 3, "admin": 3, "academic": 3,
    "lab": 3, "sports": 3, "living": 3,
}

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _set_range(base, s, e, v):
    for h in range(s, min(e, 24)):
        base[h] = v


def _default_curve(node_type, node_id, weekday="mon"):
    """按节点类型 + 星期几生成 24 小时相对到达强度曲线。

    工作日：上课/上班高峰（早 7-9 门闸、8-12 教学楼、午晚食堂高峰）。
    周六：无早课高峰，睡懒觉+运动（午 14-17 运动高峰）。
    周日：更晚更缓，食堂午晚仍有高峰。
    周一/周五微调：周一早高峰更强（返校），周五傍晚/夜间更强（离校）。
    """
    canteen = str(node_id).startswith("canteen")
    dorm = any(str(node_id).startswith(p) for p in ("west_dorm", "east_dorm", "teacher_apt"))
    is_weekend = weekday in ("sat", "sun")

    base = np.ones(24) * 0.3
    if node_type == "entrance":
        base[:] = 0.4
    elif node_type == "living" and canteen:
        base[:] = 0.3
    elif node_type == "living" and dorm:
        base[:] = 0.3
    elif node_type == "sports":
        base[:] = 0.3
    elif node_type == "road":
        base[:] = 0.2

    if node_type == "entrance":
        if is_weekend:
            _set_range(base, 10, 12, 4.0) if weekday == "sun" else _set_range(base, 9, 11, 5.0)
            _set_range(base, 17, 19, 6.0)
        else:
            _set_range(base, 7, 9, 10.0 if weekday == "mon" else 8.0)
            _set_range(base, 17, 19, 7.5 if weekday == "fri" else 6.0)
    elif node_type == "road":
        _set_range(base, 8, 9, 2.0)
        _set_range(base, 12, 13, 2.0)
        _set_range(base, 18, 19, 2.0)
    elif node_type == "admin":
        _set_range(base, 9, 11, 2.0 if is_weekend else 5.0)
        if not is_weekend:
            _set_range(base, 14, 16, 4.0)
    elif node_type == "academic":
        if is_weekend:
            _set_range(base, 14, 17, 2.0)
            if weekday == "sat":
                _set_range(base, 9, 12, 3.0)
        else:
            _set_range(base, 8, 12, 6.5 if weekday == "mon" else 5.0)
            _set_range(base, 14, 17, 5.0)
    elif node_type == "lab":
        if not is_weekend:
            _set_range(base, 9, 12, 5.0)
            _set_range(base, 14, 17, 5.0)
        elif weekday == "sat":
            _set_range(base, 9, 12, 2.0)
            _set_range(base, 14, 17, 2.0)
    elif node_type == "sports":
        _set_range(base, 18, 21, 7.0 if weekday == "sun" else (9.0 if weekday == "fri" else 8.0))
        if is_weekend:
            _set_range(base, 14, 17, 5.0 if weekday == "sat" else 6.0)
        else:
            _set_range(base, 12, 14, 2.0)
    elif node_type == "living":
        if canteen:
            if is_weekend:
                _set_range(base, 11, 13, 8.0 if weekday == "sat" else 7.0)
                _set_range(base, 17, 19, 8.0)
            else:
                _set_range(base, 7, 9, 3.0)
                _set_range(base, 11, 13, 9.0)
                _set_range(base, 17, 19, 9.0)
        elif dorm:
            if is_weekend:
                _set_range(base, 9, 11, 3.0)
                _set_range(base, 17, 19, 3.0)
                _set_range(base, 21, 23, 4.0 if weekday == "sat" else 3.0)
            else:
                _set_range(base, 6, 8, 8.5 if weekday == "mon" else 7.0)
                _set_range(base, 11, 13, 3.0)
                _set_range(base, 17, 19, 3.0)
                _set_range(base, 21, 23, 6.0 if weekday == "fri" else 4.0)
        else:
            _set_range(base, 9, 12, 3.0)
            _set_range(base, 14, 18, 4.0 if is_weekend else 3.0)
    return base


@dataclass
class FlowDataset:
    people: dict = field(default_factory=dict)
    vehicles: dict = field(default_factory=dict)
    density_series: np.ndarray = None
    node_ids: list = field(default_factory=list)


class FlowDataGenerator:
    """随机人流/车辆数据生成器（FR-17）。

    Parameters
    ----------
    n_people / n_vehicles : int
        计划总人数 / 总车数（按天计，多天时每天各生成该量）。
    density_level : str
        'off_peak' / 'peak' / 'ultra_peak'，对应峰化指数 0.8 / 2.0 / 3.0。
    node_curves : dict, optional
        单节点 24h 曲线覆盖：{node_id: [24] 相对强度}；不传则用类型默认曲线。
    od_matrix : dict, optional
        类型级 OD 概率覆盖，结构同 TYPE_OD。
    sample_interval : int
        密度采样间隔（秒），默认 60。
    start_hour / start_minute : int
        模拟起点时刻（小时/分钟），默认 6:00 起共 n_hours 小时。
        birth_tick=0 即起始时刻，多天时每天 +（n_hours*3600）。
    n_hours : int
        模拟时长（小时），默认 16。
    n_days : int
        模拟天数，默认 1；>1 时跨天连续（birth_tick 每天 +86400）。
    day_profiles : tuple/list
        7 个元素（mon~sun）表示每天用的曲线档位，默认工作日×5 + sat + sun。
    start_date : str
        起始日期 YYYY-MM-DD（默认 2026-08-03 周一），用于 density_series 时间戳。
    random_state : int
        随机种子。
    """

    def __init__(self, n_people=4000, n_vehicles=300, density_level="peak",
                 node_curves=None, od_matrix=None,
                 sample_interval=60, start_hour=6, start_minute=0, n_hours=16,
                 n_days=1, day_profiles=WEEKDAY_NAMES, start_date="2026-08-03",
                 random_state=42, data_dir="data"):
        self.n_people = int(n_people)
        self.n_vehicles = int(n_vehicles)
        self.density_level = density_level
        self.node_curves = node_curves or {}
        self.od_matrix = od_matrix or TYPE_OD
        self.sample_interval = int(sample_interval)
        self.start_hour = int(start_hour)
        self.start_minute = int(start_minute)
        self.start_sec = self.start_hour * 3600 + self.start_minute * 60
        self.n_hours = int(n_hours)
        self.n_days = int(n_days)
        self.day_profiles = list(day_profiles)
        if len(self.day_profiles) != 7:
            raise ValueError(f"day_profiles 需 7 个元素（mon~sun），收到 {len(self.day_profiles)}")
        self.start_date = datetime.date.fromisoformat(str(start_date))
        self.random_state = int(random_state)
        self.data_dir = Path(data_dir)

        self.node_ids = [n[0] for n in NODE_LIST]
        self.node_idx = {nid: i for i, nid in enumerate(self.node_ids)}
        self.n_nodes = len(self.node_ids)
        self.capacity = np.array([CAPACITY_OVERRIDES.get(n[0], TYPE_CAPACITY[n[2]]) for n in NODE_LIST], dtype=np.float64)
        self.node_types = [n[2] for n in NODE_LIST]
        self.xy = np.array([(n[3], n[4]) for n in NODE_LIST], dtype=np.float64)

        self._rng = None
        self._entities = None
        self._flow = None
        self._day_sec = self.n_hours * 3600

    # ------------------------------------------------------------------ sklearn 风格
    def fit(self, X=None, y=None):
        return self

    def get_params(self, deep=True):
        return {
            "n_people": self.n_people, "n_vehicles": self.n_vehicles,
            "density_level": self.density_level,
            "node_curves": self.node_curves, "od_matrix": self.od_matrix,
            "sample_interval": self.sample_interval, "start_hour": self.start_hour,
            "start_minute": self.start_minute, "n_hours": self.n_hours,
            "n_days": self.n_days,
            "day_profiles": self.day_profiles, "start_date": self.start_date.isoformat(),
            "random_state": self.random_state,
        }

    def set_params(self, **params):
        for k, v in params.items():
            if not hasattr(self, k):
                raise ValueError(f"Unknown param: {k}")
            setattr(self, k, v)
        return self

    # ------------------------------------------------------------------ 核心生成
    def _curves(self, weekday="mon"):
        curves = np.zeros((self.n_nodes, 24))
        for i, nid in enumerate(self.node_ids):
            if nid in self.node_curves:
                c = np.asarray(self.node_curves[nid], dtype=np.float64)
                if c.size != 24:
                    raise ValueError(f"node_curves[{nid}] must have 24 values")
                curves[i] = c
            else:
                curves[i] = _default_curve(self.node_types[i], nid, weekday)
        return curves

    def _dst_matrix(self, srcs_out, mins_out, wcur):
        """按 (src_type OD + 容量 + 目的地时变吸引度) 构建逐实体目的地概率。"""
        src_type_idx = {t: i for i, t in enumerate(dict.fromkeys(self.node_types))}
        base = np.zeros((len(src_type_idx), self.n_nodes))
        for t, ti in src_type_idx.items():
            tw = self.od_matrix.get(t, TYPE_OD[t])
            base[ti] = np.array([tw.get(self.node_types[j], 0.0) * self.capacity[j]
                                 for j in range(self.n_nodes)])
        weights = base[[src_type_idx[self.node_types[s]] for s in srcs_out]] * wcur.T[mins_out]
        weights[np.arange(len(srcs_out)), srcs_out] = 0.0
        weights /= weights.sum(axis=1, keepdims=True)
        return weights

    def _sample_entities(self, n_entities, kind, internal_prob, curves):
        """按节点时变曲线 + 泊松到达生成实体。返回 (birth_tick, src, dst, flag)。

        birth_tick 为"距起始时刻（start_hour:start_minute）的秒数"（0 ~
        n_hours*3600-1），跨天偏移由调用方叠加。

        时空语义：内部实体（flag==1，如行人 / 园内车辆）的 src 保留曲线采样
        的节点，即在园区内按时空关系随机出现；外部实体（flag==0，仅园外车辆）
        的 src 强制为三个大门（从 gate 入园）。
        """
        n_min = self.n_hours * 60
        peaking = {"off_peak": 0.8, "peak": 2.0, "ultra_peak": 3.0}[self.density_level]

        hour_grid = self.start_hour + self.start_minute / 60.0 + (np.arange(n_min) * 60 / 3600.0)
        w = np.empty((self.n_nodes, n_min), dtype=np.float64)
        for i in range(self.n_nodes):
            w[i] = np.interp(hour_grid % 24, np.arange(24), curves[i])
        w = np.power(w, peaking)
        w = w / w.sum()

        lam = n_entities * w
        counts = self._rng.poisson(lam)
        flat_idx = np.nonzero(counts.ravel())[0]
        srcs = flat_idx // n_min
        mins = flat_idx % n_min
        repeats = counts.ravel()[flat_idx]
        n_src = int(repeats.sum())
        srcs_out = np.repeat(srcs, repeats).astype(np.int32)
        mins_out = np.repeat(mins, repeats)
        births = (mins_out * 60
                  + self._rng.uniform(0, 60, size=n_src).astype(np.int64))

        od = self._dst_matrix(srcs_out, mins_out, w)
        dsts = np.empty(n_src, dtype=np.int32)
        for k in range(n_src):
            dsts[k] = self._rng.choice(self.n_nodes, p=od[k])

        flags = self._rng.binomial(1, internal_prob, size=n_src).astype(np.int8)
        entrance_idx = np.array([0, 1, 2], dtype=np.int32)
        ext_mask = flags == 0
        if ext_mask.any():
            srcs_out[ext_mask] = self._rng.choice(entrance_idx, size=ext_mask.sum()).astype(np.int32)
        return births, srcs_out, dsts, flags

    def generate(self):
        rng = np.random.default_rng(self.random_state)
        self._rng = rng

        people = {"birth_tick": [], "src_node": [], "dst_node": [], "kind": []}
        vehicles = {"birth_tick": [], "src_node": [], "dst_node": [], "kind": [], "is_internal": []}

        for d in range(self.n_days):
            curves = self._curves(self.day_profiles[d % 7])
            offset = d * self._day_sec
            # 行人 100% 园内生成（internal_prob=1.0 → src 保留曲线节点）；
            # 车辆 internal_prob=0.4 → 40% 园内按曲线、60% 园外从三大门进入。
            pb, ps, pd, _ = self._sample_entities(self.n_people, kind=0, internal_prob=1.0, curves=curves)
            vb, vs, vd, vf = self._sample_entities(self.n_vehicles, kind=1, internal_prob=0.4, curves=curves)
            people["birth_tick"].append(pb + offset)
            people["src_node"].append(ps)
            people["dst_node"].append(pd)
            vehicles["birth_tick"].append(vb + offset)
            vehicles["src_node"].append(vs)
            vehicles["dst_node"].append(vd)
            vehicles["is_internal"].append(vf)

        people = {k: (np.concatenate(v) if v else np.empty(0)) for k, v in people.items()}
        vehicles = {k: (np.concatenate(v) if v else np.empty(0)) for k, v in vehicles.items()}
        people["kind"] = np.zeros(people["birth_tick"].size, dtype=np.int8)
        vehicles["kind"] = np.ones(vehicles["birth_tick"].size, dtype=np.int8)
        vehicles["is_internal"] = vehicles["is_internal"].astype(np.int8)

        density = self._build_density(people, vehicles)
        ds = self._format_density(density)

        dataset = FlowDataset(people=people, vehicles=vehicles,
                              density_series=ds, node_ids=self.node_ids)
        self._flow = dataset
        self._store_entities(people, vehicles)
        return dataset

    def _travel_tick(self, src_idx, dst_idx, speed):
        dx = self.xy[src_idx, 0] - self.xy[dst_idx, 0]
        dy = self.xy[src_idx, 1] - self.xy[dst_idx, 1]
        dist = math.hypot(dx, dy)
        sec = dist / speed
        return int(np.clip(sec, 60, 1800))

    def _build_density(self, people, vehicles):
        n_min = self.n_days * self.n_hours * 60
        occ_p = np.zeros((self.n_nodes, n_min))
        occ_v = np.zeros((self.n_nodes, n_min))

        for pool, occ, speed in ((people, occ_p, 1.3), (vehicles, occ_v, 5.0)):
            if pool["birth_tick"].size == 0:
                continue
            src = pool["src_node"]
            dst = pool["dst_node"]
            birth = pool["birth_tick"]

            wait_min = np.array([
                self._rng.uniform(WAIT_MIN[self.node_types[s]], WAIT_MAX[self.node_types[s]])
                for s in src
            ])
            s_arr = np.clip(birth // 60, 0, n_min - 1).astype(np.int64)
            s_leave = np.clip((birth + wait_min * 60) // 60, 0, n_min - 1).astype(np.int64)
            a_s = np.bincount(src * n_min + s_arr, minlength=self.n_nodes * n_min).reshape(self.n_nodes, n_min)
            l_s = np.bincount(src * n_min + s_leave, minlength=self.n_nodes * n_min).reshape(self.n_nodes, n_min)
            occ += np.cumsum(a_s - l_s, axis=1)

            trav = np.array([self._travel_tick(s, d, speed)
                             for s, d in zip(src, dst)], dtype=np.int64)
            arr = birth + trav
            dwell_min = np.array([
                self._rng.uniform(DWELL_MIN[self.node_types[d]], DWELL_MAX[self.node_types[d]])
                for d in dst
            ])
            arr_min = np.clip(arr // 60, 0, n_min - 1).astype(np.int64)
            leave_min = np.clip((arr + dwell_min * 60) // 60, 0, n_min - 1).astype(np.int64)
            a = np.bincount(dst * n_min + arr_min, minlength=self.n_nodes * n_min).reshape(self.n_nodes, n_min)
            l = np.bincount(dst * n_min + leave_min, minlength=self.n_nodes * n_min).reshape(self.n_nodes, n_min)
            occ += np.cumsum(a - l, axis=1)
        return occ_p, occ_v

    def _format_density(self, density):
        occ_p, occ_v = density
        n_min = occ_p.shape[1]
        n_total = self.n_nodes * n_min
        density_val = occ_p / self.capacity[:, None]

        ticks = np.repeat(np.arange(n_min) * self.sample_interval, self.n_nodes)
        nodes = np.tile(np.arange(self.n_nodes), n_min)
        people = occ_p.T.ravel()
        vehicles = occ_v.T.ravel()
        dens = density_val.T.ravel()
        level = np.where(dens < 0.3, "low", np.where(dens < 0.6, "medium",
                        np.where(dens < 0.9, "high", "critical")))
        gate_status = np.where(dens >= 0.8, 0, np.where(dens >= 0.5, 2, 1))
        flow = np.round(45.0 * np.clip(1.0 - dens, 0, 1), 1)

        return {
            "tick": ticks, "node_idx": nodes, "people": people, "vehicles": vehicles,
            "density": np.round(dens, 4), "level": level,
            "gate_status": gate_status, "gate_flow_rate": flow,
        }

    def _store_entities(self, people, vehicles):
        n = people["birth_tick"].size + vehicles["birth_tick"].size
        dtype = np.dtype([
            ("id", np.int32), ("birth_tick", np.int64), ("kind", np.int8),
            ("src_node", np.int32), ("dst_node", np.int32),
        ])
        arr = np.empty(n, dtype=dtype)
        pids = np.arange(people["birth_tick"].size, dtype=np.int32)
        vids = np.arange(people["birth_tick"].size, people["birth_tick"].size + vehicles["birth_tick"].size, dtype=np.int32)
        arr[:people["birth_tick"].size] = np.rec.fromarrays(
            [pids, people["birth_tick"], people["kind"], people["src_node"], people["dst_node"]], dtype=dtype)
        arr[people["birth_tick"].size:] = np.rec.fromarrays(
            [vids, vehicles["birth_tick"], vehicles["kind"], vehicles["src_node"], vehicles["dst_node"]], dtype=dtype)
        arr.sort(order="birth_tick")
        self._entities = arr

    def sample(self, tick):
        """返回该 tick 出生的实体数组（供 TickEngine 使用）。"""
        if self._entities is None:
            return np.empty(0)
        m = self._entities["birth_tick"] == int(tick)
        return self._entities[m]

    # ------------------------------------------------------------------ 落盘
    def _write_csv(self, path, fieldnames, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(fieldnames)
            writer.writerows(rows)

    def _dump_od_csv(self, dataset, subdir="."):
        out = self.data_dir / subdir
        p = dataset.people
        v = dataset.vehicles
        people_rows = [
            (int(i), int(t), self.node_ids[s], self.node_ids[d], int(k))
            for i, (t, s, d, k) in enumerate(zip(p["birth_tick"], p["src_node"], p["dst_node"], p["kind"]))
        ]
        vid0 = len(people_rows)
        vehicle_rows = [
            (vid0 + int(i), int(t), self.node_ids[s], self.node_ids[d], int(k), int(flag))
            for i, (t, s, d, k, flag) in enumerate(
                zip(v["birth_tick"], v["src_node"], v["dst_node"], v["kind"], v["is_internal"]))
        ]
        self._write_csv(out / "people.csv", ["id", "birth_tick", "src_node", "dst_node", "kind"], people_rows)
        self._write_csv(out / "vehicles.csv", ["id", "birth_tick", "src_node", "dst_node", "kind", "is_internal"], vehicle_rows)

        ds = dataset.density_series
        rows = []
        for k in range(ds["tick"].size):
            tick = int(ds["tick"][k])
            total_sec = int(ds["tick"][k])
            ts = self._fmt_time(total_sec)
            rows.append((tick, ts, self.node_ids[int(ds["node_idx"][k])],
                         int(ds["people"][k]), int(ds["vehicles"][k]),
                         float(ds["density"][k]), ds["level"][k],
                         int(ds["gate_status"][k]), float(ds["gate_flow_rate"][k])))
        self._write_csv(out / "density_series.csv",
                        ["tick", "timestamp", "node_id", "people", "vehicles", "density", "level", "gate_status", "gate_flow_rate"],
                        rows)

    def _fmt_time(self, sec):
        day = sec // self._day_sec
        rem = sec % self._day_sec
        total_min = self.start_minute + rem // 60
        h = (self.start_hour + total_min // 60) % 24
        m = total_min % 60
        s = rem % 60
        day += (self.start_hour + total_min // 60) // 24
        date = self.start_date + datetime.timedelta(days=day)
        return f"{date} {h:02d}:{m:02d}:{s:02d}"

    def to_csv(self, subdir="."):
        """写 CSVs 并返回输出目录。"""
        self._dump_od_csv(self._flow, subdir)
        return self.data_dir / subdir


if __name__ == "__main__":
    out_root = Path(__file__).resolve().parent / "data"

    gen = FlowDataGenerator(n_people=4000, n_vehicles=300, density_level="peak",
                            n_days=7, random_state=42, data_dir=out_root)
    ds = gen.generate()
    gen.to_csv(".")
    print("normal people:", ds.people["birth_tick"].size, "vehicles:", ds.vehicles["birth_tick"].size)

    ultra = FlowDataGenerator(n_people=6000, n_vehicles=400, density_level="ultra_peak",
                              n_days=3, random_state=42, data_dir=out_root)
    ds2 = ultra.generate()
    ultra.to_csv("ultra_peak")
    print("ultra  people:", ds2.people["birth_tick"].size, "vehicles:", ds2.vehicles["birth_tick"].size)
    print("done ->", out_root)
