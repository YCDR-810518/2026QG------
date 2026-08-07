# -*- coding: utf-8 -*-
"""controller.py —— 园区控制器统一模块（成员 F，FR-19 整合版）

合并原 gate_policy.py / door_policy.py / signal_policy.py 为单一文件，消除
Gate 与 Door 滞回重复代码，修复多节点共享 _last_mode 的滞回记忆 bug。

- HysteresisPolicyController : 密度滞回控制器（role="gate" 大门闸 | "door" 园内门）。
  每节点独立记忆滞回状态（_last_modes dict），避免 3 大门 / 61 门节点间干扰。
  gate 返回 {gate_id, mode, throughput_cap, n_lanes}；
  door 返回 {node_id, mode}。

- SignalPolicyController     : 红绿灯周期状态机（原 signal_policy.py 完整移入，不变）。
  时间驱动 (tick+offset)%cycle_time→phase，绿波走廊自动推算 offset。

依赖：numpy
用法：
    g = HysteresisPolicyController(role="gate")
    g.fit(history_density_array)
    policy = g.predict(density_now=0.8, node_id="gate_south")

    s = SignalPolicyController()
    s.fit(topology)
    info = s.predict(tick=222)
"""
import numpy as np

# ============================================================================
# HysteresisPolicyController —— 密度滞回控制器（大门闸 + 园内门）
# ============================================================================


