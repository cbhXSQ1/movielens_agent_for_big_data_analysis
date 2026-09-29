# -*- coding: utf-8 -*-
"""第 5 节 Agent 侧代码包（自然语言任务组织 → 调 Hadoop driver → 解释结果）。

三层结构：
    driver_client.py  调用 driver CLI，解析 JSON 信封（只传话，不加工）
    tools.py          9 个 Agent 工具（8 个契约工具 + 1 个附加工具 quick_clean）
    explain.py        把 JSON 信封翻译成中文（五维对比 / 数据量 / 隔离 / 局限性）
    session.py        运行口径快照：追问沿用第一轮选定的 cluster/local 与 full/sample
    cli.py            命令行入口：python3 -m agent.cli <工具>
    http_api.py       零依赖 HTTP 接口（给第 6 节前端）
    agent.py          自然语言入口 respond()：一句话 → 工具调用 → 中文回答

可选增强（默认关闭，没有配置时行为与纯规则完全一致）：
    llm_config.py / llm_client.py / llm_parse.py —— 大模型可选层，
    只做「意图理解」与「措辞润色」两件事，不生成规则、不产出任何数字。

设计红线：
    * 不编造：driver 失败或未完成时，原样返回错误信封，不填占位数字
    * 隔离 ≠ 修复：解释结果时必须同屏展示数据量变化与隔离影响
    * 权威结果以 start + result 为准，quick_clean 仅供演示
"""

__all__ = ["driver_client", "tools", "explain", "session", "agent", "cli"]
