#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/ratings_cross.py —— 评分表作业 3/3：跨表引用校验（X1 / X2）。

map-only。X1 = UserID 不在清洗后的用户表；X2 = MovieID 不在清洗后的电影表。
两者都会级联影响下游，必须隔离而不是猜测补全。

维表经 `-files` 广播，用内部 JSONL（users_resolve / movies_resolve 的 keep 产物）
直接读取，无需先落一遍交付格式再读回来：

    -files ...,users_dim.jsonl,movies_dim.jsonl
    -mapper "... ratings_cross.py --users users_dim.jsonl --movies movies_dim.jsonl"

拿不到维表时 load_dim_keys 会报错退出 —— 「不知道」绝不能被当成「引用有效」。
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
from _common import run_guarded, run_ratings_cross

if __name__ == "__main__":
    run_guarded(run_ratings_cross)
