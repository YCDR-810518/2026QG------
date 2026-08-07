# -*- coding: utf-8 -*-
"""main.py —— 园区安全智能调控平台统一入口（成员 F）

通过包式导入统一编排 simulation 各模块，替代散落在各模块的入口：

    python simulation/main.py [子命令] [选项]

子命令：
    run      完整仿真流水线（默认）：拓扑 → 生成器 → 信号 → 引擎（门/闸/联合调控）
             → 驱动循环每 interval tick 追加固定 CSV + 吐 JSON + 预测预警（读 test 的
             SecurityService，--no-predict 关闭）→ 末 tick 快照 + 性能报告；
             支持网络缓存导入/导出。
    stress   压测 1000→8000（run_level）。
    verify   正确性校验（无实体丢失 + 密度相关性）。
    data     生成人流/车辆数据并落盘。
    json     每 10s 快照落盘 JSON（sender 驱动循环，同名覆盖打包窗口）。
    csv      每 10s 快照追加固定 CSV（累计落盘，供其他模块消费）。
    selftest 依次执行轻量模块自测。
    paths    打印解析后的配置路径（调试）。
    cache    网络缓存（控制状态 / 路径缓存）导出|导入。

用法示例：
    python simulation/main.py
    python simulation/main.py run --n-people 1000 --n-vehicles 80 --n-ticks 200
    python simulation/main.py run --export-cache ./data/cache.json --import-cache ./data/cache.json
    python simulation/main.py stress
    python simulation/main.py cache export --state ./data/state.json
"""
import argparse
import datetime
import json
import runpy
import sys
from pathlib import Path

