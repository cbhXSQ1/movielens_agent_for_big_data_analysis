# -*- coding: utf-8 -*-
"""driver —— 任务编排与 CLI。

run_task.py 实现 docs/hadoop/agent-interface.md v1.0 契约的全部子命令：
  validate / schemes / start / status / result / samples / report / tasks

stdout 只输出一个 JSON 信封；日志走 stderr；退出码遵循接口文档 §2。

本 __init__.py 使 driver 成为可导入的包（plan.md §3 只列了 run_task.py，
此处补 __init__.py 以便测试与编排代码复用其内部函数，属结构性补充）。
"""
