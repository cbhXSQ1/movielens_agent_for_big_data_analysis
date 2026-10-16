# -*- coding: utf-8 -*-
"""第 5 节 Agent 侧代码包（自然语言任务组织 → 调 Hadoop driver → 解释结果）。

三层结构：
    driver_client.py  调用 driver CLI，解析 JSON 信封（只传话，不加工）
    tools.py          9 个 Agent 工具（8 个契约工具 + 1 个附加工具 quick_clean）
    explain.py        把 JSON 信封翻译成中文（五维对比 / 数据量 / 隔离 / 局限性）
    cli.py            命令行入口：python3 -m agent.cli <工具>
    http_api.py       零依赖 HTTP 接口（给第 6 节前端）

运行时（阶段 3a，ag-runtime）：
    runtime/          LangGraph 运行时：对话与节点持久化、血缘、工具调用循环。
                      agent/llm_loop.py 与 agent/session.py 已删除，由 runtime/ 取代。
    自然语言入口（/api/chat）走 runtime 的「模型 → 工具 → 策略 → 执行 → 回灌」
    循环；模型不可用时结构化报错，不回退规则。

大模型层：
    llm_config.py / llm_client.py —— 大模型配置与 HTTP 调用（旧一次性形态）。
    llm/ —— 阶段 1 模型接入层（双供应商、流式事件、结构化错误）。
    （纯规则意图解析与旧的一次性 LLM 增强已删除，见 Agent_重构计划.md T2.5。）

阶段 2（ag-tools）新增的独立件（新路径依赖，不引用旧意图模块）：
    constants.py     中立领域常量（维度名 / 任务号正则 / 意图中文名）
    number_guard.py  数字溯源护栏（字段 + 上下文级）
    protocol.py      双协议解析 + 「是否发起任务」判定
    tool_facts.py    策略校验事实收集（任务锁 / 原始数据可读，可注入）

设计红线：
    * 不编造：driver 失败或未完成时，原样返回错误信封，不填占位数字
    * 隔离 ≠ 修复：解释结果时必须同屏展示数据量变化与隔离影响
    * 权威结果以 start + result 为准，quick_clean 仅供演示
"""

__all__ = ["driver_client", "tools", "explain", "cli",
           "tool_specs", "tool_policy", "constants", "number_guard",
           "protocol", "tool_facts", "runtime"]
