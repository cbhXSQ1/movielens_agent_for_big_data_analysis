# -*- coding: utf-8 -*-
"""agent/gateway —— Agent 侧的外部网关适配（**进程内模块**）。

成员：`curl_gateway`。它把「Agent ↔ opencode-go 网关」的边界适配收进普通模块
（URL 判定、会话头转换、curl 流式转发），由 `agent/llm/transport.py` 按 api_base
的 URL **进程内**复用 —— 不再需要单独拉起一个小服务进程。

边界口径：网关专属细节（TLS 指纹、会话头名、curl 转发）止步于此，不渗进 Agent 循环。
"""
