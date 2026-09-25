# -*- coding: utf-8 -*-
"""engine —— 迭代一 Hadoop 侧纯本地核心引擎（无 Hadoop 依赖）。

设计约束（plan.md §9、§4）：
  * 只用 Python 标准库（3.8+ 兼容语法）
  * 不 import 任何 Hadoop / YARN 相关模块
  * 本地 runner 与 Hadoop Streaming 作业**共用同一份引擎代码**，
    以保证「本地黄金测试 == 集群结果」

模块：
  config_loader.py  配置加载、校验、版本哈希
  operators.py      31 个检测算子 + evaluate()
  actions.py        修复策略、resolve 策略、隔离记录
  pipeline.py       本地全流程（§5 顺序，内存版）
  metrics.py        五维评分公式
"""

__all__ = ["config_loader", "operators", "actions", "pipeline", "metrics"]
