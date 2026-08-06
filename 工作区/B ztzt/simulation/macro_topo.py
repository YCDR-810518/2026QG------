# -*- coding: utf-8 -*-
"""macro_topo —— 交通网络拓扑分析模块（成员 A）

对外接口：sklearn 风格
- TrafficNetworkAnalyzer : PageRank + 中介中心性

对应需求：FR-08（PageRank / 中介中心性）
依赖：numpy、pyyaml、DjShortCut（TrafficNetwork）
用法：
    from topology import TrafficNetwork
    from macro_topo import TrafficNetworkAnalyzer

    network = TrafficNetwork.from_yaml("graph_data.yaml")
    analyzer = TrafficNetworkAnalyzer().fit(network)
    print(analyzer.pagerank_)
    print(analyzer.transform())          # 全量
    print(analyzer.transform("library")) # 单节点
"""
import heapq
import logging
import sys
from pathlib import Path

import numpy as np

# 网络核心已整合到 simulation/topology.py（原 DjShortCut.py 已删除）
sys.path.insert(0, str(Path(__file__).resolve().parent / "simulation"))
from topology import TrafficNetwork  # noqa: E402

logger = logging.getLogger(__name__)


def _configure_logger():
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
# TrafficNetworkAnalyzer —— sklearn 风格 PageRank + 中介中心性
# ============================================================================


