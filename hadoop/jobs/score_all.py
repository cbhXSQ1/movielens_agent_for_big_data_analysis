#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/score_all.py —— 评分作业（D-014 合并版）：每侧一趟，取代原三件套。

原设计每侧 10 趟（measure×3、groupstats distinct/dupgroups×6、finalize）；
合并后每侧 1 趟（mapper 按输入路径分派表，单 reducer 内存聚合）。

mapper 发射四种线，reducer 在输入结束时用 engine.metrics.metrics_from_counts
算出 18 指标 / 五维 / 综合分，输出**唯一**一行 JSON
（{"side","counts","result"}，与 agent-interface 的 scores 语义一致）。

`--source raw|cleaned`：before 用原始行（分母含坏行）、after 用清洗后 K 流。
`--table` 供本地测试显式指定；集群上由 mapreduce_map_input_file 自动分派。

提交时要求：
  -D mapreduce.job.reduces=1
  -D mapreduce.reduce.memory.mb=2048    # 聚合几百 MB；本机 NM 上限 4096，3072 永不分配（D-014）
"""
import os
import sys

# 引导 sys.path（与其它作业一致）：集群上用 -files 分发 engine.zip，
# 本地优先源码树，避免 import 到陈旧的 zip。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in reversed((os.getcwd(), _HERE, os.path.dirname(_HERE), "engine.zip")):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from _common import run_guarded, run_score_all

if __name__ == "__main__":
    run_guarded(run_score_all)
