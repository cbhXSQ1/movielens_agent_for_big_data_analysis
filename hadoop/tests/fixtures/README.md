# -*- coding: utf-8 -*-
"""tests/fixtures —— 集成测试用的三表小样本与期望输出。

plan.md §7.2 要求：约 60 行三表小数据，覆盖每类注入问题；
期望 cleaned / quarantine / 分数作为断言文件。

约定：
  * 输入文件用 ISO-8859-1 编码、`::` 分隔，与真实数据一致
  * 期望输出由**引擎本身**生成后人工核对，禁止手抄
  * fixture 只用于"作业脚本 stdin→stdout"与端到端集成测试；
    全量数字断言属于 §7.3 黄金测试（hadoop/tests/test_golden.py）

本文件仅为让空目录进入版本库并说明用途；fixture 数据在 M2 任务中生成。
"""
