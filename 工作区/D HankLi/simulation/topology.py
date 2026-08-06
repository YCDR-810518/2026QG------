# -*- coding: utf-8 -*-
"""topology.py —— 园区交通网络核心模块（成员 A+F 整合版）

整合 A 的 DjShortCut（TrafficNetwork + ShortestPathFinder）与 F 的拓扑适配
（Topology）为单一模块，消除功能重复与接口冲突：

- TrafficNetwork   : 从 graph_data.yaml 加载拓扑，管理 大门闸/园内门闸/红绿灯
                     状态缓存（统一词表 open/restricted/closed），支持控制状态
                     缓存 JSON 持久化。
- ShortestPathFinder : sklearn 风格 Dijkstra，人车分流（门只管人，车强制 open）。
- Topology         : 引擎适配层（派生数组 + 掩码 + 路径缓存 + 控制状态同步 +
                     缓存持久化），对外 API 与原有 topology.Topology 一致。

对外接口：
    network  = TrafficNetwork.from_yaml("graph_data.yaml")
    planner  = ShortestPathFinder(penalty_factor=10.0).fit(network)
    path     = planner.predict(src="gate_south", dst="canteen_1", kind=0)
    topo     = Topology()            # 引擎适配层
    topo.path(src_idx, dst_idx, kind)

依赖：numpy、pyyaml；flow_data_generator（容量/停留参数，经 config.py 注入 sys.path）
"""
import datetime
import heapq
import json
import logging
import sys
from pathlib import Path

import numpy as np

from config import get_config

# ---------------------------------------------------------------------------
# 复用 flow_data_generator 的容量/停留参数（保持与已交付 density_series 口径一致）
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flow_data_generator import (  # noqa: E402
    TYPE_CAPACITY,
    CAPACITY_OVERRIDES,
    WAIT_MIN,
    WAIT_MAX,
    DWELL_MIN,
    DWELL_MAX,
)

_PED_SPEED = 1.3   # 行人期望速度 m/s（与生成器 _travel_tick 一致）
_VEH_SPEED = 5.0   # 车辆巡航速度 m/s

# 统一日志名（兼容 gen_macro 等外部模块对 "topology" / "simulation.topology" 的压制）
logger = logging.getLogger(__name__)


