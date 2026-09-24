#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/movies_residual.py —— 维表电影作业 3/3：movies_residual —— 兜底检查（M3 M6 M7 M9）。

map-only。这些规则都不需要分组上下文，故不引入 shuffle：
  M3 空标题 → 隔离（兜底，正常已由 M4 处理）
  M6 年份异常 / M7 非法类型 → 标记不删
  M9 类型空 token 或重复 token → 只报告（check）

用法（本地 stdin→stdout 自测，plan §9.5 要求先过这一关才允许上集群）::

    cat fixture | python3 movies_residual.py --mode keep
    cat fixture | python3 movies_residual.py --mode quarantine

集群上由 driver 提交，engine/ 与 config/ 以 engine.zip 经 -files 分发。
"""
import os
import sys

# 引导 sys.path。两条现实约束：
#   1) 集群上 Hadoop 把 -files 以**符号链接**放进任务工作目录，而 Python 执行
#      `python3 x.py` 时可能把 sys.path[0] 设成 x.py 解析后的**真实路径**（appcache 里），
#      那里没有 _common.py —— 只找脚本目录会 ModuleNotFoundError。
#   2) 本地源码树与打包出来的 engine.zip 同时存在时，**源码树必须优先**，
#      否则改完代码跑本地测试仍会 import 到陈旧的 zip。
# 因此按「cwd > 脚本目录 > <repo>/hadoop > engine.zip」的顺序挂 path。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in reversed((os.getcwd(), _HERE, os.path.dirname(_HERE), "engine.zip")):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from _common import run_guarded, run_residual

if __name__ == "__main__":
    run_guarded(lambda: run_residual("movies"))
