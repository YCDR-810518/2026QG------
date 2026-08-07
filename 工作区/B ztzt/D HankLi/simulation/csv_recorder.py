# -*- coding: utf-8 -*-
"""csv_recorder.py —— 固定路径累计 CSV 记录器（成员 F）

simulation 每 10s 快照除 JSON（sender.py）外，还追加写入一个固定路径的
CSV 文件：每 tick 全部节点各一行（tick/timestamp 相同），文件持续累积，
供其他模块（E 大屏 / D 校验 / B 落库等）消费。与 sender.py 的
run_and_send 同构，采用外部驱动循环，不侵入 engine.py 主循环。

- CsvRecorder    ：管理固定 CSV（表头补齐 / truncate / append_tick）。
- run_and_record ：仅 CSV 的驱动循环（每 interval 追加一批）。
- run_and_record_send ：CSV + JSON 同步落盘的驱动循环（同一轮 run 同时
  产出两种产物，兼容 main.py json --csv-path）。

用法：
    recorder = CsvRecorder("./data/engine_timeseries.csv")
    run_and_record(engine, n_ticks=3600, recorder=recorder, interval=10)

    run_and_record_send(engine, n_ticks, recorder=CsvRecorder(),
                        sender=JsonSender(), interval=10)
"""
import os
from pathlib import Path

from config import get_config
from engine import SNAPSHOT_CSV_FIELDS
from sender import _sleep_until


def _default_csv_path():
    """默认固定 CSV 路径：读 config.yaml 的 paths.csv_file，缺省 ./data/engine_timeseries.csv。"""
    try:
        cfg = get_config()
        return cfg["paths"]["csv_file"]
    except Exception:
        return Path("./data/engine_timeseries.csv")