def _configure_logger():
    """配置日志格式（模块首次 import 时执行一次）"""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s.%(funcName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


_configure_logger()


# ============================================================================
# TrafficNetwork —— 交通网络拓扑数据结构（原 DjShortCut）
# ============================================================================


class TrafficNetwork:
    """校园交通网络拓扑数据结构。

    从 graph_data.yaml 加载节点、边与类型颜色，并构建无向邻接表
    以支持高效的最短路径查询。统一管理 大门闸/园内门闸/红绿灯 状态缓存。

    Parameters
    ----------
    nodes : dict
        节点字典，key 为 node_id (str)，value 为节点属性字典。
    edges : list
        边列表，每项包含 edgeId, nodes (list[str]), length, weight, capacity。
    node_types : dict
        节点类型 type_name (str) → 颜色 hex (str) 的映射。

    Attributes
    ----------
    nodes / edges / node_types : dict / list / dict
        构造传入的拓扑数据。
    _adjacency : dict
        无向邻接表，key 为 node_id，value 为 [(neighbor_id, weight), ...]。
    _gate_states : dict
        大门闸状态缓存（统一词表 open/restricted/closed，仅 3 个 entrance 节点，
        控制车辆入口吞吐，不影响边权）。
    _door_states : dict
        园内门闸状态缓存（统一词表 open/restricted/closed，控制人流，
        影响行人 Dijkstra 边权；车辆忽略）。
    _signal_states : dict
        红绿灯状态缓存（key 为 node_id，value 含 phase，影响边权）。
    """

    # 统一状态词表：大门闸与园内门闸共用（修复原 gate close/restrict vs door closed/restricted 冲突）
    VALID_GATE_STATES = {"open", "restricted", "closed"}
    VALID_DOOR_STATES = {"open", "restricted", "closed"}
    VALID_SIGNAL_PHASES = {"green", "yellow", "red", "off"}

    # 惩罚常量（门/红绿灯，统一单一来源）
    SIGNAL_RED_WEIGHT = 1000.0
    SIGNAL_YELLOW_WEIGHT = 3.0
    DOOR_RESTRICTED_FACTOR = 10.0

    def __init__(self, nodes, edges, node_types):
        self.nodes = nodes
        self.edges = edges
        self.node_types = node_types
        self._adjacency = None
        self._gate_states = {}
        self._signal_states = {}
        self._door_states = {}
        self._yaml_path = None

    # ------------------------------------------------------------------
    # 构造方法
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_path):
        """从 graph_data.yaml 加载拓扑配置并构建 TrafficNetwork 实例。

        Parameters
        ----------
        yaml_path : str or Path
            graph_data.yaml 的文件路径。

        Returns
        -------
        TrafficNetwork
            加载完成的交通网络拓扑实例。

        Raises
        ------
        FileNotFoundError
            当 yaml_path 不存在时抛出。
        yaml.YAMLError
            当 YAML 解析失败时抛出。
        """
        import yaml

        yaml_path = Path(yaml_path)
        if not yaml_path.exists():
            raise FileNotFoundError(f"YAML 文件不存在: {yaml_path}")

        logger.info("从 %s 加载拓扑配置", yaml_path)
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        nodes = data.get("nodes", {})
        edges = data.get("edges", [])
        node_types = data.get("node_types", {})

        logger.info(
            "加载完成: %d 个节点, %d 条边, %d 种节点类型",
            len(nodes), len(edges), len(node_types),
        )
        instance = cls(nodes=nodes, edges=edges, node_types=node_types)
        instance._yaml_path = yaml_path
        return instance

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_adjacency(self):
        """构建无向邻接表（惰性求值，首次调用时构建）。"""
        if self._adjacency is not None:
            return

        self._adjacency = {}
        for node_id in self.nodes:
            self._adjacency[node_id] = []

        for edge in self.edges:
            src, dst = edge["nodes"][0], edge["nodes"][1]
            weight = edge.get("weight", 1.0)

            if src in self._adjacency:
                self._adjacency[src].append((dst, float(weight)))
            if dst in self._adjacency:
                self._adjacency[dst].append((src, float(weight)))

        logger.debug("邻接表构建完成: %d 个节点", len(self._adjacency))

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_node(self, node_id):
        """获取节点属性字典，节点不存在时返回 None。"""
        return self.nodes.get(node_id)

    def get_neighbors(self, node_id):
        """获取指定节点的邻接节点及边权列表 [(neighbor_id, weight), ...]。"""
        self._build_adjacency()
        return self._adjacency.get(node_id, [])

    def get_edge_weight(self, src, dst):
        """获取两节点间的边权值（weight 字段），边不存在时返回 None。"""
        self._build_adjacency()
        for neighbor_id, weight in self._adjacency.get(src, []):
            if neighbor_id == dst:
                return weight
        return None

    # ------------------------------------------------------------------
    # 大门闸状态管理（统一词表 open/restricted/closed，仅车辆入口吞吐，不影响边权）
    # ------------------------------------------------------------------

    def set_gate_states(self, gate_states):
        """批量更新大门闸状态缓存。

        Parameters
        ----------
        gate_states : dict
            {node_id: "open" | "restricted" | "closed"} 的映射。

        Returns
        -------
        TrafficNetwork
            返回 self，支持链式调用。

        Raises
        ------
        ValueError
            当某状态值不在 VALID_GATE_STATES 中时抛出。
        """
        for node_id, state in gate_states.items():
            if state not in self.VALID_GATE_STATES:
                raise ValueError(
                    f"无效大门闸状态 '{state}' (节点: {node_id})，"
                    f"有效值: {self.VALID_GATE_STATES}"
                )
            if node_id not in self.nodes:
                logger.warning("节点 %s 不在拓扑中，跳过大门闸状态设置", node_id)
                continue

        self._gate_states.update(gate_states)
        logger.info("已更新 %d 个节点的大门闸状态", len(gate_states))
        return self

    def get_gate_states(self):
        """获取当前全部大门闸状态缓存。"""
        return dict(self._gate_states)

    def get_gated_nodes(self):
        """获取所有带有大门闸的节点 ID 列表（has_gate == True）。"""
        return [
            node_id
            for node_id, attr in self.nodes.items()
            if attr.get("has_gate", False)
        ]

    def _resolve_gate_state(self, node_id, override_states=None):
        """解析节点的最终大门闸状态（override > 缓存 > 默认 "open"）。"""
        if override_states and node_id in override_states:
            return override_states[node_id]
        if node_id in self._gate_states:
            return self._gate_states[node_id]
        return "open"

    # ------------------------------------------------------------------
    # 红绿灯状态管理
    # ------------------------------------------------------------------

    def set_signal_states(self, signal_states):
        """批量更新红绿灯状态缓存。

        Parameters
        ----------
        signal_states : dict
            {node_id: {"phase": "green"|"yellow"|"red"|"off", ...}} 的映射。

        Returns
        -------
        TrafficNetwork
            返回 self，支持链式调用。

        Raises
        ------
        ValueError
            当 phase 值不在 VALID_SIGNAL_PHASES 中时抛出。
        """
        for node_id, info in signal_states.items():
            if not isinstance(info, dict) or "phase" not in info:
                raise ValueError(
                    f"信号状态格式错误 (节点: {node_id})，"
                    f"需为 {{\"phase\": \"...\", ...}} dict"
                )
            phase = info["phase"]
            if phase not in self.VALID_SIGNAL_PHASES:
                raise ValueError(
                    f"无效红绿灯相位 '{phase}' (节点: {node_id})，"
                    f"有效值: {self.VALID_SIGNAL_PHASES}"
                )
            if node_id not in self.nodes:
                logger.warning("节点 %s 不在拓扑中，跳过红绿灯状态设置", node_id)
                continue

        self._signal_states.update(signal_states)
        logger.info("已更新 %d 个节点的红绿灯状态", len(signal_states))
        return self

    def get_signal_states(self):
        """获取当前全部红绿灯状态缓存。"""
        return dict(self._signal_states)

    def get_signaled_nodes(self):
        """获取所有带有红绿灯的节点 ID 列表（has_traffic_light == True）。"""
        return [
            node_id
            for node_id, attr in self.nodes.items()
            if attr.get("has_traffic_light", False)
        ]

    def _resolve_signal_phase(self, node_id, override_states=None):
        """解析节点的最终红绿灯相位（override > 缓存 > 默认 "green"）。"""
        if override_states and node_id in override_states:
            return override_states[node_id].get("phase", "green")
        if node_id in self._signal_states:
            return self._signal_states[node_id].get("phase", "green")
        return "green"

    # ------------------------------------------------------------------
    # 园内门闸状态管理（统一词表，只控制人流，影响行人边权）
    # ------------------------------------------------------------------

    def set_door_states(self, door_states):
        """批量更新园内门闸状态缓存。

        Parameters
        ----------
        door_states : dict
            {node_id: "open" | "restricted" | "closed"} 的映射。

        Returns
        -------
        TrafficNetwork
            返回 self，支持链式调用。

        Raises
        ------
        ValueError
            当某状态值不在 VALID_DOOR_STATES 中时抛出。
        """
        for node_id, state in door_states.items():
            if state not in self.VALID_DOOR_STATES:
                raise ValueError(
                    f"无效门状态 '{state}' (节点: {node_id})，"
                    f"有效值: {self.VALID_DOOR_STATES}"
                )
            if node_id not in self.nodes:
                logger.warning("节点 %s 不在拓扑中，跳过门状态设置", node_id)
                continue

        self._door_states.update(door_states)
        logger.info("已更新 %d 个节点的门状态", len(door_states))
        return self

    def get_door_states(self):
        """获取当前全部园内门闸状态缓存。"""
        return dict(self._door_states)

    def get_doored_nodes(self):
        """获取所有带门节点（全部节点均可设门）。"""
        return list(self.nodes.keys())

    def _resolve_door_state(self, node_id, override_states=None):
        """解析节点的最终门状态（override > 缓存 > 默认 "open"）。"""
        if override_states and node_id in override_states:
            return override_states[node_id]
        if node_id in self._door_states:
            return self._door_states[node_id]
        return "open"

    # ------------------------------------------------------------------
    # 有效边权（门 + 红绿灯 惩罚的单一来源）
    # ------------------------------------------------------------------

    def effective_cost(self, src_id, dst_id, kind=0, door_states=None,
                       signal_states=None, base=None, penalty_factor=10.0,
                       dest_id=None):
        """计算边 (src_id → dst_id) 的有效权值（含门/红绿灯动态修正）。

        - kind=0（行人）：门生效，closed 且非终点 → 不可通行（返回 None），
          restricted → ×penalty_factor；
        - kind=1（车辆）：门强制 open，完全不受门影响；
        - 红绿灯对两类实体均生效：red/off 且非终点 → ×1000，yellow → ×3；
        - 终点例外：目标节点 door closed / 红灯 时仍可达（仅终点）。

        Parameters
        ----------
        src_id / dst_id : str
            边两端节点 ID。
        kind : int, default=0
            实体类型：0=行人, 1=车辆。
        door_states / signal_states : dict, optional
            实时状态覆盖（override > 缓存 > 默认）。
        base : float, optional
            原始边权（缺省自动查 get_edge_weight）。
        penalty_factor : float
            restricted 门惩罚倍数。
        dest_id : str, optional
            本次查询的总目标节点 ID（用于终点例外）。

        Returns
        -------
        float or None
            有效权值；不可通行（closed 非终点）时返回 None。
        """
        if base is None:
            base = self.get_edge_weight(src_id, dst_id)
        if base is None:
            return None
        w = float(base)
        kind = int(kind)
        is_dst = (dest_id is not None and dst_id == dest_id)

        if kind == 0:
            door = self._resolve_door_state(dst_id, door_states)
            if door == "closed" and not is_dst:
                w *= penalty_factor * 100
            elif door == "restricted":
                w *= penalty_factor

        phase = self._resolve_signal_phase(dst_id, signal_states)
        if phase in ("red", "off") and not is_dst:
            w *= self.SIGNAL_RED_WEIGHT
        elif phase == "yellow":
            w *= self.SIGNAL_YELLOW_WEIGHT

        return w

    # ------------------------------------------------------------------
    # 控制状态缓存持久化（对齐 gen_macro A 精简版契约）
    # ------------------------------------------------------------------

    def get_state_snapshot(self):
        """当前控制状态缓存快照 {gate_states, door_states, signal_states}。"""
        return {
            "gate_states": dict(self._gate_states),
            "door_states": dict(self._door_states),
            "signal_states": dict(self._signal_states),
        }

    def load_state(self, state_dict):
        """从字典载入控制状态（清空后写入，校验词表）。"""
        gate_states = dict(state_dict.get("gate_states", {}))
        door_states = dict(state_dict.get("door_states", {}))
        signal_states = dict(state_dict.get("signal_states", {}))
        self._gate_states = {}
        self._door_states = {}
        self._signal_states = {}
        if gate_states:
            self.set_gate_states(gate_states)
        if door_states:
            self.set_door_states(door_states)
        if signal_states:
            self.set_signal_states(signal_states)
        return self

    def export_state(self, path):
        """把控制状态缓存导出为 JSON（network 网络缓存持久化）。

        Parameters
        ----------
        path : str or Path
            输出 JSON 路径。

        Returns
        -------
        Path
            写出的文件路径。
        """
        out = {
            "version": 1,
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            **self.get_state_snapshot(),
        }
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        logger.info("控制状态缓存导出 -> %s", out_path)
        return out_path

    def import_state(self, path):
        """从 JSON 载入控制状态缓存。

        Parameters
        ----------
        path : str or Path
            之前 export_state 写出的 JSON 路径。

        Returns
        -------
        TrafficNetwork
            返回 self。
        """
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        self.load_state(data)
        logger.info("控制状态缓存导入 <- %s", path)
        return self


