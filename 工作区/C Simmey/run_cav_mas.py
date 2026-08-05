# -*- coding: utf-8 -*-
"""run_cav_mas.py —— CAV+MAS 对比演示 · 入口脚本

实际实现位于同目录 cav_mas/ 包（config / topology / entities / engine /
metrics / privacy / visualization / main），本文件仅负责调用主流程，
并保证任意工作目录下均可直接运行。

场景：图书馆 → 直道(220m) → 工一（拐弯） → 直道(140m) → 教一，总长 360m，弯道处限速。
输出：控制台量化指标 + figures/ 下对比图、动画 GIF，以及差分隐私脱敏后的路径 CSV。

用法：
    python run_cav_mas.py
    python -m cav_mas.main
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cav_mas.main import main

if __name__ == "__main__":
    main()
