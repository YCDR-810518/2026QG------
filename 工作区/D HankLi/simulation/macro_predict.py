# -*- coding: utf-8 -*-
"""macro_predict —— CSV 批次驱动的宏观热度/热点预测模块（成员 A）

读取 F 累计落盘的 engine_timeseries.csv 中最新一批（相同 timestamp，61 节点），
以实时密度修正边权（可叠加门/信号状态），驱动 TrafficNetworkAnalyzer /
AttractRankAnalyzer 重新计算节点热度与热点区域，通过两个独立 predict 端口
分别返回 pandas DataFrame：

- DensityBatchProvider : CSV 最新批次读取器（tail 读取，只读，专门类）
- MacroPredictor       : 整合入口，predict_network / predict_hotspots 两个端口

数据流：
    F CsvRecorder 每 10s 追加一批 → DensityBatchProvider.read_batch()
    → 最新批 density/door_status/signal_status → MacroPredictor
    → TrafficNetworkAnalyzer.fit(density=..., door_states=..., signal_states=...)
      边权 w' = w × (1 + congestion_factor × d̄)，d̄ 为边两端节点密度均值
    → predict_network()  节点级热度 DataFrame（node_id/pagerank/betweenness/heatScore/rank）
    → predict_hotspots() 区域级热点 DataFrame（region/nodeIds/attractScore）

依赖：pandas、numpy、config、topology（Topology）、macro_topo、macro_attractrank
用法：
    from macro_predict import MacroPredictor
    pred = MacroPredictor()                 # csv_path 缺省读 config paths.csv_file
    df_net = pred.predict_network()         # 61 行 × 5 列
    df_hot = pred.predict_hotspots()        # N 行 × 3 列
"""
import io
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from config import get_config
from macro_attractrank import AttractRankAnalyzer
from macro_topo import TrafficNetworkAnalyzer
from topology import Topology

__all__ = ["DensityBatchProvider", "MacroPredictor"]

# CSV 表头（口径与 engine.SNAPSHOT_CSV_FIELDS 完全一致）
SNAPSHOT_CSV_FIELDS = [
    "tick", "timestamp", "node_id", "people", "vehicles",
    "density", "level",
    "gate_status", "gate_flow_rate",
    "door_status", "door_flow_rate",
    "signal_status", "signal_flow_rate",
]

# tail 读取窗口（字节）：单批约 7 KB，128 KB ≈ 18 批；不足时翻倍重试
_TAIL_WINDOW = 128 * 1024
_TAIL_WINDOW_MAX = 512 * 1024


def _default_csv_path():
    """默认 CSV 路径：读 config.yaml paths.csv_file。"""
    cfg = get_config()
    return Path(cfg["paths"]["csv_file"])


# ============================================================================
# DensityBatchProvider —— CSV 最新批次读取器（只读）
# ============================================================================