# ============================================================================
# ShortestPathFinder —— sklearn 风格 Dijkstra 最短路径查找器
# ============================================================================


class ShortestPathFinder:
    """Dijkstra 最短路径查找器（sklearn 风格接口，人车分流）。

    使用最小堆优化的 Dijkstra 算法，在 TrafficNetwork 拓扑中计算两节点之间
    的最短路径。支持动态门/红绿灯对边权的实时影响：

    - 门（doorId）：只控制人（kind=0），closed 绕行 / restricted ×penalty；
      车辆（kind=1）强制 open，完全不受门影响。
    - 红绿灯：对两类实体均生效（保持不变）。

    Parameters
    ----------
    penalty_factor : float, default=10.0
        门状态为 "restricted" 时的边权惩罚倍数。

    Attributes
    ----------
    penalty_factor : float
        受限门闸的惩罚倍数（构造参数）。
    graph_ : TrafficNetwork or None
        fit 后设置的交通网络拓扑实例。
    n_nodes_ / node_ids_ : int / list
        fit 后的学习属性。
    """

    def __init__(self, penalty_factor=10.0):
        self.penalty_factor = float(penalty_factor)
        self.graph_ = None
        self.n_nodes_ = None
        self.node_ids_ = None

    # ------------------------------------------------------------------
    # fit
    # ------------------------------------------------------------------

    def fit(self, graph):
        """加载交通网络拓扑，构建内部加速数据结构。

        Parameters
        ----------
        graph : TrafficNetwork
            从 graph_data.yaml 加载的交通网络拓扑实例。

        Returns
        -------
        ShortestPathFinder
            返回 self，支持链式调用。

        Raises
        ------
        TypeError
            当 graph 不是 TrafficNetwork 实例时抛出。
        """
        if not isinstance(graph, TrafficNetwork):
            raise TypeError(
                f"graph 必须是 TrafficNetwork 实例，收到: {type(graph).__name__}"
            )

        logger.info("fit: 加载交通网络 (%d 节点)", len(graph.nodes))
        self.graph_ = graph
        self.n_nodes_ = len(graph.nodes)
        self.node_ids_ = list(graph.nodes.keys())

        graph._build_adjacency()

        logger.info("fit 完成: %d 个节点已就绪", self.n_nodes_)
        return self

    # ------------------------------------------------------------------
    # predict —— 最短路径查询（人车分流）
    # ------------------------------------------------------------------

    def predict(self, src, dst, kind=0, door_states=None, signal_states=None):
        """计算从 src 到 dst 的最短路径（支持动态门和红绿灯）。

        Parameters
        ----------
        src : str
            起点节点 ID。
        dst : str
            终点节点 ID。
        kind : int, default=0
            实体类型：0=行人（门生效），1=车辆（忽略门）。
        door_states : dict, optional
            实时门状态覆盖 {node_id: "open"|"restricted"|"closed"}。
        signal_states : dict, optional
            实时红绿灯状态覆盖 {node_id: {"phase": ...}}。

        Returns
        -------
        list of str
            从 src 到 dst 的最短路径节点 ID 列表（含起点和终点）。

        Raises
        ------
        RuntimeError
            当尚未调用 fit 时抛出。
        ValueError
            当 src / dst 不在图中、kind 非法，或两点不可达时抛出。
        """
        if self.graph_ is None:
            raise RuntimeError("请先调用 fit(graph) 加载交通网络")

        kind = int(kind)
        if kind not in (0, 1):
            raise ValueError(f"kind 必须为 0(行人) 或 1(车辆)，收到: {kind}")

        graph = self.graph_

        if src not in graph.nodes:
            raise ValueError(f"起点节点不在图中: {src}")
        if dst not in graph.nodes:
            raise ValueError(f"终点节点不在图中: {dst}")

        if src == dst:
            logger.debug("src == dst，直接返回单节点路径")
            return [src]

        log_parts = [f"计算最短路径: {src} → {dst} (kind={kind})"]
        if door_states:
            log_parts.append(f"(门覆盖 {len(door_states)} 节点)")
        if signal_states:
            log_parts.append(f"(信号覆盖 {len(signal_states)} 节点)")
        logger.info(" ".join(log_parts))

        path, total_weight = self._dijkstra(src, dst, kind, door_states, signal_states)

        if path is None:
            print(f"\n[DEBUG] 节点不可达: {src} → {dst} (kind={kind})")
            closed_doors = {k: v for k, v in graph._door_states.items() if v == "closed"}
            restricted_doors = {k: v for k, v in graph._door_states.items() if v == "restricted"}
            red_signals = {k: v for k, v in graph._signal_states.items() if v.get("phase") in ("red", "off")}
            if closed_doors:
                print(f"  closed doors: {closed_doors}")
            if restricted_doors:
                print(f"  restricted doors: {restricted_doors}")
            if red_signals:
                print(f"  red/off signals: {red_signals}")
            if not closed_doors and not restricted_doors and not red_signals:
                print(f"  (无 door/signal 阻断，可能是图本身不连通)")
            raise ValueError(f"节点不可达: {src} → {dst}")

        logger.info(
            "最短路径: %d 步, 总权值 %.2f, 路径: %s",
            len(path) - 1, total_weight, " → ".join(path),
        )
        return path

    # ------------------------------------------------------------------
    # Dijkstra 核心算法（门感知 + 红绿灯感知 + 人车分流）
    # ------------------------------------------------------------------

    def _dijkstra(self, src, dst, kind, door_states=None, signal_states=None):
        """Dijkstra 最短路径算法（最小堆实现）。

        松弛边时统一调用 graph.effective_cost（门/红绿灯惩罚单一来源）。

        Returns
        -------
        (list or None, float)
            (最短路径节点 ID 列表, 总权值)，不可达时返回 (None, 0.0)。
        """
        graph = self.graph_

        distances = {node_id: float("inf") for node_id in graph.nodes}
        predecessors = {node_id: None for node_id in graph.nodes}
        distances[src] = 0.0

        priority_queue = [(0.0, src)]
        visited = set()

        while priority_queue:
            current_dist, current_node = heapq.heappop(priority_queue)

            if current_node in visited:
                continue
            visited.add(current_node)

            if current_node == dst:
                break

            for neighbor, weight in graph.get_neighbors(current_node):
                if neighbor in visited:
                    continue

                eff = graph.effective_cost(
                    current_node, neighbor, kind=kind,
                    door_states=door_states, signal_states=signal_states,
                    base=float(weight), penalty_factor=self.penalty_factor,
                    dest_id=dst,
                )
                if eff is None:
                    continue

                new_dist = current_dist + eff
                if new_dist < distances[neighbor]:
                    distances[neighbor] = new_dist
                    predecessors[neighbor] = current_node
                    heapq.heappush(priority_queue, (new_dist, neighbor))

        if distances[dst] == float("inf"):
            return None, 0.0

        path = []
        current = dst
        while current is not None:
            path.append(current)
            current = predecessors[current]
        path.reverse()

        return path, distances[dst]

    # ------------------------------------------------------------------
    # sklearn 参数管理接口
    # ------------------------------------------------------------------

    def get_params(self, deep=True):
        """获取当前超参数。"""
        return {"penalty_factor": self.penalty_factor}

    def set_params(self, **params):
        """设置超参数。

        Raises
        ------
        ValueError
            当传入未知参数时抛出。
        """
        for key, value in params.items():
            if not hasattr(self, key):
                raise ValueError(f"Unknown param: {key}")
            setattr(self, key, value)
        return self


