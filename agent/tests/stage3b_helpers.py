# -*- coding: utf-8 -*-
"""阶段 3b（ag-runtime）测试脚手架：把一段文本切成多个 SSE 分片的假模型。

复用阶段 1 的假传输（`FakeStreamTransport`，经 `stage3_helpers.ScriptedTransport`
装配），把一次助手回答**故意拆成多片** content delta —— 用于验证推送流上的
`message_delta` 确实是**逐段**到达，而不是一次性给全。
"""

import json

from agent.tests.stage3_helpers import sse


def streamed_text_response(chunks):
    """把若干文本片段拼成一次「多分片」的 SSE 流式回答脚本。"""
    lines = [sse({"choices": [{"index": 0, "delta": {"content": chunk}}]})
             for chunk in chunks]
    lines.append(sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    lines.append("data: [DONE]\n\n")
    return lines


def event_types(events):
    """取事件序列里的事件名列表（断言顺序用）。"""
    return [event.get("event") for event in events]
