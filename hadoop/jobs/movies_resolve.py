#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/movies_resolve.py —— 维表电影作业 2/3：movies_resolve —— 同 MovieID 去重与冲突裁决（M4）。

reduce，key = MovieID。内容相同 → 去重；冲突 → 保留合法字段最多的记录
（标题非空、无 [20XX]、无乱码、类型合法）；并列 → 取原始行字典序最小者，
保证本地与集群选出同一条。

用法（本地 stdin→stdout 自测，plan §9.5 要求先过这一关才允许上集群）::

    cat fixture | python3 movies_resolve.py --mode keep
    cat fixture | python3 movies_resolve.py --mode quarantine

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

from _common import run_guarded, run_resolve

if __name__ == "__main__":
    run_guarded(lambda: run_resolve("movies"))