# ============================================================================
# Topology —— 引擎适配层（原 F 的拓扑封装）
# ============================================================================


class Topology:
    """园区交通拓扑（节点/边/容量/信号门闸 + 最短路径缓存 + 缓存持久化）。

    内部持有 TrafficNetwork 与 ShortestPathFinder，为仿真引擎提供：
    - node_id ↔ node_idx 互译（与生成器 NODE_LIST 对齐，8.2 钉死项）；
    - 派生数组：edge_length / capacities / node_types / is_signal / is_gate；
    - 人车分流路径查询 path(src_idx, dst_idx, kind)（经 planner.predict(kind=kind)）；
    - 控制状态同步 sync_control_states（直传 network.set_*，词表统一）；
    - 网络缓存持久化 export_state / export_cache / import_state / import_cache。

    Parameters
    ----------
    yaml_path : str or Path
        A 输出的 graph_data.yaml 路径。
    """

    def __init__(self, yaml_path=None):
        cfg = get_config()  # 触发 config 注入 项目目录 到 sys.path

        yaml_path = Path(yaml_path) if yaml_path else cfg["topology"]["file"]
        if not yaml_path.exists():
            raise FileNotFoundError(f"拓扑文件不存在: {yaml_path}")

        self.network = TrafficNetwork.from_yaml(yaml_path)
        self.planner = ShortestPathFinder().fit(self.network)

        self.node_ids = list(self.network.nodes.keys())
        self.node_idx = {nid: i for i, nid in enumerate(self.node_ids)}
        self.n_nodes = len(self.node_ids)
        self.node_types = [self.network.nodes[nid]["type"] for nid in self.node_ids]
        self.type_idx = {t: i for i, t in enumerate(dict.fromkeys(self.node_types))}
        self.node_type_idx = np.array([self.type_idx[t] for t in self.node_types], dtype=np.int32)

        self.xy = np.array(
            [[self.network.nodes[nid]["x"], self.network.nodes[nid]["y"]] for nid in self.node_ids],
            dtype=np.float64,
        )

        self._build_edge_length()
        self._build_capacity()

        # 掩码统一走 DjShortCut 公开查询接口
        signaled = set(self.network.get_signaled_nodes())
        gated = set(self.network.get_gated_nodes())
        self.is_signal = np.array([nid in signaled for nid in self.node_ids], dtype=np.bool_)
        self.is_gate = np.array([nid in gated for nid in self.node_ids], dtype=np.bool_)
        self.is_entrance = np.asarray([t == "entrance" for t in self.node_types], dtype=np.bool_)

        self.signal_nodes = [i for i in range(self.n_nodes) if self.is_signal[i]]
        self.gate_nodes = [i for i in range(self.n_nodes) if self.is_gate[i] and self.is_entrance[i]]

        self._path_cache = {}
        self._state_sig = None
        self._ctrl_version = 0
        self._cached_version = 0

    # ------------------------------------------------------------------ 内部
    def _build_edge_length(self):
        self.edge_length = np.full((self.n_nodes, self.n_nodes), -1.0, dtype=np.float32)
        for edge in self.network.edges:
            a, b = edge["nodes"][0], edge["nodes"][1]
            if a not in self.node_idx or b not in self.node_idx:
                continue
            i, j = self.node_idx[a], self.node_idx[b]
            length = float(edge.get("length", 1.0))
            self.edge_length[i, j] = length
            self.edge_length[j, i] = length

    def _build_capacity(self):
        caps = np.zeros(self.n_nodes, dtype=np.float64)
        for i, nid in enumerate(self.node_ids):
            caps[i] = CAPACITY_OVERRIDES.get(nid, TYPE_CAPACITY[self.node_types[i]])
        self.capacities = caps

    def _control_sig(self):
        """当前 network 控制状态指纹（用于路径缓存失效判定）。"""
        gate = dict(self.network._gate_states)
        door = dict(self.network._door_states)
        sig = dict(self.network._signal_states)
        return (tuple(sorted(gate.items())),
                tuple(sorted(door.items())),
                tuple(sorted(sig.items())))

    # ------------------------------------------------------------------ 查询
    def node(self, node_id):
        """按编码取节点序号，不存在返回 -1。"""
        return self.node_idx.get(node_id, -1)

    def node_name(self, idx):
        """按序号取节点编码。"""
        return self.node_ids[idx]

    def edge_len(self, src_idx, dst_idx):
        """两节点间边长（米）；无直接边返回 -1。"""
        return self.edge_length[src_idx, dst_idx]

    # ------------------------------------------------------------------ 控制状态同步
    def sync_control_states(self, gate_states=None, door_states=None, signal_states=None):
        """把大门/园内门闸/红绿灯控制状态写入共享 TrafficNetwork 缓存。

        状态指纹不变时零开销；变化时更新 network 缓存并使路径缓存失效
        （新生成实体的最短路径随状态动态绕行）。

        语义（词表统一 open/restricted/closed）：
        - gate_states 仅限流入园车辆吞吐，不影响边权。
        - door_states 只控制人流，影响行人 Dijkstra 边权；车辆路径强制 open。
        - signal_states 红绿灯相位，影响边权（保持不变）。

        Returns
        -------
        Topology
            返回 self。
        """
        gate_states = dict(gate_states or {})
        door_states = dict(door_states or {})
        signal_states = dict(signal_states or {})
        sig = (tuple(sorted(gate_states.items())),
               tuple(sorted(door_states.items())),
               tuple(sorted(signal_states.items())))
        if sig == self._state_sig:
            return self
        self.network.set_gate_states(gate_states)
        self.network.set_door_states(door_states)
        self.network.set_signal_states(signal_states)
        self._state_sig = sig
        self._ctrl_version += 1
        return self

    def get_gate_states(self):
        """当前大门闸状态缓存（TrafficNetwork 视图）。"""
        return self.network.get_gate_states()

    def get_door_states(self):
        """当前园内门闸状态缓存（TrafficNetwork 视图）。"""
        return self.network.get_door_states()

    def get_signal_states(self):
        """当前红绿灯状态缓存（TrafficNetwork 视图）。"""
        return self.network.get_signal_states()

    # ------------------------------------------------------------------ 路径
    def path(self, src_idx, dst_idx, kind=0):
        """src → dst 的最短路径节点序号序列（惰性缓存 Dijkstra 结果）。

        人车分流：门（doorId）只控制人，不限制车。
        - kind=0（行人）：自动感知 network 门状态缓存，closed 绕行 / restricted 限流；
        - kind=1（车辆）：门状态强制全部 open，完全不受门影响；
        - 红绿灯对两类实体均生效（信号逻辑保持不变）。

        Parameters
        ----------
        src_idx / dst_idx : int
            节点序号。
        kind : int, default=0
            实体类型：0=行人, 1=车辆。

        Returns
        -------
        np.ndarray of int32
            路径节点序号序列（含 src 与 dst）。
        """
        kind = int(kind)
        if src_idx == dst_idx:
            return np.array([src_idx], dtype=np.int32)
        if self._ctrl_version != self._cached_version:
            self._path_cache.clear()
            self._cached_version = self._ctrl_version
        key = (src_idx, dst_idx, kind)
        if key not in self._path_cache:
            raw = self.planner.predict(
                self.node_ids[src_idx], self.node_ids[dst_idx], kind=kind,
            )
            self._path_cache[key] = np.array([self.node_idx[n] for n in raw], dtype=np.int32)
        return self._path_cache[key]

    def path_nodes(self, src_idx, dst_idx, kind=0):
        """同 path，返回编码列表（调试用）。"""
        return [self.node_ids[i] for i in self.path(src_idx, dst_idx, kind)]

    def n_cached_paths(self):
        """已缓存路径数。"""
        return len(self._path_cache)

    # ------------------------------------------------------------------ 缓存持久化
    def export_cache(self, path):
        """导出网络缓存（控制状态 + 路径缓存）为 JSON，用于跨进程热启动。

        Parameters
        ----------
        path : str or Path
            输出 JSON 路径。

        Returns
        -------
        Path
            写出的文件路径。
        """
        out = {
            "version": 1,
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "control": self.network.get_state_snapshot(),
            "paths": {
                f"{self.node_ids[s]}|{self.node_ids[d]}|{k}": [self.node_ids[i] for i in p]
                for (s, d, k), p in self._path_cache.items()
            },
        }
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        logger.info("网络缓存导出 -> %s (%d 条路径)", out_path, len(out["paths"]))
        return out_path

    def import_cache(self, path):
        """导入网络缓存（控制状态 + 路径缓存），免去冷启动重算 Dijkstra。

        Parameters
        ----------
        path : str or Path
            之前 export_cache 写出的 JSON 路径。

        Returns
        -------
        Topology
            返回 self。
        """
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        self.network.load_state(data.get("control", {}))
        self._state_sig = self._control_sig()

        self._path_cache.clear()
        for key, node_id_list in data.get("paths", {}).items():
            src_id, dst_id, kind_s = key.split("|")
            s, d = self.node_idx.get(src_id), self.node_idx.get(dst_id)
            if s is None or d is None:
                continue
            try:
                idxs = np.array([self.node_idx[n] for n in node_id_list], dtype=np.int32)
            except KeyError:
                continue
            self._path_cache[(s, d, int(kind_s))] = idxs

        self._cached_version = self._ctrl_version
        logger.info("网络缓存导入 <- %s (%d 条路径)", path, len(self._path_cache))
        return self

    # ------------------------------------------------------------------ 移动
    @staticmethod
    def base_speed(kind):
        """按实体类型返回默认巡航速度（m/s）。"""
        return _VEH_SPEED if kind == 1 else _PED_SPEED

    @staticmethod
    def wait_ticks(node_type, rng):
        """按节点类型采样出发前等待时长（tick，1 tick = 1 s）。"""
        return int(rng.uniform(WAIT_MIN[node_type], WAIT_MAX[node_type]) * 60)

    @staticmethod
    def dwell_ticks(node_type, rng):
        """按节点类型采样停留时长（tick）。"""
        return int(rng.uniform(DWELL_MIN[node_type], DWELL_MAX[node_type]) * 60)