class CsvRecorder:
    """固定路径累计 CSV 记录器。

    Parameters
    ----------
    path : str or Path, optional
        固定 CSV 文件路径（追加累积，不覆盖）；缺省读 config paths.csv_file。
    encoding : str
        落盘编码。

    Attributes
    ----------
    path : Path
        CSV 文件绝对路径。
    """

    def __init__(self, path=None, encoding="utf-8"):
        self.path = Path(path) if path is not None else _default_csv_path()
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.encoding = encoding
        self.ensure_header()

    # ------------------------------------------------------------------ 表头
    def ensure_header(self):
        """文件不存在或为空时补写表头，保证后续追加可读。

        Returns
        -------
        bool
            True 表示本次补写了表头。
        """
        if self.path.exists() and self.path.stat().st_size > 0:
            return False
        with open(self.path, "w", newline="", encoding=self.encoding) as f:
            f.write(",".join(SNAPSHOT_CSV_FIELDS) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True

    def truncate(self):
        """清空文件并重写表头（run 开始时调用，避免跨运行 tick 重复）。"""
        with open(self.path, "w", newline="", encoding=self.encoding) as f:
            f.write(",".join(SNAPSHOT_CSV_FIELDS) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return self

    # ------------------------------------------------------------------ 追加
    def append_tick(self, engine, tick, timestamp_fn=None):
        """把某 tick 的全部节点状态追加写入 CSV（每节点一行）。

        Parameters
        ----------
        engine : TickEngine
            已推进到 tick 的引擎实例（复用 engine._snapshot_rows(t)）。
        tick : int
            要落盘的 tick。
        timestamp_fn : callable, optional
            时间戳覆写函数 timestamp_fn(tick) -> str；提供时把本批每行的
            timestamp 列覆写为其结果（如墙钟/自定义基准时间）。

        Returns
        -------
        int
            本次追加的行数（节点数）。
        """
        import csv

        rows = engine._snapshot_rows(int(tick))
        if not rows:
            return 0
        if timestamp_fn is not None:
            ts = timestamp_fn(int(tick))
            for row in rows:
                row[1] = ts
        with open(self.path, "a", newline="", encoding=self.encoding) as f:
            w = csv.writer(f)
            for row in rows:
                w.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        return len(rows)


def run_and_record(engine, n_ticks, recorder=None, interval=10, truncate=False,
                   on_append=None, tick_hz=None, timestamp_fn=None):
    """驱动循环：逐 tick 推进引擎，每 interval 个 tick 追加一批 CSV 行。

    Parameters
    ----------
    engine : TickEngine
        已构建的引擎实例。
    n_ticks : int
        运行的 tick 总数（每 tick = 1 秒）。
    recorder : CsvRecorder, optional
        记录器；缺省按 config 自动构建。
    interval : int
        追加间隔（tick）。缺省 10 即每 10s 一次。
    truncate : bool
        run 开始前是否清空文件重建表头（避免跨运行 tick 重复）。
    on_append : callable, optional
        每批追加后的回调，签名 on_append(path: Path, tick: int, rows: int)。
    tick_hz : float, optional
        实时节拍频率（tick/秒）。>0 时每个 tick 对齐到 1/tick_hz 现实秒；
        缺省/0/None 表示全速跑不节拍。
    timestamp_fn : callable, optional
        时间戳覆写函数 timestamp_fn(tick) -> str；提供时覆写每批 timestamp 列。

    Returns
    -------
    CsvRecorder
        使用的记录器实例。
    """
    import time

    recorder = recorder if recorder is not None else CsvRecorder()
    if truncate:
        recorder.truncate()
    n_ticks = int(n_ticks)
    interval = max(1, int(interval))
    tick_hz = float(tick_hz) if tick_hz else 0.0
    period = 1.0 / tick_hz if tick_hz > 0 else None
    start = time.perf_counter() if period else None
    for t in range(n_ticks):
        engine.step(t)
        if period is not None:
            _sleep_until(start + (t + 1) * period)
        if t % interval == 0:
            n = recorder.append_tick(engine, t, timestamp_fn=timestamp_fn)
            if on_append is not None:
                on_append(recorder.path, t, n)
    return recorder


def run_and_record_send(engine, n_ticks, recorder=None, sender=None, interval=10,
                        truncate=False, on_package=None, tick_hz=None,
                        timestamp_fn=None):
    """驱动循环：每 interval 个 tick 同时追加 CSV 并落盘 JSON 快照。

    Parameters 同 run_and_record 与 sender.run_and_send；on_package 语义
    与 run_and_send 一致（返回真值后清空 JSON 窗口，供下一批覆盖）。
    tick_hz / timestamp_fn 同 run_and_record（实时节拍 + 时间戳覆写）。

    Returns
    -------
    CsvRecorder
        使用的记录器实例。
    """
    import time

    from sender import JsonSender, engine_snapshot_to_json

    recorder = recorder if recorder is not None else CsvRecorder()
    sender = sender if sender is not None else JsonSender()
    if truncate:
        recorder.truncate()
    n_ticks = int(n_ticks)
    interval = max(1, int(interval))
    tick_hz = float(tick_hz) if tick_hz else 0.0
    period = 1.0 / tick_hz if tick_hz > 0 else None
    start = time.perf_counter() if period else None
    with sender:
        for t in range(n_ticks):
            snap = engine.step(t)
            if period is not None:
                _sleep_until(start + (t + 1) * period)
            if t % interval == 0:
                recorder.append_tick(engine, t, timestamp_fn=timestamp_fn)
                if timestamp_fn is not None and snap is not None:
                    snap = dict(snap)
                    snap["timestamp"] = timestamp_fn(t)
                sender.emit("engine_snapshot", engine_snapshot_to_json(snap),
                            indent=2)
                if on_package is not None:
                    ok = on_package(sender.package_files())
                    if ok:
                        sender.clear()
    return recorder


if __name__ == "__main__":
    import sys

    from controller import HysteresisPolicyController
    from flow_data_generator import FlowDataGenerator
    from joint_regulator import JointRegulator
    from topology import Topology

    cfg = get_config()
    topo = Topology(cfg["topology"]["file"])
    gen = FlowDataGenerator(n_people=200, n_vehicles=20, random_state=42, n_days=1)
    gen.generate()
    eng = __import__("engine", fromlist=["TickEngine"]).TickEngine(
        topo, gen,
        gate_policy=HysteresisPolicyController(role="gate"),
        door_policy=HysteresisPolicyController(role="door"),
        joint_regulator=JointRegulator(),
    )

    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp()) / "engine_timeseries.csv"
    recorder = CsvRecorder(path=tmp)
    print(f"== csv_recorder selftest: run 25 ticks, append every 10 ==")
    run_and_record(eng, n_ticks=25, recorder=recorder, interval=10)
    lines = recorder.path.read_text(encoding="utf-8").strip().splitlines()
    header = lines[0].split(",")
    assert header == SNAPSHOT_CSV_FIELDS, f"表头不符: {header}"
    body = [l.split(",") for l in lines[1:]]
    ticks = sorted({row[0] for row in body})
    assert ticks == ["0", "10", "20"], f"期望采样 tick 0/10/20，实际 {ticks}"
    assert len(body) == 3 * topo.n_nodes, (
        f"期望每 tick {topo.n_nodes} 行，实际 {len(body)}")
    assert len({row[1] for row in body}) == 3, "每 tick 时间戳应一致"
    print(f"  ticks={ticks} rows={len(body)} (3 ticks × {topo.n_nodes} nodes)")
    print(f"  sample row: {body[0]}")

    print(f"== append again (10 more ticks -> 30) 验证累积 ==")
    run_and_record(eng, n_ticks=10, recorder=recorder, interval=10)
    n_lines = len(recorder.path.read_text(encoding="utf-8").strip().splitlines())
    assert n_lines == 1 + 4 * topo.n_nodes, f"累积行数不符: {n_lines}"
    print(f"  cumulative rows={n_lines - 1} (ticks 0/10/20/30) OK")

    print(f"== truncate() 清空重建表头 ==")
    recorder.truncate()
    after = recorder.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(after) == 1 and after[0] == ",".join(SNAPSHOT_CSV_FIELDS)
    print(f"  truncate -> header only ({len(after)} line) OK")
    print("ALL PASS")
    sys.exit(0)
