# -*- coding: utf-8 -*-
"""config.py —— 仿真引擎配置读取（成员 F）

依据《变量及接口命名规范.md》附录 B：路径与运行参数集中注入 config.yaml，
代码只负责"读配置"。本模块同时负责把 A 的 DjShortCut 模块目录加入 sys.path，
使 topology.py 可以直接 import TrafficNetwork / ShortestPathFinder。

依赖：pyyaml
用法：
    from config import get_config
    cfg = get_config()
    topo = Topology(cfg["topology"]["file"])
"""
import sys
from pathlib import Path

import yaml

# 确保 F 工作区目录与本包目录都在 sys.path 上
# （引擎需 import flow_data_generator；包内部用绝对导入，包式 import 也需本目录）
_PKG_DIR = Path(__file__).resolve().parent
_F_WORKSPACE = _PKG_DIR.parent
for _p in (_F_WORKSPACE, _PKG_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_DEFAULT_CONFIG_PATH = _F_WORKSPACE / "config.yaml"

_DEFAULTS = {
    "simulation": {
        "seed": 42,
        "dt": 1.0,
        "max_capacity": 10000,
        "tick_hz": 1,
        "clock": "now",
        "start_datetime": "2026-08-04 12:30:00",
        "check_interval": 10,
        "enable_signals": True,
        "vehicle_compliance": 1.0,
        "ped_compliance": 0.3,
        "movement_class": "ConstantSpeedMovement",
        "movement_mode": "idm",
    },
    "topology": {
        "file": "../../项目目录/graph_data.yaml",
        "a_module_dir": "../../项目目录",
    },
    "paths": {
        "data_dir": "./data",
        "log_dir": "./logs",
    },
}


def _resolve(base_dir: Path, value) -> Path:
    """把配置中的相对路径解析为相对配置文件目录的绝对路径。"""
    p = Path(str(value))
    return p if p.is_absolute() else (base_dir / p).resolve()


def load_config(config_path=None) -> dict:
    """加载 config.yaml 并注入 A 模块目录到 sys.path。

    Parameters
    ----------
    config_path : str or Path, optional
        配置文件路径；缺省取本模块同级目录的 config.yaml。

    Returns
    -------
    dict
        解析后的配置字典，路径字段已转为绝对 Path。
    """
    cfg_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    base_dir = cfg_path.resolve().parent

    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for section, values in loaded.items():
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
            else:
                cfg[section] = values

    a_module_dir = _resolve(base_dir, cfg["topology"]["a_module_dir"])
    if a_module_dir.exists() and str(a_module_dir) not in sys.path:
        sys.path.insert(0, str(a_module_dir))

    cfg["topology"]["file"] = _resolve(base_dir, cfg["topology"]["file"])
    cfg["paths"]["data_dir"] = _resolve(base_dir, cfg["paths"]["data_dir"])
    cfg["paths"]["log_dir"] = _resolve(base_dir, cfg["paths"]["log_dir"])
    return cfg


def get_config(config_path=None) -> dict:
    """单例读取配置（带缓存，供各模块共享同一份配置）。"""
    if getattr(get_config, "_cache", None) is None or config_path is not None:
        get_config._cache = load_config(config_path)
    return get_config._cache


def sim_params(config=None) -> dict:
    """返回 simulation 段参数字典（引擎构造常用）。"""
    cfg = config or get_config()
    return dict(cfg["simulation"])


if __name__ == "__main__":
    c = get_config()
    print("simulation:", c["simulation"])
    print("topology file:", c["topology"]["file"])
    print("topology exists:", c["topology"]["file"].exists())
