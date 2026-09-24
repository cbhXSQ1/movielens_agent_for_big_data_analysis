#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs/clean_finalize.py —— 收尾作业：内部记录 → 交付格式的 cleaned 表。

把内部 JSON 记录变成 `::` 分隔、ISO-8859-1 的交付文件，并让全表按
**业务键数值序**排列：

  mapper  （`--table ratings`）         发 `"<零填充业务键>\t<:: 行>"`
  reducer （`--table ratings --reduce`）只输出 `<:: 行>`

为什么需要零填充：Hadoop 按 Text 排序，是**字典序**（`"10" < "2"`）。
把业务键零填充成定宽数字后，字典序与数值序一致；配合**单 reducer**
（`-D mapreduce.job.reduces=1`，plan §10 已认可）得到全局有序输出。
于是集群 cleaned 三表与本地 runner 逐字节相同 —— 这正是 §7.4 哈希对账的前提。

见 decisions.md D-012。
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

from _common import parse_args, run_finalize, run_guarded

TABLES = ("ratings", "users", "movies")


def load_table():
    table = parse_args().get("table")
    if table not in TABLES:
        raise SystemExit("--table 必须是 %s，得到 %r" % (" / ".join(TABLES), table))
    return table


if __name__ == "__main__":
    run_guarded(lambda: run_finalize(load_table()))
