#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/score_finalize.py —— 评分作业 3/3：计数 → 分数。

map-only。输入是 score_measure 与 score_groupstats 的所有 part 文件，
每行 `"<计数键>\t<数值>"`：普通键求和，`"<mid>.max_ts"` 取最大值。

比率、维度分、综合分**只在这里**算 —— plan §5.2 明令「不得在 driver 内计算比率」。
公式来自 `engine.metrics.metrics_from_counts`，与本地 runner **同一份实现**，
因此「本地黄金测试 = 集群结果」不是靠人工核对维持的。

输出一行 JSON：`{"side": "...", "counts": {...}, "result": {metrics, dimensions, composite}}`。
出现未登记的计数键（作业间串数据）会直接报错，不静默忽略。
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
from _common import run_guarded, run_score_finalize

if __name__ == "__main__":
    run_guarded(run_score_finalize)