class DensityBatchProvider:
    """CSV 最新批次读取器（tail 读取，只读，不整读历史）。

    Parameters
    ----------
    csv_path : str or Path, optional
        固定 CSV 路径；缺省读 config.yaml paths.csv_file。
        构造时校验文件存在，不存在抛 FileNotFoundError。

    Attributes
    ----------
    csv_path : Path
        解析后的 CSV 绝对路径。
    """

    def __init__(self, csv_path=None):
        path = Path(csv_path) if csv_path is not None else _default_csv_path()
        self.csv_path = path.resolve()
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV 不存在: {self.csv_path}")

    # ------------------------------------------------------------------ 读取
    def read_batch(self, timestamp=None, csv_path=None):
        """读取最新一批（或指定批次）节点状态。

        Parameters
        ----------
        timestamp : str, optional
            'YYYY-MM-DD HH:MM:SS'。None → 最新一批；指定 → 该批次（测试/回放）。
        csv_path : str or Path, optional
            非 None 时覆盖本次调用的 CSV 路径
            （优先级：参数 > 构造参数 > config）。

        Returns
        -------
        pandas.DataFrame
            该批次全部行（61 节点 × 13 列，列名 = SNAPSHOT_CSV_FIELDS）。
            dtype：tick/people/vehicles 为 int64，density 为 float64，
            其余列为 str（空值保持 ""）。文件仅表头 / 无匹配批次时返回
            空 DataFrame（保留 13 列表头）。

        Raises
        ------
        FileNotFoundError
            csv_path 覆盖的路径不存在。
        """
        path = Path(csv_path) if csv_path is not None else self.csv_path
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"CSV 不存在: {path}")

        df = self._parse(self._tail_text(path, _TAIL_WINDOW))
        if df.empty and _TAIL_WINDOW < _TAIL_WINDOW_MAX:
            df = self._parse(self._tail_text(path, _TAIL_WINDOW_MAX))
        if df.empty:
            return pd.DataFrame(columns=SNAPSHOT_CSV_FIELDS)

        if timestamp is not None:
            out = df[df["timestamp"] == timestamp]
        else:
            out = df[df["timestamp"] == df["timestamp"].iloc[-1]]
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _tail_text(path, window):
        """二进制回读文件末尾 window 字节，切到最后一个完整行。"""
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            if size == 0:
                return ""
            f.seek(max(0, size - window))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if not text.endswith("\n"):
            nl = text.rfind("\n")
            text = text[: nl + 1] if nl != -1 else ""
        return text

    @classmethod
    def _parse(cls, text):
        """解析 chunk 文本 → 规范 dtype 的 DataFrame（剔除表头/空行/半行）。

        半行兜底：chunk 首行可能是不完整的旧行，靠 timestamp 批次过滤剔除。
        """
        cols = SNAPSHOT_CSV_FIELDS
        if not text or not text.strip():
            return pd.DataFrame(columns=cols)
        try:
            df = pd.read_csv(io.StringIO(text), header=None, names=cols,
                             dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=cols)

        df = df[df["node_id"].ne("") & df["node_id"].ne("node_id")]
        if df.empty:
            return pd.DataFrame(columns=cols)

        df["tick"] = pd.to_numeric(df["tick"], errors="coerce").fillna(0).astype(np.int64)
        df["people"] = pd.to_numeric(df["people"], errors="coerce").fillna(0).astype(np.int64)
        df["vehicles"] = pd.to_numeric(df["vehicles"], errors="coerce").fillna(0).astype(np.int64)
        df["density"] = pd.to_numeric(df["density"], errors="coerce").fillna(0.0).astype(np.float64)
        return df


# ============================================================================
# MacroPredictor —— 整合入口（两个独立 predict 端口）
# ============================================================================


class MacroPredictor:
    """CSV 最新批次 → 密度修正边权 → 宏观热度/热点区域预测。

    Parameters
    ----------
    csv_path : str or Path, optional
        固定 CSV 路径；缺省读 config paths.csv_file。
    topology : Topology, optional
        拓扑实例；缺省惰性构建 Topology()（读 config topology.file）。
    congestion_factor : float, default=1.0
        拥堵修正系数 k：边权 w' = w × (1 + k × d̄)，d̄ 为边两端节点密度均值。
    alpha : float, default=0.5
        传给 TrafficNetworkAnalyzer 的 heatScore 中 PageRank 权重。

    Attributes
    ----------
    csv_path / congestion_factor / alpha : 构造参数。
    """

    # 端口输出列（对齐冻结接口 1-交通网络接口.md hotness/hotspots 字段）
    NETWORK_COLUMNS = ["node_id", "pagerank", "betweenness", "heatScore", "rank"]
    HOTSPOTS_COLUMNS = ["region", "nodeIds", "attractScore"]

    def __init__(self, csv_path=None, topology=None, congestion_factor=1.0,
                 alpha=0.5):
        self.csv_path = csv_path
        self.topology = topology
        self.congestion_factor = float(congestion_factor)
        self.alpha = float(alpha)
        self._provider = None
        self._topo = None

    # ------------------------------------------------------------------ 端口一
    def predict_network(self, timestamp=None, csv_path=None):
        """节点级宏观热度（TrafficNetworkAnalyzer 批次结果）。

        Returns
        -------
        pandas.DataFrame
            61 行 × 5 列：node_id, pagerank, betweenness, heatScore, rank。
            pagerank/betweenness/heatScore 为 float64，rank 为 int64
            （1 = 最热）。无数据时返回空 DataFrame（保留列）。
        """
        batch = self._provider_().read_batch(timestamp=timestamp, csv_path=csv_path)
        cols = self.NETWORK_COLUMNS
        if batch.empty:
            return pd.DataFrame(columns=cols)

        analyzer = TrafficNetworkAnalyzer(
            alpha=self.alpha, congestion_factor=self.congestion_factor,
        ).fit(
            self._topo_().network,
            door_states=self._door_states(batch),
            signal_states=self._signal_states(batch),
            density=batch.set_index("node_id")["density"],
        )
        result = analyzer.transform()  # {node_id: {pagerank, betweenness, heatScore, rank}}

        df = pd.DataFrame(
            [{"node_id": nid, **result[nid]} for nid in batch["node_id"]],
            columns=cols,
        )
        df["rank"] = df["rank"].astype(np.int64)
        return df

    # ------------------------------------------------------------------ 端口二
    def predict_hotspots(self, timestamp=None, csv_path=None):
        """区域级热点分析（AttractRankAnalyzer 批次结果）。

        Returns
        -------
        pandas.DataFrame
            N 行 × 3 列：region, nodeIds(list[str]), attractScore(float)。
            无数据时返回空 DataFrame（保留列）。
        """
        batch = self._provider_().read_batch(timestamp=timestamp, csv_path=csv_path)
        cols = self.HOTSPOTS_COLUMNS
        if batch.empty:
            return pd.DataFrame(columns=cols)

        attract = AttractRankAnalyzer(alpha=self.alpha).fit(
            self._topo_().network,
            door_states=self._door_states(batch),
            signal_states=self._signal_states(batch),
            density=batch.set_index("node_id")["density"],
        )
        return pd.DataFrame(attract.transform(), columns=cols)

    # ------------------------------------------------------------------ 内部
    def _provider_(self):
        if self._provider is None:
            self._provider = DensityBatchProvider(csv_path=self.csv_path)
        return self._provider

    def _topo_(self):
        if self._topo is None:
            self._topo = self.topology if self.topology is not None else Topology()
        return self._topo

    @staticmethod
    def _door_states(batch):
        """CSV 批次 door_status → {node_id: open/restricted/closed}（空串跳过）。"""
        return {r.node_id: r.door_status for r in batch.itertuples()
                if r.door_status != ""}

    @staticmethod
    def _signal_states(batch):
        """CSV 批次 signal_status → {node_id: {"phase": ...}}（空串跳过）。"""
        return {r.node_id: {"phase": r.signal_status} for r in batch.itertuples()
                if r.signal_status != ""}


