#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/score_measure.py —— 评分作业 1/3：逐记录归约出计数。

map-only（`-D mapreduce.job.reduces=0`）。每行输出 `"<计数键>\t<数值>"`：

  * 普通聚合：该记录的贡献（count 类 0/1，字段/token 类为记录内个数）
  * `count on raw_lines`：对**每一条非空原始行**记 1 —— 含无法解析的坏行，
    因为 C2/C3/U2/S1 的分母就是原始行数
  * 新鲜度：`"<mid>.max_ts\t<时间戳>"`，最大值留给 finalize 取

唯一键 / 重复组这类**键集合**聚合交给 score_groupstats；本作业不保留任何集合，
内存与数据量无关。

`--source raw|cleaned`（before 用 raw、after 用 cleaned），`--table` 指定表。
同一套作业对每张表各跑一遍，与本地 runner 的 dataset 逐表对应。
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
from _common import run_guarded, run_score_measure

if __name__ == "__main__":
    run_guarded(run_score_measure)