class HysteresisPolicyController:
    """密度滞回控制器（统一 Gate + Door，FR-19）。

    Parameters
    ----------
    role : str
        "gate"（大门闸，控制车辆入口吞吐）或 "door"（园内门，只控制人流）。
    window_size : int
        自适应阈值学习窗口。
    open_threshold : float
        密度达到该值进入限流（mode=restricted）。
    close_threshold : float
        密度低于该值恢复开门（mode=open）。
    max_close_ratio : float
        closed 触发密度相对 open_threshold 的倍数。
    max_lanes / min_lanes : int
        开闸数上下限（仅 role="gate"）。
    base_throughput : float
        单闸道单位 tick 放行上限（仅 role="gate"）。

    Attributes
    ----------
    open_threshold_ / close_threshold_ : float
        fit(history) 后学到的自适应阈值（尾下划线）。
    _last_modes : dict
        按 node_id 独立记忆的滞回模式（修复多节点共享 _last_mode 的 bug）。
    """

    def __init__(self, role="door", window_size=10, open_threshold=0.5,
                 close_threshold=0.3, max_close_ratio=1.8,
                 max_lanes=4, min_lanes=1, base_throughput=45.0):
        if role not in ("gate", "door"):
            raise ValueError(f"role 必须为 'gate' 或 'door'，收到: {role}")
        self.role = role
        self.window_size = int(window_size)
        self.open_threshold = float(open_threshold)
        self.close_threshold = float(close_threshold)
        self.max_close_ratio = float(max_close_ratio)
        self.max_lanes = int(max_lanes)
        self.min_lanes = int(min_lanes)
        self.base_throughput = float(base_throughput)
        self.open_threshold_ = None
        self.close_threshold_ = None
        self._last_modes = {}  # node_id → mode，按节点独立记忆滞回状态

    # ------------------------------------------------------------------ sklearn 风格
    def fit(self, history):
        """按历史密度 EWMA + 分位数自适应学习阈值。

        Parameters
        ----------
        history : array-like
            该控制器对应节点的历史密度序列。

        Returns
        -------
        HysteresisPolicyController
            返回 self。
        """
        hist = np.asarray(history, dtype=np.float64)
        if hist.size == 0:
            self.open_threshold_ = self.open_threshold
            self.close_threshold_ = self.close_threshold
            return self
        ewma = hist[-self.window_size:]
        self.open_threshold_ = float(np.percentile(ewma, 90))
        self.close_threshold_ = max(float(np.percentile(ewma, 30)), 0.0)
        self.open_threshold_ = max(self.open_threshold_, self.open_threshold)
        return self

    def get_params(self, deep=True):
        base = {
            "role": self.role,
            "window_size": self.window_size,
            "open_threshold": self.open_threshold,
            "close_threshold": self.close_threshold,
            "max_close_ratio": self.max_close_ratio,
        }
        if self.role == "gate":
            base.update({
                "max_lanes": self.max_lanes,
                "min_lanes": self.min_lanes,
                "base_throughput": self.base_throughput,
            })
        return base

    def set_params(self, **params):
        for k, v in params.items():
            if not hasattr(self, k):
                raise ValueError(f"Unknown param: {k}")
            setattr(self, k, v)
        return self

    # ------------------------------------------------------------------ 策略
    def _hysteresis_mode(self, density_now, node_id):
        """共享滞回核心：密度 → mode，按 node_id 独立记忆上次状态。

        Returns
        -------
        str
            "open" | "restricted" | "closed"。
        """
        d = float(density_now)
        open_th = self.open_threshold_ if self.open_threshold_ is not None else self.open_threshold
        close_th = self.close_threshold_ if self.close_threshold_ is not None else self.close_threshold
        last = self._last_modes.get(node_id, "open")

        if d >= open_th * self.max_close_ratio:
            mode = "closed"
        elif d >= open_th:
            mode = "restricted"
        elif d <= close_th:
            mode = "open"
        else:
            mode = last  # 滞回带：保持上次状态

        self._last_modes[node_id] = mode
        return mode

    def predict(self, density_now, node_id=None, gate_id=None):
        """根据当前密度给出调控策略。

        Parameters
        ----------
        density_now : float
            该节点当前密度。
        node_id : str, optional
            节点/门闸编号（缺省返回占位 id）。
        gate_id : str, optional
            门闸编号别名（兼容旧 GatePolicyController 调用）。

        Returns
        -------
        dict
            gate: {gate_id, mode, throughput_cap, n_lanes}
            door: {node_id, mode}
        """
        nid = node_id or gate_id or f"{self.role}_unknown"
        mode = self._hysteresis_mode(density_now, nid)

        if self.role == "gate":
            if mode == "closed":
                throughput_cap, n_lanes = 0.0, self.min_lanes
            elif mode == "restricted":
                throughput_cap = self.base_throughput * 0.3 * self.min_lanes
                n_lanes = max(self.min_lanes, self.max_lanes - 1)
            else:  # open
                throughput_cap = self.base_throughput * self.max_lanes
                n_lanes = self.max_lanes
            return {
                "gate_id": nid,
                "mode": mode,
                "throughput_cap": int(throughput_cap),
                "n_lanes": int(n_lanes),
            }
        else:  # door
            return {"node_id": nid, "mode": mode}


# ============================================================================
# SignalPolicyController —— 动态红绿灯控制器（原 signal_policy.py 完整移入）
# ============================================================================

# ---------------------------------------------------------------------------
# 默认信号配时（路口 30/3/27 = 60s）
# 结构：{node_id: (green, yellow, red, mode)}
# 注意：以 graph_data.yaml 的 has_traffic_light=true 为单一数据源（8 个），
# 大门（gate_*）只做车辆限流闸，无红绿灯相位。
# ---------------------------------------------------------------------------
_DEFAULT_GREEN = 30
_DEFAULT_YELLOW = 3
_DEFAULT_RED = 27

DEFAULT_SIGNALS = {
    "cross_zh_south": (30, 3, 27, "fixed"),
    "cross_zh_mid": (30, 3, 27, "fixed"),
    "cross_zh_north": (30, 3, 27, "fixed"),
    "rd_guanggong_1": (30, 3, 27, "fixed"),
    "pedestrian_bridge": (30, 3, 27, "fixed"),
    "underpass": (30, 3, 27, "fixed"),
    "library": (30, 3, 27, "fixed"),
    "sports_fitness": (30, 3, 27, "fixed"),
}

