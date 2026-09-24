#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/ratings_dedupe.py —— 评分表作业 2/3：去重与冲突兜底（R6 + R7）。

map+reduce，key = (UserID, MovieID, Timestamp)。

  mapper  发 `"<UserID>::<MovieID>::<Timestamp>\t<内部记录>"`
  reducer 组内先按**原始行**字典序排序，再调 group_stage_one 应用 R6 的 keep 口径；
          随后用 residual_stage_one 判定 R7（同键评分冲突 → 整组隔离）。

R6 之后每个业务键只剩一条记录，故 R7 在本数据上结构性为 0 —— 与本地 pipeline 一致。
R8（同用户同电影多时间戳）键不同且只标记不删，由 stats_marks 统计。

`--mode quarantine` 输出的是**被去重**的记录（rule_id=R6）。它们属于 counts.dedupe，
不是隔离区内容，driver 会把这一趟写到 dedupe 审计目录而非 quarantine/。
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
from _common import run_guarded, run_ratings_dedupe

if __name__ == "__main__":
    run_guarded(run_ratings_dedupe)
