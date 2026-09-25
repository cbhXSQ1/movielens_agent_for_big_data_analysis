#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/stats_marks.py —— 统计与标记作业（R9 / M8 / R8 / X3），产出 stats.json 组成部分。

单 reducer（`-D mapreduce.job.reduces=1`）。两个互补的趟，用 `--source` 选择：

  --source raw-ratings   输入带行号的**原始**评分表
                         → R9：每个用户的非法评分条数（超阈值即注入嫌疑）
  --source cleaned       输入清洗后的 ratings + users + movies（可多个 -input）
                         → R8 同用户同电影多时间戳；M8 同名不同 ID；
                           X3 维表里从未被评分的对象

为什么 R9 要单独回到原始数据：它的输入是「非法评分」，而那些记录已被 R2 隔离，
清洗结果里再也数不出来。行为类统计不能从清洗结果反推。

记录是**自描述**的：按字段集合分派（有 Rating → 评分；有 Title → 电影；
有 Gender → 用户），所以三种清洗产物可以混在同一个 `-input` 里，
不需要靠文件名区分。

X3 的差集（从未被评分 = 维表键 − 评分引用键）需要在输入结束时做全局集合运算，
这正是**单 reducer** 的用途；聚集体都很小（用户 6,040 / 电影 3,883 / 同名组 218）。

R9 的「命中记录数」由 cleaned 趟统计：driver 把 raw 趟的 part 文件取回、
抽出匹配用户集合，再经 `--r9-users r9_users.json` 传给 cleaned 趟。
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

from _common import run_guarded, run_stats_marks

if __name__ == "__main__":
    run_guarded(run_stats_marks)