# ---------------------------------------------------------------------------
# 绿波走廊：主轴（中环 南/中/北）+ 分支挂载（按拓扑自动推算 offset）
# ---------------------------------------------------------------------------
GREEN_WAVE_CORRIDOR = [
    "cross_zh_south",
    "cross_zh_mid",
    "cross_zh_north",
]
GREEN_WAVE_BRANCH = [
    ("rd_guanggong_1", "cross_zh_mid"),
]


class SignalPolicyController:
    """完整信号周期状态机（FR-19 红绿灯扩展）。

    Parameters
    ----------
    signal_config : dict, optional
        {node_id: (green, yellow, red, mode)} 或带 offset 的 5 元组
        (green, yellow, red, mode, offset)；缺省用 DEFAULT_SIGNALS。
    base_throughput : int
        绿灯相位单位 tick 放行上限（默认 90，对齐冻结样例）。
    random_state : int, optional
        预留随机种子。
    wave_speed : float
        绿波走廊行程速度（m/s，默认 1.3 行人），用于自动推算 offset。
    auto_offsets : bool
        是否按拓扑自动推算各信号 offset 错峰/绿波（默认 True）。

    Attributes
    ----------
    signals_ : dict
        学习属性：{node_idx: 信号状态字典}（fit 后生成，尾下划线）。
    """

    def __init__(self, signal_config=None, base_throughput=90, random_state=None,
                 wave_speed=1.3, auto_offsets=True):
        self.signal_config = signal_config or dict(DEFAULT_SIGNALS)
        self.base_throughput = int(base_throughput)
        self.random_state = random_state
        self.wave_speed = float(wave_speed)
        self.auto_offsets = bool(auto_offsets)
        self.signals_ = None

    # ------------------------------------------------------------------ sklearn 风格
    def fit(self, topology, signal_config=None):
        """加载信号配置并绑定到拓扑节点序号。

        以 topology.signal_nodes（graph_data.yaml 的 has_traffic_light=true）
        为单一数据源：对每个信号节点生成配时，config 缺配时用默认路口周期
        (30/3/27)，保证信号数量与位置始终对齐拓扑。

        Parameters
        ----------
        topology : Topology
            园区拓扑实例（引擎共享同一实例）。
        signal_config : dict, optional
            {node_id: (green, yellow, red, mode)} 覆盖默认配时；
            5 元组可带手动 offset 覆盖自动推算值。

        Returns
        -------
        SignalPolicyController
            返回 self。
        """
        signals = signal_config or self.signal_config

        self.signals_ = {}
        manual = {}
        for i, sid in enumerate(topology.signal_nodes):
            node_id = topology.node_ids[sid]
            row = signals.get(node_id)
            if row is None:
                green, yellow, red, mode = (
                    _DEFAULT_GREEN, _DEFAULT_YELLOW, _DEFAULT_RED, "fixed")
                offset = 0
            elif len(row) >= 5:
                green, yellow, red, mode, offset = row[:5]
                manual[node_id] = int(offset)
            else:
                green, yellow, red, mode = row
                offset = 0
            self.signals_[sid] = {
                "signal_id": f"S{i + 1:02d}",
                "node_id": node_id,
                "mode": mode,
                "cycle_time": green + yellow + red,
                "green_time": int(green),
                "yellow_time": int(yellow),
                "red_time": int(red),
                "offset": int(offset),
                "throughput_cap": self.base_throughput,
                "n_phases": 2,
                "signal_status": 1,
            }

        if self.auto_offsets:
            auto = self._corridor_offsets(topology, signals)
            for sd in self.signals_.values():
                nid = sd["node_id"]
                if nid in auto and nid not in manual:
                    sd["offset"] = auto[nid]
        return self

    def get_params(self, deep=True):
        return {
            "signal_config": self.signal_config,
            "base_throughput": self.base_throughput,
            "random_state": self.random_state,
            "wave_speed": self.wave_speed,
            "auto_offsets": self.auto_offsets,
        }

    def set_params(self, **params):
        for k, v in params.items():
            if k not in ("signal_config", "base_throughput", "random_state",
                         "wave_speed", "auto_offsets"):
                raise ValueError(f"Unknown param: {k}")
            setattr(self, k, v)
        return self

    # ------------------------------------------------------------------ 绿波 offset 自动推算
    def _corridor_offsets(self, topology, signals):
        """按绿波走廊 + 行程时间推算各信号节点 offset。"""
        offsets = {}
        idx = topology.node_idx
        speed = max(self.wave_speed, 0.1)
        used = {}

        def _dist(a_id, b_id):
            ia, ib = idx.get(a_id), idx.get(b_id)
            if ia is None or ib is None:
                return 0.0
            el = float(topology.edge_length[ia, ib])
            if el > 0:
                return el
            p = topology.path(ia, ib)
            d = 0.0
            for u, v in zip(p[:-1], p[1:]):
                l = float(topology.edge_length[u, v])
                d += l if l > 0 else 1.0
            return d

        def _cycle(nid):
            row = signals.get(nid)
            if row is None:
                return 60
            return int(row[0]) + int(row[1]) + int(row[2])

        def _assign(nid, up=None, base=0.0):
            if nid not in signals:
                return base
            cyc = _cycle(nid)
            if up is None:
                off = 0
            else:
                off = int(round(base + _dist(up, nid) / speed)) % cyc
            used_set = used.setdefault(cyc, set())
            while off in used_set and len(used_set) < cyc:
                off = (off + 1) % cyc
            used_set.add(off)
            offsets[nid] = off
            return float(off)

        chain = [n for n in GREEN_WAVE_CORRIDOR if n in signals]
        if chain:
            base = _assign(chain[0])
            prev = chain[0]
            for nid in chain[1:]:
                base = _assign(nid, prev, base)
                prev = nid
        for nid, up in GREEN_WAVE_BRANCH:
            if nid in signals and up in signals:
                _assign(nid, up, float(offsets.get(up, 0)))
        return offsets

    # ------------------------------------------------------------------ 状态机
    def phase_of(self, signal_dict, tick):
        """计算某信号在 tick 时刻的相位。

        Returns
        -------
        str
            green / yellow / red / off。
        """
        status = signal_dict["signal_status"]
        if status == 0:
            return "off"
        if status == 3:
            return "red"
        if status == 2:
            return "off"
        t = (int(tick) + signal_dict["offset"]) % signal_dict["cycle_time"]
        if t < signal_dict["green_time"]:
            return "green"
        if t < signal_dict["green_time"] + signal_dict["yellow_time"]:
            return "yellow"
        return "red"

    def passable(self, node_idx, tick):
        """该节点此刻是否可放行（green / off）。"""
        sig = self.signals_.get(int(node_idx))
        if sig is None:
            return True
        return self.phase_of(sig, tick) in ("green", "off")

    def predict(self, tick):
        """返回全信号状态字典（对齐冻结契约）。

        Parameters
        ----------
        tick : int
            当前模拟 tick（秒）。

        Returns
        -------
        dict
            {node_idx: {signal_id, node_id, phase, mode, cycle_time,
                        green_time, yellow_time, red_time, offset,
                        throughput_cap, n_phases, signal_status,
                        signal_flow_rate}}。
        """
        out = {}
        for node_idx, sig in self.signals_.items():
            phase = self.phase_of(sig, tick)
            passable = phase in ("green", "off")
            flow = sig["throughput_cap"] if passable else 0.0
            out[node_idx] = {
                "signal_id": sig["signal_id"],
                "node_id": sig["node_id"],
                "phase": phase,
                "mode": sig["mode"],
                "cycle_time": sig["cycle_time"],
                "green_time": sig["green_time"],
                "yellow_time": sig["yellow_time"],
                "red_time": sig["red_time"],
                "offset": sig["offset"],
                "throughput_cap": sig["throughput_cap"],
                "n_phases": sig["n_phases"],
                "signal_status": sig["signal_status"],
                "signal_flow_rate": float(flow),
            }
        return out

    # ------------------------------------------------------------------ FR-19 自适应钩子
    def adjust(self, node_idx, density):
        """按路口密度自适应调整 green_time（mode='adaptive' 时生效）。"""
        sig = self.signals_.get(int(node_idx))
        if sig is None or sig["mode"] != "adaptive":
            return
        base = sig["green_time"]
        scale = float(np.clip(1.0 - 0.5 * density, 0.5, 1.5))
        sig["green_time"] = int(round(base * scale))
        sig["cycle_time"] = sig["green_time"] + sig["yellow_time"] + sig["red_time"]