# 包式导入：把 项目目录 与 simulation 目录注入 sys.path
_PKG_DIR = Path(__file__).resolve().parent          # simulation/
_ROOT = _PKG_DIR.parent                             # 项目目录/
for _p in (_ROOT, _PKG_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from simulation import (  # noqa: E402
    FlowDataGenerator,
    HysteresisPolicyController,
    JointRegulator,
    MacroPredictor,
    Topology,
)
from simulation import (  # noqa: E402
    get_config,
    run_level,
    run_integration,
    verify_baseline,
)
from flow_data_generator import WEEKDAY_NAMES  # noqa: E402

LIGHT_SELFTESTS = [
    "config",
    "entities",
    "movement",
    "controller",
    "joint_regulator",
    "topology",
]

_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _tick_hz(cfg, args):
    """解析实时节拍频率：--max-speed/--hz 0 → 0（全速）；缺省取 config tick_hz。"""
    if getattr(args, "max_speed", False):
        return 0.0
    if getattr(args, "hz", None) is not None:
        return float(args.hz)
    return float(cfg["simulation"].get("tick_hz", 1.0))


def _timestamp_fn(cfg, args, tick_hz):
    """构造输出时间戳覆写函数 timestamp_fn(tick) -> str。

    clock=now : 产出时刻 datetime.now()；
    clock=base: start_datetime + tick 秒（数据从指定基准时间开始，随实时节拍推进）。
    """
    clock = getattr(args, "clock", None) or str(cfg["simulation"].get("clock", "now"))
    if clock == "now":
        return lambda t: datetime.datetime.now().strftime(_TS_FMT)
    base = getattr(args, "start_datetime", None) or cfg["simulation"].get("start_datetime")
    if base:
        base_dt = datetime.datetime.fromisoformat(str(base))
    else:
        base_dt = datetime.datetime.combine(
            datetime.date.fromisoformat(str(cfg["simulation"].get("start_date", "2026-08-03"))),
            datetime.time(int(cfg["simulation"].get("start_hour", 6)),
                          int(cfg["simulation"].get("start_minute", 0))))
    return lambda t: (base_dt + datetime.timedelta(seconds=int(t))).strftime(_TS_FMT)


def _parse_start(cfg):
    """从 config 解析模拟起始时刻，返回 (start_hour, start_minute, start_date, day_profiles)。

    以 simulation.start_datetime 为唯一时间源；缺省回退 start_date + start_hour。
    day_profiles 按起始日期的星期几轮转，保证周几与真实日历对齐（周一开局则
    ("mon",...,"sun")，周二开局则 ("tue",...,"sun","mon")）。
    """
    sim = cfg["simulation"]
    base = sim.get("start_datetime")
    if base:
        base_dt = datetime.datetime.fromisoformat(str(base))
        start_hour, start_minute, start_date = base_dt.hour, base_dt.minute, base_dt.date()
    else:
        start_date = datetime.date.fromisoformat(str(sim.get("start_date", "2026-08-03")))
        start_hour = int(sim.get("start_hour", 6))
        start_minute = int(sim.get("start_minute", 0))
    weekday_idx = start_date.isoweekday() - 1          # mon=0 ... sun=6
    day_profiles = WEEKDAY_NAMES[weekday_idx:] + WEEKDAY_NAMES[:weekday_idx]
    return start_hour, start_minute, start_date, day_profiles


def _window_seconds(cfg):
    """每天仿真窗口时长（秒）= end_time - start_datetime 的当日时刻差。

    优先用 simulation.start_datetime 的时刻 + simulation.end_time（缺省 22:00:00）
    计算；未配置 start_datetime 时回退 start_date + start_hour。
    窗口 ≤ 0（起点不在结束前）抛 ValueError。未配 end_time 时返回 None（调用方兜底）。
    """
    sim = cfg["simulation"]
    end = sim.get("end_time")
    if not end:
        return None
    end_dt = datetime.datetime.strptime(str(end), "%H:%M:%S")
    base = sim.get("start_datetime")
    if base:
        start_dt = datetime.datetime.fromisoformat(str(base))
    else:
        start_date = datetime.date.fromisoformat(str(sim.get("start_date", "2026-08-03")))
        start_dt = datetime.datetime.combine(
            start_date,
            datetime.time(int(sim.get("start_hour", 6)), int(sim.get("start_minute", 0))))
    start_sec = start_dt.hour * 3600 + start_dt.minute * 60 + start_dt.second
    end_sec = end_dt.hour * 3600 + end_dt.minute * 60 + end_dt.second
    duration = end_sec - start_sec
    if duration <= 0:
        raise ValueError(
            f"end_time({end}) 必须在 start_datetime 当日时刻({start_dt:%H:%M:%S})之后")
    return duration


def _day_ticks(args, main_cfg, cfg):
    """每天 tick 数，优先级：--n-ticks > end_time 窗口 > main.n_ticks（兜底）。"""
    if getattr(args, "n_ticks", None) is not None:
        return int(args.n_ticks)
    win = _window_seconds(cfg)
    if win is not None:
        return int(win)
    return int(main_cfg.get("n_ticks", 3600))


# ---------------------------------------------------------------------------
# run —— 完整仿真流水线
# ---------------------------------------------------------------------------

def _print_alert_result(result):
    """打印一次 预测+预警 结果（对齐 test/service.py run_loop 输出风格）。"""
    if result.get("skipped"):
        print(f"  [预测跳过] {result.get('reason', '')}")
        return
    pred = result["predictions"]
    period = pred["period"]
    stats = pred["density_stats"]
    top3 = sorted(stats.items(), key=lambda kv: -kv[1])[:3]
    print(f"  [预测] {period['start']} ~ {period['end']} | "
          f"Top3: {', '.join(f'{k}={v}' for k, v in top3)} | "
          f"预警 {len(result['alerts'])} 条 | 已提交后端 {len(result['posted'])} 条")
    for a in result["alerts"]:
        print(f"    [{a['level']}] {a['type']} @ {a['node_id']} "
              f"密度={a.get('current_density')} 建议={a.get('suggested_action')}")


def _build_engine(cfg, n_people, n_vehicles, import_state=None, import_cache=None,
                  n_days=1, n_ticks_per_day=None):
    """构建 拓扑 + 生成器 + 引擎；可选导入网络缓存。"""
    from simulation import TickEngine

    start_hour, start_minute, start_date, day_profiles = _parse_start(cfg)
    n_base = int(n_ticks_per_day) if n_ticks_per_day else int(
        cfg.get("main", {}).get("n_ticks", 57600))
    n_hours = n_base / 3600.0

    topo = Topology(cfg["topology"]["file"])
    if import_state:
        topo.network.import_state(import_state)
    if import_cache:
        topo.import_cache(import_cache)

    gen = FlowDataGenerator(n_people=n_people, n_vehicles=n_vehicles,
                            random_state=cfg["simulation"]["seed"], n_days=n_days,
                            start_hour=start_hour, start_minute=start_minute,
                            start_date=start_date.isoformat(),
                            day_profiles=day_profiles, n_hours=n_hours,
                            density_level=cfg["simulation"].get("density_level", "peak"))
    gen.generate()

    # 按 config.yaml 加载移动模型（供 C 扩展 IDM/CAV）
    movement = None
    movement_cls_name = cfg["simulation"].get("movement_class", "ConstantSpeedMovement")
    movement_mode = cfg["simulation"].get("movement_mode", "idm")
    if movement_cls_name == "CavIdmMovement":
        from movement_cav import CavIdmMovement
        movement = CavIdmMovement(topo, mode=movement_mode)

    eng = TickEngine(
        topo, gen,
        movement=movement,
        gate_policy=HysteresisPolicyController(role="gate"),
        door_policy=HysteresisPolicyController(role="door"),
        joint_regulator=JointRegulator(),
        enable_signals=cfg["simulation"]["enable_signals"],
        seed=cfg["simulation"]["seed"],
        start_hour=start_hour, start_minute=start_minute, start_date=start_date.isoformat(),
    )
    return topo, gen, eng


def cmd_run(args, cfg):
    main_cfg = cfg.get("main", {})
    n_people = args.n_people if args.n_people is not None else int(main_cfg.get("n_people", 2000))
    n_vehicles = args.n_vehicles if args.n_vehicles is not None else int(main_cfg.get("n_vehicles", 150))
    n_days = args.n_days if args.n_days is not None else int(main_cfg.get("n_days", 1))
    n_base = _day_ticks(args, main_cfg, cfg)
    n_ticks = n_base * n_days

    print(f"== run: {n_people}人 / {n_vehicles}车 × {n_ticks} tick（{n_base}/天 × {n_days}天）==")
    if args.import_state:
        print(f"  导入控制状态: {args.import_state}")
    if args.import_cache:
        print(f"  导入网络缓存: {args.import_cache}")

    topo, gen, eng = _build_engine(
        cfg, n_people, n_vehicles,
        import_state=args.import_state, import_cache=args.import_cache,
        n_days=n_days, n_ticks_per_day=n_base,
    )

    from simulation import CsvRecorder, JsonSender, run_and_record_send

    tick_hz = _tick_hz(cfg, args)
    timestamp_fn = _timestamp_fn(cfg, args, tick_hz)
    interval = args.interval or 10
    print(f"  -> 每 {interval} tick 追加固定 CSV({cfg['paths']['csv_file']}) + 吐 JSON({cfg['paths']['data_dir']}/json)"
          + (f"，实时节拍 {tick_hz} tick/s" if tick_hz > 0 else "，全速")
          + f"，时间戳 clock={getattr(args, 'clock', None) or cfg['simulation'].get('clock', 'now')}")

    # 固定 CSV 绝对路径（与 CsvRecorder/MacroPredictor 同源，CWD 解析）
    csv_abs = str(Path(cfg["paths"]["csv_file"]).resolve())
    sender = JsonSender(data_dir=cfg["paths"]["data_dir"])
    # 宏观预测：始终开启，读 CSV 最新批次 → 节点热度 + 热点区域
    pred = MacroPredictor(csv_path=csv_abs, topology=topo)
    print(f"  宏观预测已启用（读 {csv_abs}）")

    # 预测+预警：默认启用，--no-predict 关闭（import test 里的 SecurityService）
    svc = None
    if not args.no_predict:
        _TEST_DIR = _ROOT / "test"
        if str(_TEST_DIR) not in sys.path:
            sys.path.insert(0, str(_TEST_DIR))
        from service import SecurityService

        svc = SecurityService.from_config(
            csv_path=csv_abs,                          # 与 CsvRecorder 同一文件（config paths.csv_file）
            backend_base="http://192.168.1.114:8100",  # 后端地址
            demo_mode=False,                           # 只发真实预警
            interval_seconds=60,                       # 轮询间隔
        )
        print(f"  预测+预警已启用（读 {csv_abs}，模型 {_TEST_DIR / 'checkpoints'}）")

    # 实时数据上传：--backend 覆盖配置，--no-upload 关闭（默认读 config realtime_backend）
    from backend_client import BackendClient, from_config

    client = None if args.no_upload else (from_config(cfg) or
                                          (BackendClient(base_url=args.backend)
                                           if args.backend else None))
    if client is not None:
        print(f"  实时数据上传已启用 -> {client._upload_url}")

    def _on_package(files):
        test_pred = None
        test_alerts = []
        if svc is not None:
            try:
                result = svc.check_alerts()
                _print_alert_result(result)
                test_pred = result.get("predictions")
                test_alerts = result.get("alerts", [])
            except Exception as e:
                print(f"  [预测异常跳过] {e}")
        try:
            df_net = pred.predict_network()
            df_hot = pred.predict_hotspots()
            rows = json.loads(
                (sender.window_dir / "engine_snapshot.json").read_text(encoding="utf-8"))
            union_pack = {
                "engine_snapshot": rows,
                "vehicle_paths": eng.vehicle_paths_json(),
                "predict_network": df_net.to_dict(orient="records"),
                "predict_hotspots": df_hot.to_dict(orient="records"),
                "prediction": test_pred,
                "alerts": test_alerts,
            }
            sender.emit("union_pack", union_pack, indent=2)
            (sender.window_dir / "engine_snapshot.json").unlink()
            print(f"  [宏观预测] 网络 {len(df_net)} 节点 | 热点 {len(df_hot)} 区域 "
                  f"| 预警 {len(test_alerts)} 条 -> union_pack.json")
            if client is not None:
                ok = client.send_payload(union_pack)
                print(f"  [实时上传] {'成功' if ok else '失败'} -> {client._upload_url}")
                return ok
        except Exception as e:
            print(f"  [宏观预测跳过] {e}")
        return False

    run_and_record_send(
        eng, n_ticks,
        recorder=CsvRecorder(path=cfg["paths"]["csv_file"]),
        sender=sender,
        interval=interval,
        on_package=_on_package,
        tick_hz=tick_hz,
        timestamp_fn=timestamp_fn,
    )
    report = eng.metrics.report()
    snap = eng._snapshot()

    print("\n-- 性能报告 --")
    print(f"  tick_mean_ms: {report['tick_mean_ms']:.3f}")
    print(f"  tick_p95_ms : {report['tick_p95_ms']:.3f}")
    print(f"  avg_active  : {report['avg_active']:.1f}")
    print(f"  module_mean_ms: {report.get('module_mean_ms')}")
    print("\n-- 末 tick 快照 --")
    print(f"  tick={snap.get('tick')} timestamp={snap.get('timestamp')}")
    gate_modes = {v.get("gate_id"): v.get("mode") for v in snap.get("gates", {}).values()}
    print(f"  大门状态: {gate_modes}")
    print(f"  门状态使用集合: {sorted(set(snap.get('doors', {}).values()))}")

    if args.export_state:
        topo.network.export_state(args.export_state)
        print(f"  控制状态导出 -> {args.export_state}")
    if args.export_cache:
        topo.export_cache(args.export_cache)
        print(f"  网络缓存导出 -> {args.export_cache}")
    if main_cfg.get("export_csv"):
        eng.export_snapshot_csv(main_cfg["export_csv"])
        print(f"  密度快照导出 -> {main_cfg['export_csv']}")
    return 0


# ---------------------------------------------------------------------------
# stress / verify
# ---------------------------------------------------------------------------

def cmd_stress(args, cfg):
    levels = args.levels or [int(x) for x in cfg.get("main", {}).get(
        "pressure_levels", [1000, 2000, 4000, 6000, 8000])]
    reports = []
    for lvl in levels:
        print(f"\n=== 压测 level={lvl} ===")
        reports.append(run_level(int(lvl), n_ticks=args.n_ticks, warmup=args.warmup))

    print(f"\n{'level':>7} {'tick_mean_ms':>12} {'p95_ms':>8} {'mem_mb':>9} "
          f"{'thr/s':>10} {'active':>9}  passed")
    for r in reports:
        row = r.as_table()
        print(f"{row['level']:>7} {row['tick_mean_ms']:>12} {row['tick_p95_ms']:>8} "
              f"{row['mem_mb']:>9} {row['throughput/s']:>10} {row['avg_active']:>9}  "
              f"{row['passed']}")
    return 0 if all(r.passed for r in reports) else 1


def cmd_verify(args, cfg):
    res = verify_baseline()
    print("verify_baseline:", res)
    return 0 if res.get("passed") else 1


# ---------------------------------------------------------------------------
# json —— 每 10s 快照落盘（sender 驱动循环）
# ---------------------------------------------------------------------------

def cmd_json(args, cfg):
    from simulation import JsonSender, run_and_send

    main_cfg = cfg.get("main", {})
    n_people = args.n_people or int(main_cfg.get("n_people", 2000))
    n_vehicles = args.n_vehicles or int(main_cfg.get("n_vehicles", 150))
    n_days = args.n_days if args.n_days is not None else int(main_cfg.get("n_days", 1))
    n_base = _day_ticks(args, main_cfg, cfg)
    n_ticks = n_base * n_days
    interval = args.interval or 10

    topo, gen, eng = _build_engine(cfg, n_people, n_vehicles, n_days=n_days,
                                   n_ticks_per_day=n_base)
    data_dir = str(Path(args.json_dir)) if args.json_dir else cfg["paths"]["data_dir"]
    tick_hz = _tick_hz(cfg, args)
    timestamp_fn = _timestamp_fn(cfg, args, tick_hz)

    if args.csv_path:
        from simulation import CsvRecorder, run_and_record_send

        csv_path = str(Path(args.csv_path))
        print(f"== json+csv: {n_people}人 / {n_vehicles}车 × {n_ticks} tick，"
              f"每 {interval}s 落盘 JSON + 追加 CSV ==")
        sender = JsonSender(data_dir=data_dir)
        recorder = run_and_record_send(
            eng, n_ticks,
            recorder=CsvRecorder(path=csv_path),
            sender=sender, interval=interval,
            on_package=lambda files: False,
            tick_hz=tick_hz, timestamp_fn=timestamp_fn,
        )
        print("JSON 窗口文件:", [str(p) for p in sender.package_files()])
        print(f"CSV 累计落盘 -> {recorder.path} "
              f"({recorder.path.stat().st_size} bytes)")
        return 0

    print(f"== json: {n_people}人 / {n_vehicles}车 × {n_ticks} tick，"
          f"每 {interval}s 落盘一批 ==")
    sender = JsonSender(data_dir=data_dir)
    run_and_send(eng, n_ticks, sender=sender, interval=interval,
                 tick_hz=tick_hz, timestamp_fn=timestamp_fn)
    files = sender.package_files()
    print("JSON 窗口文件:", [str(p) for p in files])
    return 0


def cmd_csv(args, cfg):
    from simulation import CsvRecorder, run_and_record

    main_cfg = cfg.get("main", {})
    n_people = args.n_people or int(main_cfg.get("n_people", 2000))
    n_vehicles = args.n_vehicles or int(main_cfg.get("n_vehicles", 150))
    n_days = args.n_days if args.n_days is not None else int(main_cfg.get("n_days", 1))
    n_base = _day_ticks(args, main_cfg, cfg)
    n_ticks = n_base * n_days
    interval = args.interval or 10

    print(f"== csv: {n_people}人 / {n_vehicles}车 × {n_ticks} tick，"
          f"每 {interval}s 追加一批 ==")
    if args.truncate:
        print("  truncate: run 开始前清空文件重建表头")

    topo, gen, eng = _build_engine(cfg, n_people, n_vehicles, n_days=n_days,
                                   n_ticks_per_day=n_base)
    csv_path = str(Path(args.csv_path)) if args.csv_path else cfg["paths"]["csv_file"]
    recorder = CsvRecorder(path=csv_path)
    run_and_record(eng, n_ticks, recorder=recorder, interval=interval,
                   truncate=args.truncate,
                   tick_hz=_tick_hz(cfg, args),
                   timestamp_fn=_timestamp_fn(cfg, args, _tick_hz(cfg, args)))
    print(f"CSV 累计落盘 -> {recorder.path} "
          f"({recorder.path.stat().st_size} bytes)")
    return 0


# ---------------------------------------------------------------------------
# data / selftest / paths
# ---------------------------------------------------------------------------

def cmd_data(args, cfg):
    main_cfg = cfg.get("main", {})
    n_people = args.n_people or int(main_cfg.get("n_people", 4000))
    n_vehicles = args.n_vehicles or int(main_cfg.get("n_vehicles", 300))
    data_dir = str(Path(cfg["paths"]["data_dir"]))
    start_hour, start_minute, start_date, day_profiles = _parse_start(cfg)
    n_base = _day_ticks(args, main_cfg, cfg)
    n_hours = n_base / 3600.0
    gen = FlowDataGenerator(n_people=n_people, n_vehicles=n_vehicles,
                            random_state=cfg["simulation"]["seed"],
                            n_days=args.n_days, data_dir=data_dir,
                            start_hour=start_hour, start_minute=start_minute,
                            start_date=start_date.isoformat(),
                            day_profiles=day_profiles, n_hours=n_hours,
                            density_level=cfg["simulation"].get("density_level", "peak"))
    ds = gen.generate()
    out = gen.to_csv(args.subdir)
    print("people:", ds.people["birth_tick"].size,
          "vehicles:", ds.vehicles["birth_tick"].size)
    print("write ->", out)
    return 0


def cmd_selftest(args, cfg):
    for mod in LIGHT_SELFTESTS:
        print(f"\n===== selftest: {mod} =====")
        runpy.run_module(mod, run_name="__main__")
    return 0


def cmd_paths(args, cfg):
    print("topology file:", cfg["topology"]["file"])
    print("  exists:", cfg["topology"]["file"].exists())
    print("a_module_dir:", cfg["topology"]["a_module_dir"])
    print("data_dir:", cfg["paths"]["data_dir"])
    print("log_dir:", cfg["paths"]["log_dir"])
    return 0


# ---------------------------------------------------------------------------
# cache —— 网络缓存（控制状态 / 路径缓存）持久化
# ---------------------------------------------------------------------------

def cmd_cache(args, cfg):
    topo = Topology(cfg["topology"]["file"])
    if args.action == "export":
        if args.state:
            topo.network.export_state(args.state)
        if args.cache:
            topo.export_cache(args.cache)
        if not args.state and not args.cache:
            print("未指定 --state 或 --cache，未导出任何内容")
    else:  # import
        if args.state:
            topo.network.import_state(args.state)
            print("控制状态导入 ->", topo.get_gate_states(), topo.get_door_states())
        if args.cache:
            topo.import_cache(args.cache)
            print(f"网络缓存导入 -> {topo.n_cached_paths()} 条路径")
        if not args.state and not args.cache:
            print("未指定 --state 或 --cache，未导入任何内容")
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _add_clock_args(p):
    """给子命令加实时节拍 + 时间戳来源参数。"""
    p.add_argument("--hz", type=float, default=None,
                   help="实时节拍频率（tick/秒；缺省取 config simulation.tick_hz，0=全速）")
    p.add_argument("--max-speed", action="store_true",
                   help="全速跑完不节拍（等价 --hz 0）")
    p.add_argument("--clock", choices=["now", "base"], default=None,
                   help="输出时间戳来源: now=当下时间 / base=start_datetime+tick 秒")
    p.add_argument("--start-datetime", default=None,
                   help="clock=base 时数据起始时间 YYYY-MM-DD HH:MM:SS（缺省取 config simulation.start_datetime）")


def build_parser():
    p = argparse.ArgumentParser(
        prog="main",
        description="园区安全智能调控平台统一入口",
    )
    p.add_argument("--config", default=None, help="覆盖配置文件路径")

    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="完整仿真流水线（默认）")
    r.add_argument("--n-people", type=int, default=None)
    r.add_argument("--n-vehicles", type=int, default=None)
    r.add_argument("--n-ticks", type=int, default=None)
    r.add_argument("--n-days", type=int, default=None)
    r.add_argument("--interval", type=int, default=None, help="落盘间隔 tick（缺省 10）")
    r.add_argument("--import-state", default=None, help="导入控制状态 JSON")
    r.add_argument("--import-cache", default=None, help="导入网络缓存 JSON（控制+路径）")
    r.add_argument("--export-state", default=None, help="导出控制状态 JSON")
    r.add_argument("--export-cache", default=None, help="导出网络缓存 JSON（控制+路径）")
    r.add_argument("--no-predict", action="store_true",
                   help="关闭预测+预警（只写 JSON+CSV）")
    r.add_argument("--backend", default=None,
                   help="实时数据上传后端地址（覆盖 config realtime_backend，如 http://127.0.0.1:8000）")
    r.add_argument("--no-upload", action="store_true",
                   help="关闭实时数据上传（不 POST union_pack 给后端）")
    _add_clock_args(r)
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("stress", help="压测 1000→8000")
    s.add_argument("--n-ticks", type=int, default=100)
    s.add_argument("--warmup", type=int, default=10)
    s.add_argument("--levels", type=int, nargs="*", default=None)
    s.set_defaults(func=cmd_stress)

    v = sub.add_parser("verify", help="正确性校验")
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("data", help="生成数据落盘")
    d.add_argument("--n-people", type=int, default=None)
    d.add_argument("--n-vehicles", type=int, default=None)
    d.add_argument("--n-days", type=int, default=1)
    d.add_argument("--subdir", default=".")
    d.set_defaults(func=cmd_data)

    st = sub.add_parser("selftest", help="轻量模块自测")
    st.set_defaults(func=cmd_selftest)

    j = sub.add_parser("json", help="每 10s 快照落盘 JSON（sender 驱动循环）")
    j.add_argument("--n-people", type=int, default=None)
    j.add_argument("--n-vehicles", type=int, default=None)
    j.add_argument("--n-ticks", type=int, default=None)
    j.add_argument("--n-days", type=int, default=None)
    j.add_argument("--interval", type=int, default=None, help="落盘间隔 tick（缺省 10）")
    j.add_argument("--json-dir", default=None, help="JSON 打包窗口根目录（缺省 config data_dir）")
    j.add_argument("--csv-path", default=None,
                   help="联动追加固定 CSV（同一轮 run 每 interval 同时写 JSON 与 CSV）")
    _add_clock_args(j)
    j.set_defaults(func=cmd_json)

    c = sub.add_parser("csv", help="每 10s 快照追加固定 CSV（累计落盘）")
    c.add_argument("--n-people", type=int, default=None)
    c.add_argument("--n-vehicles", type=int, default=None)
    c.add_argument("--n-ticks", type=int, default=None)
    c.add_argument("--n-days", type=int, default=None)
    c.add_argument("--interval", type=int, default=None, help="追加间隔 tick（缺省 10）")
    c.add_argument("--csv-path", default=None,
                   help="固定 CSV 路径（缺省 config paths.csv_file）")
    c.add_argument("--truncate", action="store_true",
                   help="run 开始前清空文件重建表头（避免跨运行 tick 重复）")
    _add_clock_args(c)
    c.set_defaults(func=cmd_csv)


    pa = sub.add_parser("paths", help="打印配置路径")
    pa.set_defaults(func=cmd_paths)

    c = sub.add_parser("cache", help="网络缓存导出|导入")
    c.add_argument("action", choices=["export", "import"])
    c.add_argument("--state", default=None, help="控制状态 JSON 路径")
    c.add_argument("--cache", default=None, help="网络缓存 JSON 路径（控制+路径）")
    c.set_defaults(func=cmd_cache)

    return p


_SUBCOMMANDS = {"run", "stress", "verify", "data", "selftest",
                "json", "csv", "paths", "cache"}


def main(argv=None):
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 首个非选项参数不是子命令（或无子命令）时默认 run，允许
    # "python main.py --n-people 100" 这类省略 run 的写法。
    idx = 2 if argv[:1] == ["--config"] and len(argv) >= 2 else 0
    if not argv or idx >= len(argv) or argv[idx] not in _SUBCOMMANDS:
        argv = ["run"] + argv
    args = parser.parse_args(argv)

    func = args.func
    cfg = get_config(args.config)
    return func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