# ============================================================================
# 自测：临时 CSV 3 批 → 读取器 + 两个端口 + 密度修正生效验证
# ============================================================================

if __name__ == "__main__":
    import csv
    import tempfile

    TS = ["2026-08-04 12:00:00", "2026-08-04 12:00:10", "2026-08-04 12:00:20"]

    def _write_header(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write(",".join(SNAPSHOT_CSV_FIELDS) + "\n")

    def _write_batch(path, tick, ts, node_ids, densities,
                     door_overrides=None, signal_overrides=None):
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for i, nid in enumerate(node_ids):
                d = float(densities[i])
                level = "low" if d < 0.3 else ("medium" if d < 0.6 else "high")
                door = (door_overrides or {}).get(nid, "open")
                sig = (signal_overrides or {}).get(nid, "")
                w.writerow([tick, ts, nid, 5, 0, f"{d:.4f}", level,
                            "", "", door, "", sig, ""])

    topo = Topology()
    node_ids = topo.node_ids
    print("=" * 60)
    print("macro_predict 自测（节点数: %d）" % topo.n_nodes)
    print("=" * 60)

    tmp = Path(tempfile.mkdtemp()) / "engine_timeseries.csv"
    _write_header(tmp)
    _write_batch(tmp, 0, TS[0], node_ids, [0.1] * len(node_ids))
    _write_batch(tmp, 10, TS[1], node_ids,
                 [0.05 + 0.5 * (i / len(node_ids)) for i in range(len(node_ids))])
    signal_nodes = set(topo.network.get_signaled_nodes())
    _write_batch(tmp, 20, TS[2], node_ids, [0.9] * len(node_ids),
                 door_overrides={"library": "closed"},
                 signal_overrides={n: "red" for n in signal_nodes})

    provider = DensityBatchProvider(csv_path=tmp)

    # ---- 一、读取器 ----
    print("\n--- 一、DensityBatchProvider ---")
    latest = provider.read_batch()
    assert len(latest) == topo.n_nodes, f"最新批行数不符: {len(latest)}"
    assert latest["tick"].unique().tolist() == [20], latest["tick"].unique()
    assert latest["timestamp"].nunique() == 1 and latest["timestamp"].iloc[0] == TS[2]
    assert list(latest.columns) == SNAPSHOT_CSV_FIELDS
    assert latest["tick"].dtype == np.int64 and latest["density"].dtype == np.float64
    assert pd.api.types.is_string_dtype(latest["node_id"]), latest["node_id"].dtype
    print(f"  最新批: {len(latest)} 行, tick={latest['tick'].iloc[0]}, "
          f"ts={latest['timestamp'].iloc[0]}")
    print(f"  dtypes: {dict(latest.dtypes.astype(str))}")

    batch1 = provider.read_batch(timestamp=TS[1])
    assert len(batch1) == topo.n_nodes and batch1["tick"].iloc[0] == 10
    print(f"  指定批次 {TS[1]}: {len(batch1)} 行 OK")

    empty_ts = provider.read_batch(timestamp="1999-01-01 00:00:00")
    assert empty_ts.empty and len(empty_ts.columns) == len(SNAPSHOT_CSV_FIELDS)
    print("  不存在的 timestamp → 空 DataFrame(保留 13 列) OK")

    # 半行兜底：追加无换行的残缺行，最新批仍完整
    with open(tmp, "a", encoding="utf-8") as f:
        f.write("broken-partial-line-without-newline")
    latest2 = provider.read_batch()
    assert len(latest2) == topo.n_nodes and latest2["tick"].iloc[0] == 20
    print("  尾部残缺行 → 仍取到完整最新批 OK")

    # 仅表头文件
    tmp2 = Path(tempfile.mkdtemp()) / "empty.csv"
    _write_header(tmp2)
    empty_df = DensityBatchProvider(csv_path=tmp2).read_batch()
    assert empty_df.empty and len(empty_df.columns) == len(SNAPSHOT_CSV_FIELDS)
    print("  仅表头文件 → 空 DataFrame(保留 13 列) OK")

    # ---- 二、predict_network ----
    print("\n--- 二、MacroPredictor.predict_network ---")
    pred = MacroPredictor(csv_path=tmp, congestion_factor=1.0)
    df_net = pred.predict_network()
    assert len(df_net) == topo.n_nodes
    assert list(df_net.columns) == MacroPredictor.NETWORK_COLUMNS
    assert df_net["rank"].dtype == np.int64
    assert sorted(df_net["rank"].tolist()) == list(range(1, topo.n_nodes + 1))
    top3 = df_net.nlargest(3, "heatScore")["node_id"].tolist()
    print(f"  行数={len(df_net)} 列={list(df_net.columns)}")
    print(f"  heatScore Top3: {top3}")

    # ---- 三、密度修正生效验证 ----
    # 注：均匀密度只等比缩放边权，PageRank/中介中心性对等比缩放不变，
    # 因此验证采用"非均匀密度分布"（一半节点 1.0，另一半 0.0）。
    print("\n--- 三、密度修正生效验证 ---")
    tmp_low = Path(tempfile.mkdtemp()) / "low.csv"
    tmp_high = Path(tempfile.mkdtemp()) / "high.csv"
    half = len(node_ids) // 2
    dens_high = [1.0 if i < half else 0.0 for i in range(len(node_ids))]
    for p, ds in ((tmp_low, [0.0] * len(node_ids)), (tmp_high, dens_high)):
        _write_header(p)
        _write_batch(p, 0, TS[0], node_ids, ds)
    pred_low = MacroPredictor(csv_path=tmp_low)
    pred_high = MacroPredictor(csv_path=tmp_high)
    s_low = pred_low.predict_network()["heatScore"].sum()
    s_high = pred_high.predict_network()["heatScore"].sum()
    assert s_high != s_low, "密度修正未生效: 非均匀密度热度总和应不同"
    rank_top_low = pred_low.predict_network().nlargest(3, "heatScore")["node_id"].tolist()
    rank_top_high = pred_high.predict_network().nlargest(3, "heatScore")["node_id"].tolist()
    print(f"  全 0.0 密度: heatScore 总和={s_low:.2f} Top3={rank_top_low}")
    print(f"  半 1.0 密度: heatScore 总和={s_high:.2f} Top3={rank_top_high}")
    print(f"  热度排行发生变化: {rank_top_low != rank_top_high} → 生效 OK")

    # ---- 四、predict_hotspots ----
    print("\n--- 四、MacroPredictor.predict_hotspots ---")
    df_hot = pred.predict_hotspots()
    assert list(df_hot.columns) == MacroPredictor.HOTSPOTS_COLUMNS
    assert len(df_hot) > 0, "真实拓扑应聚类出区域"
    print(f"  区域数={len(df_hot)} 列={list(df_hot.columns)}")
    for r in df_hot.head(3).itertuples():
        print(f"    {r.region:15s} nodes={len(r.nodeIds):2d} score={r.attractScore:.1f}")

    print("\n" + "=" * 60)
    print("ALL PASS")
    print("=" * 60)
