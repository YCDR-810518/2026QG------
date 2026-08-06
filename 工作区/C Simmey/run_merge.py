# -*- coding: utf-8 -*-
"""run_merge.py —— 60° 夹角双路→单车道协同汇合(CAV+MAS)对比演示 · 入口脚本

实际实现位于同目录 cav_mas_merge/ 包,本文件仅负责调用主流程,
保证任意工作目录下均可直接运行。

用法:
    python run_merge.py
    python -m cav_mas_merge.main
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cav_mas_merge.main import main

if __name__ == "__main__":
    main()
