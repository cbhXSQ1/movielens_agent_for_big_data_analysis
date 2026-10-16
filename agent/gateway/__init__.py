# -*- coding: utf-8 -*-
"""agent/gateway —— Agent 侧的外部网关适配器。

目前只有一个成员：`opencode_proxy`（opencode-go 网关转接头，独立进程）。
它是「Agent ↔ 外部大模型网关」的边界适配层，不掺入 agent 的模型循环。
"""
