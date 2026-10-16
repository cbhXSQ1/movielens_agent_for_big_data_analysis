# -*- coding: utf-8 -*-
"""回归：思考型模型的 reasoning_content 必须能被捕获并在工具往返时回传。

真机联调（opencode-go / deepseek-v4.1-flash）暴露：带工具调用的助手消息若不回传
reasoning_content，网关直接报 invalid_request_error（"The reasoning_content in the
thinking mode must be passed back to the API"）。本测试锁住两件事：

  1) SSE 解析把 delta.reasoning_content 产出为 reasoning_delta 事件；
  2) 消息桥把推理内容存进助手消息，并在回灌（to_openai）时带回 reasoning_content。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.llm.sse import SseStreamParser
from agent.runtime import messages as bridge


class _Call(object):
    def __init__(self, name, args, call_id):
        self.name = name
        self.arguments = args
        self.call_id = call_id


class TestReasoningSse(unittest.TestCase):
    def test_reasoning_delta_emitted(self):
        parser = SseStreamParser()
        chunk = ('data: {"choices":[{"delta":{"reasoning_content":"让我想想"}}]}\n'
                 'data: {"choices":[{"delta":{"content":"答案"}}]}\n')
        events = parser.feed(chunk)
        kinds = [getattr(e, "kind", None) for e in events]
        self.assertIn("reasoning_delta", kinds)
        self.assertIn("text_delta", kinds)
        reasoning = [e.text for e in events
                     if getattr(e, "kind", None) == "reasoning_delta"]
        self.assertEqual(reasoning, ["让我想想"])


class TestReasoningPassthrough(unittest.TestCase):
    def test_assistant_message_carries_reasoning(self):
        ai = bridge.assistant_from_calls("", [_Call("list_tasks", {}, "call_1")],
                                         bridge.PROTOCOL_NATIVE, "Think step by step")
        self.assertEqual(ai.additional_kwargs.get("reasoning_content"),
                         "Think step by step")
        out = bridge.to_openai([ai])
        msg = [m for m in out if m.get("role") == "assistant"][0]
        self.assertEqual(msg.get("reasoning_content"), "Think step by step")
        self.assertTrue(msg.get("tool_calls"))

    def test_no_reasoning_means_no_field(self):
        ai = bridge.assistant_from_calls("", [_Call("list_tasks", {}, "call_1")],
                                         bridge.PROTOCOL_NATIVE)
        out = bridge.to_openai([ai])
        msg = [m for m in out if m.get("role") == "assistant"][0]
        self.assertNotIn("reasoning_content", msg)


if __name__ == "__main__":
    unittest.main()