class TrafficNetworkAnalyzer:
    """交通网络拓扑分析器（PageRank + 中介中心性）。

    支持动态门/红绿灯状态影响边权后重新计算节点重要性排名。

    Parameters
    ----------
    damping_factor : float, default=0.85
        PageRank 阻尼系数。
    max_iter : int, default=100
        PageRank 最大迭代次数。
    tol : float, default=1e-6
        PageRank 收敛阈值。
    alpha : float, default=0.5
        heatScore 中 PageRank 的权重占比（0~1）。
        heatScore = (alpha × PR_norm + (1-alpha) × BC_norm) × 100。
    congestion_factor : float, default=1.0
        实时密度拥堵修正系数 k（见 fit 的 density 参数）：
        边权 w' = w × (1 + k × d̄)，d̄ 为边两端节点密度的平均值。

    Attributes
    ----------
    pagerank_ : dict {node_id: float}
        fit 后所有节点的 PageRank 得分（归一化 0~1）。
    betweenness_ : dict {node_id: float}
        fit 后所有节点的中介中心性（原始值）。
    heat_scores_ : dict {node_id: float}
        fit 后所有节点的综合热度分（0~100）。
    ranks_ : dict {node_id: int}
        fit 后所有节点的热度排名（1 = 最热）。
    n_nodes_ : int
        节点总数。
    node_ids_ : list of str
        所有节点 ID 列表。
    graph_ : TrafficNetwork
        加载的拓扑实例。
    """

    _SIGNAL_RED_WEIGHT = 1000.0
    _SIGNAL_YELLOW_WEIGHT = 3.0
    _DOOR_RESTRICTED_FACTOR = 10.0

    def __init__(self, damping_factor=0.85, max_iter=100, tol=1e-6, alpha=0.5,
                 congestion_factor=1.0):
        self.damping_factor = float(damping_factor)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.alpha = float(alpha)
        self.congestion_factor = float(congestion_factor)

        self.pagerank_ = None
        self.betweenness_ = None
        self.heat_scores_ = None
        self.ranks_ = None
        self.n_nodes_ = None
        self.node_ids_ = None
        self.graph_ = None

    # ------------------------------------------------------------------
    # fit —— 加载图 + 计算 PageRank + 计算中介中心性
    # ------------------------------------------------------------------

    def fit(self, graph, door_states=None, signal_states=None, density=None):
        """加载交通网络拓扑并计算节点重要性指标。

        Parameters
        ----------
        graph : TrafficNetwork
            TrafficNetwork 拓扑实例。
        door_states : dict, optional
            门状态 {node_id: "open" | "closed" | "restricted"}。
            影响边权：closed→断开，restricted→×10。
        signal_states : dict, optional
            红绿灯状态 {node_id: {"phase": "green" | "yellow" | "red" | "off", ...}}。
            影响边权：red/off→×1000，yellow→×3。
        density : dict or pandas.Series, optional
            各节点实时密度 {node_id: float}（如引擎 people_density 或 CSV 批次
            density 列）。提供时在门/信号修正之后叠加拥堵修正：
            边权 w' = w × (1 + congestion_factor × d̄)，
            d̄ = (density[src] + density[dst]) / 2，缺失节点按 0 计。
            None 表示不做密度修正（保持纯静态/门信号分析）。

        Returns
        -------
        TrafficNetworkAnalyzer
            返回 self，支持链式调用。
        """
        if not isinstance(graph, TrafficNetwork):
            raise TypeError(
                f"graph 必须是 TrafficNetwork 实例，收到: {type(graph).__name__}"
            )

        logger.info(
            "fit: 加载拓扑 (%d 节点)，门=%s 信号=%s 密度=%s",
            len(graph.nodes),
            len(door_states or {}),
            len(signal_states or {}),
            "有" if density is not None else "无",
        )

        self.graph_ = graph
        self.n_nodes_ = len(graph.nodes)
        self.node_ids_ = list(graph.nodes.keys())

        graph._build_adjacency()

        weights = self._build_weight_matrix(door_states, signal_states, density)

        self.pagerank_ = self._compute_pagerank(weights)
        logger.info("PageRank 完成: top1=%s %.4f",
                     max(self.pagerank_, key=self.pagerank_.get),
                     max(self.pagerank_.values()))

        self.betweenness_ = self._compute_betweenness(weights)
        logger.info("中介中心性完成: top1=%s %.1f",
                     max(self.betweenness_, key=self.betweenness_.get),
                     max(self.betweenness_.values()))

        self._compute_heat_scores()
        logger.info("热度排行完成: top3=%s",
                     sorted(self.ranks_, key=self.ranks_.get)[:3])

        return self

    # ------------------------------------------------------------------
    # 边权矩阵构建（含门/红绿灯动态修正）
    # ------------------------------------------------------------------

    def _build_weight_matrix(self, door_states=None, signal_states=None,
                             density=None):
        """构建有效边权矩阵（无向加权，含动态状态修正）。

        Parameters
        ----------
        door_states : dict or None
        signal_states : dict or None
        density : dict or pandas.Series or None
            各节点实时密度，None 表示不做密度修正。

        Returns
        -------
        np.ndarray (n, n)
            对称的邻接权值矩阵。
        """
        graph = self.graph_
        n = self.n_nodes_
        id_to_idx = {nid: i for i, nid in enumerate(self.node_ids_)}
        weights = np.zeros((n, n), dtype=np.float64)

        density_lookup = None
        if density is not None:
            if hasattr(density, "to_dict"):
                density = density.to_dict()
            density_lookup = {str(k): float(v) for k, v in dict(density).items()}

        for edge in graph.edges:
            src, dst = edge["nodes"][0], edge["nodes"][1]
            if src not in id_to_idx or dst not in id_to_idx:
                continue
            i, j = id_to_idx[src], id_to_idx[dst]
            w = float(edge.get("weight", 1.0))

            src_door = graph._resolve_door_state(src, door_states)
            dst_door = graph._resolve_door_state(dst, door_states)

            if src_door == "closed" or dst_door == "closed":
                continue

            if src_door == "restricted":
                w *= self._DOOR_RESTRICTED_FACTOR
            if dst_door == "restricted":
                w *= self._DOOR_RESTRICTED_FACTOR

            src_phase = graph._resolve_signal_phase(src, signal_states)
            dst_phase = graph._resolve_signal_phase(dst, signal_states)
            for phase in (src_phase, dst_phase):
                if phase in ("red", "off"):
                    w *= self._SIGNAL_RED_WEIGHT
                elif phase == "yellow":
                    w *= self._SIGNAL_YELLOW_WEIGHT

            if density_lookup is not None:
                d_bar = (density_lookup.get(src, 0.0) + density_lookup.get(dst, 0.0)) / 2.0
                w *= 1.0 + self.congestion_factor * d_bar

            weights[i, j] = w
            weights[j, i] = w

        for i in range(n):
            if weights[i].sum() == 0.0:
                weights[i, i] = 1.0

        return weights

    # ------------------------------------------------------------------
    # PageRank（加权无向图）
    # ------------------------------------------------------------------

    def _compute_pagerank(self, weights):
        """加权无向图 PageRank。

        Parameters
        ----------
        weights : np.ndarray (n, n)

        Returns
        -------
        dict {node_id: float}
        """
        n = self.n_nodes_
        row_sums = weights.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0.0] = 1.0
        transition = weights / row_sums

        r = np.ones(n) / n
        teleport = np.ones(n) / n

        for iteration in range(self.max_iter):
            r_new = (1.0 - self.damping_factor) * teleport + self.damping_factor * (transition.T @ r)
            delta = np.abs(r_new - r).max()
            if delta < self.tol:
                logger.debug("PageRank 收敛: iter=%d delta=%.2e", iteration + 1, delta)
                r = r_new
                break
            r = r_new

        return {nid: float(r[idx]) for idx, nid in enumerate(self.node_ids_)}

    # ------------------------------------------------------------------
    # Brandes 中介中心性（加权图）
    # ------------------------------------------------------------------

    def _compute_betweenness(self, weights):
        """Brandes 算法计算加权图中介中心性。

        Parameters
        ----------
        weights : np.ndarray (n, n)

        Returns
        -------
        dict {node_id: float}
        """
        n = self.n_nodes_
        betweenness = np.zeros(n, dtype=np.float64)

        for s in range(n):
            dist = np.full(n, np.inf)
            sigma = np.zeros(n, dtype=np.float64)
            dist[s] = 0.0
            sigma[s] = 1.0
            delta = np.zeros(n, dtype=np.float64)
            predecessors = [[] for _ in range(n)]

            pq = [(0.0, s)]
            visited = np.zeros(n, dtype=bool)
            stack = []

            while pq:
                d, v = heapq.heappop(pq)
                if visited[v]:
                    continue
                visited[v] = True
                stack.append(v)

                for w in range(n):
                    wgt = weights[v, w]
                    if wgt <= 0.0 or w == v:
                        continue

                    new_d = d + wgt
                    if abs(new_d - dist[w]) < 1e-12:
                        sigma[w] += sigma[v]
                        predecessors[w].append(v)
                    elif new_d < dist[w] - 1e-12:
                        dist[w] = new_d
                        sigma[w] = sigma[v]
                        predecessors[w] = [v]
                        heapq.heappush(pq, (new_d, w))

            while stack:
                w = stack.pop()
                for v in predecessors[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != s:
                    betweenness[w] += delta[w]

        norm = (n - 1) * (n - 2) / 2.0
        if norm > 0.0:
            betweenness /= norm

        return {nid: float(betweenness[idx]) for idx, nid in enumerate(self.node_ids_)}

    # ------------------------------------------------------------------
    # 综合热度分 + 排名
    # ------------------------------------------------------------------

    def _compute_heat_scores(self):
        """按 alpha 加权合并 PageRank 与中介中心性，计算排名。"""
        pr = self.pagerank_
        bc = self.betweenness_

        pr_max = max(pr.values()) if pr else 1.0
        bc_max = max(bc.values()) if bc else 1.0

        pr_norm = {k: v / max(pr_max, 1e-12) for k, v in pr.items()}
        bc_norm = {k: v / max(bc_max, 1e-12) for k, v in bc.items()}

        self.heat_scores_ = {}
        for nid in self.node_ids_:
            self.heat_scores_[nid] = float(
                (self.alpha * pr_norm[nid] + (1.0 - self.alpha) * bc_norm[nid]) * 100.0
            )

        sorted_nodes = sorted(self.heat_scores_, key=self.heat_scores_.get, reverse=True)
        self.ranks_ = {nid: rank for rank, nid in enumerate(sorted_nodes, 1)}

    # ------------------------------------------------------------------
    # transform —— 查询结果
    # ------------------------------------------------------------------

    def transform(self, node=None):
        """查询节点重要性指标。

        Parameters
        ----------
        node : str, optional
            节点 ID。不传返回全部节点。

        Returns
        -------
        dict or dict of dict
            - node=None: {node_id: {pagerank, betweenness, heatScore, rank}, ...}
            - node="...": {nodeId, pagerank, betweenness, heatScore, rank}

        Raises
        ------
        RuntimeError
            尚未调用 fit()。
        ValueError
            指定节点不存在。
        """
        if self.pagerank_ is None or self.betweenness_ is None:
            raise RuntimeError("请先调用 fit(graph)")

        if node is not None:
            if node not in self.node_ids_:
                raise ValueError(f"节点不存在: {node}")
            return {
                "nodeId": node,
                "pagerank": round(self.pagerank_[node], 6),
                "betweenness": round(self.betweenness_[node], 4),
                "heatScore": round(self.heat_scores_[node], 1),
                "rank": self.ranks_[node],
            }

        result = {}
        for nid in self.node_ids_:
            result[nid] = {
                "pagerank": round(self.pagerank_[nid], 6),
                "betweenness": round(self.betweenness_[nid], 4),
                "heatScore": round(self.heat_scores_[nid], 1),
                "rank": self.ranks_[nid],
            }
        return result

    # ------------------------------------------------------------------
    # sklearn 参数管理接口
    # ------------------------------------------------------------------

    def get_params(self, deep=True):
        return {
            "damping_factor": self.damping_factor,
            "max_iter": self.max_iter,
            "tol": self.tol,
            "alpha": self.alpha,
            "congestion_factor": self.congestion_factor,
        }

    def set_params(self, **params):
        for k, v in params.items():
            if not hasattr(self, k):
                raise ValueError(f"Unknown param: {k}")
            setattr(self, k, v)
        return self


# ============================================================================
# 自测
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    from pathlib import Path

    from config import get_config

    yaml_path = get_config()["topology"]["file"]
    network = TrafficNetwork.from_yaml(yaml_path)

    print("=" * 60)
    print("TrafficNetworkAnalyzer 自测")
    print("=" * 60)

    # ── 1. 静态分析 ──
    print("\n--- 一、静态拓扑分析 ---")
    analyzer = TrafficNetworkAnalyzer(alpha=0.5).fit(network)

    top5 = sorted(analyzer.pagerank_.items(), key=lambda x: -x[1])[:5]
    print("\nPageRank Top 5:")
    for nid, score in top5:
        print(f"  {nid:25s}  {score:.6f}")

    top5_bc = sorted(analyzer.betweenness_.items(), key=lambda x: -x[1])[:5]
    print("\nBetweenness Top 5:")
    for nid, score in top5_bc:
        print(f"  {nid:25s}  {score:.4f}")

    top5_heat = sorted(analyzer.heat_scores_.items(), key=lambda x: -x[1])[:5]
    print("\nHeatScore Top 5:")
    for nid, score in top5_heat:
        print(f"  {nid:25s}  {score:.1f}")

    # ── 2. 单节点查询 ──
    print("\n--- 二、单节点查询 ---")
    result = analyzer.transform("library")
    print(f"  library: {result}")

    # ── 3. 全量查询（测前3个） ──
    print("\n--- 三、全量查询（前 3 个） ---")
    all_scores = analyzer.transform()
    for nid in list(all_scores.keys())[:3]:
        print(f"  {nid}: {all_scores[nid]}")

    # ── 4. 动态分析 ──
    print("\n--- 四、动态分析（西门封 + 中环红灯） ---")
    analyzer2 = TrafficNetworkAnalyzer(alpha=0.5).fit(
        network,
        door_states={"gate_west": "closed"},
        signal_states={"cross_zh_mid": {"phase": "red"}},
    )

    top5_dyn = sorted(analyzer2.heat_scores_.items(), key=lambda x: -x[1])[:5]
    print("\n动态 HeatScore Top 5:")
    for nid, score in top5_dyn:
        print(f"  {nid:25s}  {score:.1f}")

    # 对比：静态 top5 在动态中的排名
    print("\n静态 Top5 在动态分析中的排名变化:")
    for nid, _ in top5_heat:
        print(f"  {nid:25s}  静态 rank={analyzer.ranks_[nid]} → 动态 rank={analyzer2.ranks_[nid]}")

    # ── 5. get_params ──
    print("\n--- 五、参数查询 ---")
    print(f"  get_params(): {analyzer.get_params()}")

    print("\n" + "=" * 60)
    print("自测完成")
    print("=" * 60)
