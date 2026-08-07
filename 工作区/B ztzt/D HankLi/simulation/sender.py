# -*- coding: utf-8 -*-
"""sender.py —— 本地 JSON 持久化发送器（成员 F）

引擎每 10s 生成的快照、以及后续其他模块的 JSON 数据，统一经本模块打包
写入本地目录，供打包发送到后端。后端接口未定，此处只负责"落盘 + 窗口管理"：

- JsonSender 管理一个"打包窗口"目录（data/json/），每个数据类别一个文件，
  同名覆盖：每批数据打包发送成功后调 clear() 清空窗口，下一批（10s）再
  写入时即覆盖上一批，本地不累积历史。
- 编码器兼容 numpy 标量/数组、datetime、Path 等，保证快照可直接 json.dumps。
- 引擎接入采用 sender 驱动循环（run_and_send），不修改 engine.py。
- 引擎快照按"每 tick 每节点一行"的扁平行数组落盘，字段与 CSV 表头一致：
  tick/timestamp/node_id/people/vehicles/density/level/gate_status/
  gate_flow_rate/door_status/door_flow_rate/signal_status/signal_flow_rate。

用法：
    sender = JsonSender(data_dir="./data")
    with sender:
        run_and_send(engine, n_ticks=3600, sender=sender, interval=10,
                     on_package=lambda files: print("package:", files))

    # 其他数据类别同样进同一窗口打包：
    sender.emit("weather", {"tick": 100, "level": "rain"})
"""
import datetime
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from config import get_config


class NumpyJSONEncoder(json.JSONEncoder):
    """把 numpy / datetime / Path 等转换为原生 JSON 类型。"""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, (np.str_,)):
            return str(obj)
        return super().default(obj)


def _default_data_dir():
    """默认落盘根目录：读 config.yaml 的 paths.data_dir，缺省 ./data。"""
    try:
        cfg = get_config()
        return cfg["paths"]["data_dir"]
    except Exception:
        return Path("./data")


def _replace_retry(src, dst, encoding="utf-8", attempts=4, base_delay=0.05):
    """Windows 容错原子替换：重试 + 降级直写。

    os.replace 在 Windows 上会因目标文件被其它进程以"禁止删除"方式占用
    （前端文件监视、编辑器预览、杀软扫描等）而抛 PermissionError [WinError 5]。
    本函数先带指数退避重试 replace；仍失败则先删除目标再替换；最后降级为
    直写（只需目标可写，不要求删除权限）。
    """
    import time

    last_err = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(base_delay * (2 ** i))
    try:
        try:
            os.unlink(dst)
        except OSError:
            pass
        os.replace(src, dst)
        return
    except PermissionError as e:
        last_err = e
    try:
        with open(dst, "w", encoding=encoding) as f:
            with open(src, "r", encoding=encoding) as g:
                f.write(g.read())
            f.flush()
            os.fsync(f.fileno())
        os.unlink(src)
        return
    except OSError as e:
        raise last_err if last_err is not None else e
    finally:
        if os.path.exists(src):
            try:
                os.unlink(src)
            except OSError:
                pass