# ============================================================================
# 自测：加载 YAML → Dijkstra（人车分流）→ 状态管理 → 缓存持久化
# ============================================================================

if __name__ == "__main__":
    import tempfile

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    print("=" * 60)
    print("topology.py（DjShortCut + Topology 整合版）自测")
    print("=" * 60)

    topo = Topology()
    network = topo.network
    planner = topo.planner

    print(f"\n节点数: {topo.n_nodes} | 信号节点: {len(topo.signal_nodes)} | "
          f"大门闸: {len(topo.gate_nodes)}")

    # ---- 一、基础最短路径 ----
    print("\n--- 一、基础最短路径 ---")
    for src, dst in (("gate_south", "canteen_1"), ("gate_west", "gate_east")):
        path = planner.predict(src, dst)
        print(f"  {src} → {dst}: {' → '.join(path)}")

    # ---- 二、人车分流（门只管人） ----
    print("\n--- 二、门人车分流（library=closed） ---")
    i_s, i_d, i_lib = topo.node("gate_south"), topo.node("canteen_1"), topo.node("library")
    topo.sync_control_states(door_states={"library": "closed"})
    ped = topo.path_nodes(i_s, i_d, kind=0)
    veh = topo.path_nodes(i_s, i_d, kind=1)
    print(f"  行人: {' → '.join(ped)}")
    print(f"  车辆: {' → '.join(veh)}")
    assert "library" not in ped[1:-1], "行人应绕开 closed 门"
    assert "library" in veh, "车辆应穿门不受影响"
    print("  验证通过: 车辆忽略门，行人绕行")
    topo.sync_control_states(door_states={"library": "open"})

    # ---- 三、状态缓存持久化 ----
    print("\n--- 三、控制状态缓存持久化 ---")
    with tempfile.TemporaryDirectory() as td:
        state_path = Path(td) / "state.json"
        network.set_door_states({"library": "closed", "canteen_1": "restricted"})
        network.set_signal_states({"cross_zh_mid": {"phase": "red"}})
        network.export_state(state_path)
        print("  导出 ->", state_path)
        network.set_door_states({"library": "open"})
        network.import_state(state_path)
        print("  导入后 door_states:", network.get_door_states())
        assert network.get_door_states()["library"] == "closed"

        # ---- 四、网络缓存（控制 + 路径）持久化 ----
        print("\n--- 四、网络缓存持久化（控制状态 + 路径） ---")
        cache_path = Path(td) / "cache.json"
        topo.export_cache(cache_path)
        n_before = topo.n_cached_paths()
        print(f"  导出 {n_before} 条路径 ->", cache_path)
        topo2 = Topology()
        topo2.import_cache(cache_path)
        print(f"  导入后路径缓存: {topo2.n_cached_paths()} 条 | "
              f"door_states: {topo2.get_door_states()}")
        assert topo2.n_cached_paths() == n_before
        assert topo2.get_door_states()["library"] == "closed"

    print("\n" + "=" * 60)
    print("全部测试通过")
    print("=" * 60)
