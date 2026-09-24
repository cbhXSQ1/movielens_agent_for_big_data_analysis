#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/score_groupstats.py —— 评分作业 2/3：按业务键 shuffle 的键统计。

map+reduce，两趟用 `--pass` 选择：

  --pass distinct    key fields = 2。map 发 `"<计数键>\t<键元组>"`，
                     reducer 数不同的键元组个数（每个不同的键恰好一组）。
                     用于 U1 评分键唯一率、U2 完全重复行率、U3 维表 ID 唯一率。
  --pass dupgroups   key fields = 3。map 发 `"<计数键>\t<键元组>\t<值元组>"`，
                     reducer 按 (键, 值) 分组后对每个键累计记录数与不同值个数：
                       记录数 > 1             → 分母 +1（重复组）
                       记录数 > 1 且只有一个值 → 分子 +1（无冲突的重复组）
                     用于 S4 同键矛盾率。

提交时必须配 `-D stream.num.map.output.key.fields=<2|3>`，
否则 Hadoop 只会按第一个字段分组。

**单 reducer** 且只累加整型计数器、不保留键集合：内存与数据量无关，
全量 102 万条评分的唯一键统计也不会撑爆 reducer。

`--source raw` 时无法解析的行按「行号 + 原文」当作独立的一行参与
distinct_row 统计 —— 与本地 `_agg_distinct_row_count` 口径一致。
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
from _common import run_guarded, run_score_groupstats

if __name__ == "__main__":
    run_guarded(run_score_groupstats)