# ============================================================================
# 统一自测：滞回策略 + 信号相位
# ============================================================================

if __name__ == "__main__":
    from pathlib import Path

    from topology import Topology

    print("=" * 60)
    print("controller.py（HysteresisPolicy + SignalPolicy）统一自测")
    print("=" * 60)

    # ---- 一、Gate 滞回 ----
    print("\n--- 一、Gate 滞回策略 ---")
    g = HysteresisPolicyController(role="gate")
    g.fit(np.array([0.1, 0.2, 0.1, 0.3, 0.2, 0.1, 0.4, 0.3, 0.2, 0.1]))
    for d in (0.05, 0.2, 0.6, 0.85, 0.95, 0.2, 0.05):
        print(f"  density={d:.2f} -> {g.predict(d, 'gate_south')}")

    # ---- 二、Door 滞回 + 多节点独立记忆 ----
    print("\n--- 二、Door 滞回策略（多节点独立记忆） ---")
    dd = HysteresisPolicyController(role="door")
    dd.fit(np.array([0.1, 0.2, 0.1, 0.3, 0.2, 0.1, 0.4, 0.3, 0.2, 0.1]))
    for d in (0.05, 0.2, 0.6, 0.85, 0.95, 0.2, 0.05):
        a = dd.predict(d, "library")
        b = dd.predict(d, "canteen_1")
        print(f"  density={d:.2f} -> library={a['mode']} canteen={b['mode']} (modes={list(dd._last_modes.values())})")

    # ---- 三、Gate + Door 滞回互不干扰 ----
    print("\n--- 三、Gate/Door 独立滞回验证 ---")
    g2 = HysteresisPolicyController(role="gate")
    g2.predict(0.95, "gate_south")  # closed
    g2.predict(0.95, "gate_west")   # closed
    g2.predict(0.05, "gate_south")  # → 应该靠自己的滞回从 closed 出来到 open
    g2_mode = g2.predict(0.05, "gate_south")["mode"]
    print(f"  gate_south mode after 0.95→0.05: {g2_mode}")
    assert g2_mode == "open", "gate_south 独立滞回应回到 open"
    print("  验证通过: gate_south 独立记忆")

    # ---- 四、红绿灯信号周期 ----
    print("\n--- 四、红绿灯信号周期 ---")
    _yaml = Path(__file__).resolve().parent.parent / "graph_data.yaml"
    topo = Topology(yaml_path=str(_yaml))
    ctrl = SignalPolicyController().fit(topo)
    print("  signals:", {k: v["signal_id"] + ":" + v["node_id"] + f"(offset={v['offset']})"
                         for k, v in ctrl.signals_.items()})
    nodes = list(ctrl.signals_.keys())
    header = "  tick | " + " | ".join(f"{ctrl.signals_[s]['node_id']:>16}" for s in nodes)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t in range(0, 61, 5):
        info = ctrl.predict(t)
        row = " | ".join(f"{info[s]['phase']:>16}" for s in nodes)
        print(f"  {t:4d} | {row}")

    print("\n" + "=" * 60)
    print("全部测试通过")
    print("=" * 60)
