# -*- coding: utf-8 -*-
"""macro_attractrank —— AttractRank 热点区域分析模块（成员 A）

对外接口：sklearn 风格
- AttractRankAnalyzer : 空间聚类 + 区域吸引度评分

对应需求：FR-09（AttractRank 热点区域分析）
依赖：numpy、DjShortCut（TrafficNetwork）、macro_topo（TrafficNetworkAnalyzer）
用法：
    from topology import TrafficNetwork
    from macro_attractrank import AttractRankAnalyzer

    network = TrafficNetwork.from_yaml("graph_data.yaml")
    analyzer = AttractRankAnalyzer().fit(network)
    regions = analyzer.transform()
"""
import logging
import sys
from pathlib import Path

import numpy as np

# 网络核心已整合到 simulation/topology.py（原 DjShortCut.py 已删除）
sys.path.insert(0, str(Path(__file__).resolve().parent / "simulation"))
from topology import TrafficNetwork  # noqa: E402
from macro_topo import TrafficNetworkAnalyzer  # noqa: E402

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
# AttractRankAnalyzer —— sklearn 风格 热点区域分析
# ============================================================================


class AttractRankAnalyzer:
    """AttractRank 热点区域分析器。

    基于空间聚类（Union-Find）将离散节点聚合为有意义的区域，
    并计算每个区域的综合吸引度得分。

    Parameters
    ----------
    alpha : float, default=0.5
        传给内部 TrafficNetworkAnalyzer 的 heatScore 中 PageRank 权重。

    Attributes
    ----------
    regions_ : list of dict
        [{"region": str, "nodeIds": list[str], "attractScore": float}, ...]
        fit 后生成的学习属性。
    n_regions_ : int
        区域总数。
    graph_ : TrafficNetwork
        加载的拓扑实例。
    analyzer_ : TrafficNetworkAnalyzer
        内部使用的分析器实例。
    distance_threshold_ : float
        从 YAML 读取的空间聚类距离阈值。
    min_nodes_ : int
        从 YAML 读取的最少节点数。
    """

    def __init__(self, alpha=0.5):
        self.alpha = float(alpha)
        self.regions_ = None
        self.n_regions_ = None
        self.graph_ = None
        self.analyzer_ = None
        self.distance_threshold_ = None
        self.min_nodes_ = None

    # ------------------------------------------------------------------
    # fit
    # ------------------------------------------------------------------

    def fit(self, graph, analyzer=None, door_states=None, signal_states=None,
            density=None):
        """加载拓扑并执行空间聚类 + 区域评分。

        Parameters
        ----------
        graph : TrafficNetwork
            TrafficNetwork 拓扑实例。
        analyzer : TrafficNetworkAnalyzer, optional
            已 fit 的分析器，不传则内部自动构建。传入外部 analyzer 时
            door_states / signal_states / density 均不生效（由调用方负责）。
        door_states : dict, optional
            门状态覆盖（透传给内部 TrafficNetworkAnalyzer）。
        signal_states : dict, optional
            红绿灯状态覆盖（透传给内部 TrafficNetworkAnalyzer）。
        density : dict or pandas.Series, optional
            各节点实时密度 {node_id: float}，透传给内部
            TrafficNetworkAnalyzer 做边权拥堵修正（None 表示不修正）。

        Returns
        -------
        AttractRankAnalyzer
            返回 self。
        """
        if not isinstance(graph, TrafficNetwork):
            raise TypeError(
                f"graph 必须是 TrafficNetwork 实例，收到: {type(graph).__name__}"
            )

        self.graph_ = graph

        self._load_config(graph)

        if analyzer is not None:
            self.analyzer_ = analyzer
        else:
            logger.info("内部构建 TrafficNetworkAnalyzer (alpha=%.1f)", self.alpha)
            self.analyzer_ = TrafficNetworkAnalyzer(alpha=self.alpha).fit(
                graph, door_states=door_states, signal_states=signal_states,
                density=density,
            )

        self._cluster()

        self._score()

        logger.info(
            "聚类完成: %d 个区域, 覆盖 %d 个节点",
            self.n_regions_, sum(len(r["nodeIds"]) for r in self.regions_),
        )
        return self

    # ------------------------------------------------------------------
    # 读取 YAML 配置
    # ------------------------------------------------------------------

    def _load_config(self, graph):
        yaml_path = getattr(graph, '_yaml_path', None)
        if yaml_path is None:
            # 没有记录原始 YAML 路径，用默认值
            self.distance_threshold_ = 15.0
            self.min_nodes_ = 2
            return

        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        cfg = raw.get("attractrank", {})
        self.distance_threshold_ = float(cfg.get("distance_threshold", 15.0))
        self.min_nodes_ = int(cfg.get("min_nodes", 2))
        logger.info(
            "从 YAML 读取配置: distance_threshold=%.1f min_nodes=%d",
            self.distance_threshold_, self.min_nodes_,
        )

    # ------------------------------------------------------------------
    # Union-Find 聚类
    # ------------------------------------------------------------------

    def _cluster(self):
        graph = self.graph_
        threshold = self.distance_threshold_
        node_ids = list(graph.nodes.keys())
        n = len(node_ids)
        idx_of = {nid: i for i, nid in enumerate(node_ids)}

        coords = np.array(
            [[graph.nodes[nid]["x"], graph.nodes[nid]["y"]] for nid in node_ids],
            dtype=np.float64,
        )

        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # 1) 直接边连接 + 距离 < threshold
        for edge in graph.edges:
            src, dst = edge["nodes"][0], edge["nodes"][1]
            if src not in idx_of or dst not in idx_of:
                continue
            i, j = idx_of[src], idx_of[dst]
            dist = float(np.linalg.norm(coords[i] - coords[j]))
            if dist <= threshold:
                union(i, j)

        # 2) 同类型且空间距离 < threshold / 2
        for i in range(n):
            ni_type = graph.nodes[node_ids[i]]["type"]
            for j in range(i + 1, n):
                if graph.nodes[node_ids[j]]["type"] != ni_type:
                    continue
                dist = float(np.linalg.norm(coords[i] - coords[j]))
                if dist <= threshold / 2.0:
                    union(i, j)

        # 收集簇
        clusters = {}
        for i in range(n):
            root = find(i)
            clusters.setdefault(root, []).append(i)

        # 过滤孤立点
        raw_regions = []
        for root, members in clusters.items():
            if len(members) < self.min_nodes_:
                continue
            member_ids = [node_ids[i] for i in members]
            raw_regions.append(member_ids)

        # 按规模排序
        raw_regions.sort(key=lambda x: -len(x))

        self._raw_regions = raw_regions

    # ------------------------------------------------------------------
    # 吸引度评分 + 命名
    # ------------------------------------------------------------------

    def _score(self):
        analyzer = self.analyzer_
        graph = self.graph_

        all_scores = []
        for member_ids in self._raw_regions:
            # 区域名 = 类型众数 + 序号
            type_counts = {}
            for nid in member_ids:
                t = graph.nodes[nid]["type"]
                type_counts[t] = type_counts.get(t, 0) + 1
            dominant_type = max(type_counts, key=type_counts.get)

            total_heat = sum(analyzer.heat_scores_.get(nid, 0.0) for nid in member_ids)
            all_scores.append({
                "member_ids": member_ids,
                "dominant_type": dominant_type,
                "total_heat": total_heat,
            })

        max_heat = max(item["total_heat"] for item in all_scores) if all_scores else 1.0

        type_counters = {}
        self.regions_ = []
        for item in all_scores:
            dt = item["dominant_type"]
            type_counters[dt] = type_counters.get(dt, 0) + 1
            region_name = f"{dt}_{type_counters[dt]}"

            self.regions_.append({
                "region": region_name,
                "nodeIds": item["member_ids"],
                "attractScore": round(
                    item["total_heat"] / max(max_heat, 1e-12) * 100.0, 1
                ),
            })

        self.n_regions_ = len(self.regions_)

    # ------------------------------------------------------------------
    # transform
    # ------------------------------------------------------------------

    def transform(self):
        """返回全部热点区域。

        Returns
        -------
        list of dict
            [{"region": str, "nodeIds": list[str], "attractScore": float}, ...]

        Raises
        ------
        RuntimeError
            尚未调用 fit()。
        """
        if self.regions_ is None:
            raise RuntimeError("请先调用 fit(graph)")
        return self.regions_

    # ------------------------------------------------------------------
    # sklearn 参数管理
    # ------------------------------------------------------------------

    def get_params(self, deep=True):
        return {"alpha": self.alpha}

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
    print("AttractRankAnalyzer 自测")
    print("=" * 60)

    # ── 1. 静态分析 ──
    print("\n--- 一、静态热点区域分析 ---")
    analyzer = AttractRankAnalyzer(alpha=0.5).fit(network)

    print(f"\n聚类结果: {analyzer.n_regions_} 个区域")
    for r in analyzer.transform():
        print(f"  {r['region']:15s}  nodes={len(r['nodeIds']):2d}  "
              f"score={r['attractScore']:5.1f}  members={r['nodeIds'][:3]}")

    # ── 2. 传入已有 TrafficNetworkAnalyzer ──
    print("\n--- 二、传入已有 TrafficNetworkAnalyzer ---")
    from macro_topo import TrafficNetworkAnalyzer
    pre_analyzer = TrafficNetworkAnalyzer(alpha=0.5).fit(network)
    analyzer2 = AttractRankAnalyzer(alpha=0.5).fit(network, analyzer=pre_analyzer)
    print(f"复用分析器完成: {analyzer2.n_regions_} 个区域")

    # ── 3. 动态分析 ──
    print("\n--- 三、动态分析（封西门 + 中环红灯） ---")
    analyzer3 = AttractRankAnalyzer(alpha=0.5).fit(
        network,
        door_states={"gate_west": "closed"},
        signal_states={"cross_zh_mid": {"phase": "red"}},
    )
    print(f"\n动态聚类: {analyzer3.n_regions_} 个区域")
    for r in analyzer3.transform():
        print(f"  {r['region']:15s}  nodes={len(r['nodeIds']):2d}  "
              f"score={r['attractScore']:5.1f}")

    # ── 4. get_params ──
    print(f"\n--- 四、参数 ---")
    print(f"  get_params(): {analyzer.get_params()}")
    print(f"  distance_threshold (from YAML): {analyzer.distance_threshold_}")
    print(f"  min_nodes (from YAML): {analyzer.min_nodes_}")

    print("\n" + "=" * 60)
    print("自测完成")
    print("=" * 60)
