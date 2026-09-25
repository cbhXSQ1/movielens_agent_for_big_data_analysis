#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/ratings_validate.py —— 评分表作业 1/3：解析 / 校验 / 修复。

覆盖规则 P2 P3 R1 R2 R3 R4 R5，map-only（`-D mapreduce.job.reduces=0`），双模式两趟。

阶段内顺序是**语义关键**，不能重排：
  * R4（毫秒→秒）必须**先于** R5（范围校验）。毫秒值本身就是一个越界整数，
    先判范围会把 13,503 条毫秒记录误隔离。实测：R4 前越界 25,506 条，
    R4 修复 13,503 条之后 R5 才是 12,003 条。
  * R3（时间戳非整数）在 R4 前：非整数不参与毫秒判定。
  * R1（键为空）在最前：无键的记录无法定位，不必再判值域。

顺序由 config 的 pipeline 阶段顺序决定，作业不自行编排序号 ——
见 engine/pipeline.py 的 _LINE_STAGES 与 RuleBook.ids。
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
from _common import run_guarded, run_normalize

if __name__ == "__main__":
    run_guarded(lambda: run_normalize("ratings"))