class JsonSender:
    """本地 JSON 打包窗口管理器。

    Parameters
    ----------
    data_dir : str or Path
        数据根目录；窗口目录为 <data_dir>/<window_dir>。
    window_dir : str
        打包窗口子目录名（缺省 "json"）。
    encoding : str
        落盘编码。

    Attributes
    ----------
    window_dir : Path
        打包窗口目录（当前批次所有类别 JSON 所在处）。
    """

    def __init__(self, data_dir=None, window_dir="json", encoding="utf-8"):
        base = Path(data_dir) if data_dir is not None else _default_data_dir()
        self.data_dir = base.resolve()
        self.window_dir = self.data_dir / window_dir
        self.encoding = encoding
        self.window_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 落盘
    def emit(self, category, payload, indent=None):
        """把一类数据原子写入窗口目录 data/json/<category>.json（同名覆盖）。

        Parameters
        ----------
        category : str
            数据类别（作为文件名，如 "engine_snapshot"）。
        payload : dict
            JSON 可序列化对象（含 numpy 类型亦可）。
        indent : int, optional
            json.dumps 缩进；缺省 None（紧凑）。

        Returns
        -------
        Path
            写出的文件路径。
        """
        if not isinstance(category, str) or not category:
            raise ValueError("category 必须为非空字符串")
        if "/" in category or "\\" in category or category in (".", ".."):
            raise ValueError(f"category 含非法字符: {category!r}")

        text = json.dumps(payload, cls=NumpyJSONEncoder, ensure_ascii=False,
                          indent=indent)
        out = self.window_dir / f"{category}.json"
        fd, tmp = tempfile.mkstemp(dir=str(self.window_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=self.encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            _replace_retry(tmp, out, encoding=self.encoding)
        except BaseException:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise
        return out

    # ------------------------------------------------------------------ 窗口
    def package_files(self):
        """当前窗口全部文件（供打包发送到后端）。"""
        return sorted(p for p in self.window_dir.glob("*.json"))

    def clear(self):
        """清空当前窗口文件（在每批打包发送成功后调用，供下一批覆盖）。"""
        n = 0
        for p in self.package_files():
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def flush(self):
        """空操作保留位：与上层 IO 约定一致，未来如需缓冲可在此落盘。"""
        return self

    # ------------------------------------------------------------------ 上下文
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


SNAPSHOT_JSON_FIELDS = [
    "tick", "timestamp", "node_id", "people", "vehicles",
    "density", "level",
    "gate_status", "gate_flow_rate",
    "door_status", "door_flow_rate",
    "signal_status", "signal_flow_rate",
]


def engine_snapshot_to_json(snap):
    """把引擎 _snapshot() 返回值转纯 JSON 扁平行数组。

    每个节点输出一行，字段与 CSV 表头完全一致：tick / timestamp / node_id /
    people / vehicles / density / level / gate_status / gate_flow_rate /
    door_status / door_flow_rate / signal_status / signal_flow_rate。
    编码器会兜底处理 numpy 标量。
    """
    if snap is None:
        return None
    tick = snap.get("tick")
    timestamp = snap.get("timestamp")
    nodes = snap.get("nodes", [])
    gates = snap.get("gates", {})       # 键 = 节点序号
    doors = snap.get("doors", {})       # 键 = node_id
    signals = snap.get("signals", {})   # 键 = 节点序号
    rows = []
    for i, nd in enumerate(nodes):
        g = gates.get(i)
        sig = signals.get(i)
        rows.append({
            "tick": tick,
            "timestamp": timestamp,
            "node_id": nd.get("node_id"),
            "people": nd.get("people"),
            "vehicles": nd.get("vehicles"),
            "density": nd.get("density"),
            "level": nd.get("level"),
            "gate_status": g.get("mode", "") if g else "",
            "gate_flow_rate": g.get("throughput_cap", "") if g else "",
            "door_status": doors.get(nd.get("node_id"), ""),
            "door_flow_rate": "",
            "signal_status": sig.get("phase", "") if sig else "",
            "signal_flow_rate": sig.get("signal_flow_rate", "") if sig else "",
        })
    return rows


def _sleep_until(deadline):
    """睡到绝对时刻 deadline（time.perf_counter 基准），避免累积漂移。"""
    import time

    remain = deadline - time.perf_counter()
    if remain > 0:
        time.sleep(remain)


def run_and_send(engine, n_ticks, sender=None, interval=10, on_package=None,
                 tick_hz=None, timestamp_fn=None):
    """sender 驱动循环：逐 tick 推进引擎，每 interval 个 tick 落盘一批快照。

    Parameters
    ----------
    engine : TickEngine
        已构建的引擎实例。
    n_ticks : int
        运行的 tick 总数（每 tick = 1 秒）。
    sender : JsonSender, optional
        打包窗口；缺省按 config 自动构建。
    interval : int
        落盘间隔（tick）。缺省 10 即每 10s 一批。
    on_package : callable, optional
        每批落盘后回调（如打包发送到后端）；后端接口未定时可传 None。
        签名：on_package(package_files: list[Path]) -> bool
        返回真值表示发送成功，随后清空窗口供下一批覆盖；返回假/None 则
        保留文件（可稍后重试）。
    tick_hz : float, optional
        实时节拍频率（tick/秒）。>0 时每个 tick 对齐到 1/tick_hz 现实秒
        （1 tick = 1 现实秒即 tick_hz=1）；缺省/0/None 表示全速跑不节拍。
    timestamp_fn : callable, optional
        输出时间戳覆写函数，签名 timestamp_fn(tick) -> str。提供时在每次
        落盘前用其结果覆写 snap["timestamp"]（如 clock=now 用 datetime.now()，
        clock=base 用 start_datetime + tick 秒）；缺省保留引擎仿真时间戳。

    Returns
    -------
    JsonSender
        使用的发送器实例。
    """
    import time

    sender = sender if sender is not None else JsonSender()
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
                if timestamp_fn is not None and snap is not None:
                    snap = dict(snap)
                    snap["timestamp"] = timestamp_fn(t)
                sender.emit("engine_snapshot", engine_snapshot_to_json(snap), indent=2)
                if on_package is not None:
                    ok = on_package(sender.package_files())
                    if ok:
                        sender.clear()
    return sender


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

    sender = JsonSender(data_dir=cfg["paths"]["data_dir"])

    def _show_package(files):
        for p in files:
            body = p.read_text(encoding="utf-8")
            print(f"[package] {p.name}  ({len(body)} bytes)")
            print(body[:300])
        return True

    print(f"== sender selftest: run 25 ticks, emit every 10 ==")
    run_and_send(eng, n_ticks=25, sender=sender, interval=10,
                 on_package=_show_package)
    remaining = sender.package_files()
    print(f"== after successful sends, window files (should be 0): "
          f"{len(remaining)} ==")

    print(f"== emit one more snapshot, verify overwrite + clear ==")
    snap = eng.step(30)
    sender.emit("engine_snapshot", engine_snapshot_to_json(snap))
    files = sender.package_files()
    assert len(files) == 1, f"expected 1 file, got {len(files)}"
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert isinstance(body, list), f"expected list, got {type(body)}"
    assert len(body) == topo.n_nodes, f"expected {topo.n_nodes} rows, got {len(body)}"
    assert body[0]["tick"] == 30, f"expected tick=30, got {body[0]['tick']}"
    assert set(body[0].keys()) == set(SNAPSHOT_JSON_FIELDS), (
        f"列名不符: {sorted(body[0].keys())}")
    print(f"  overwrote -> {files[0].name} tick={body[0]['tick']} "
          f"rows={len(body)} columns={sorted(body[0].keys())}")
    cleared = sender.clear()
    print(f"  clear() removed {cleared} file(s), now "
          f"{len(sender.package_files())} left")
    sys.exit(0)
