# -*- coding: utf-8 -*-
"""cav_mas_merge —— 60° 夹角双路汇入单车道协同汇合(CAV+MAS)包

场景:主路 A(水平)与汇入路 B(与之成 60° 夹角)在汇合点合并为单车道,
车辆先沿各自车道行驶,再汇聚为单车道车流。对照组为 IDM 独立决策 +
主路优先让行(汇入车排队),实验组为 CAV 协调器时隙分配(到点即插不停车)。

子模块:config / topology / entities / engine / metrics / visualization /
simulator / main。入口脚本 run_merge.py 在上级目录。
"""
