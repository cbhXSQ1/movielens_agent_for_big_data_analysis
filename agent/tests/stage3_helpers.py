# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）测试脚手架：假模型（FakeStreamTransport）+ 假工具后端。

复用阶段 1 的 `agent.llm.FakeStreamTransport`，把**每个响应**包一个假传输，
按顺序喂给 provider —— 于是可以造「先 list_tasks、再基于结果给最终回答」这类
**多步工具调用**场景，且全程不碰真实网关、不碰真实 driver。
"""

import json

from agent import llm, llm_config
from agent.llm import FakeStreamTransport, ModelError, UPSTREAM_ERROR
from agent.llm.config import ModelConfig


def sse(obj):
    """把一个 JSON 对象包成一条 SSE data 行。"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def text_response(text):
    """一段纯文本最终回答的 SSE 脚本。"""
    return [
        sse({"choices": [{"index": 0, "delta": {"content": text}}]}),
        sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]\n\n",
    ]


def tool_response(name, arguments):
    """一次原生 tool_calls 工具调用请求的 SSE 脚本（参数可切成多片）。"""
    raw = json.dumps(arguments, ensure_ascii=False)
    return [
        sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": name, "arguments": ""}}]}}]}),
        sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": raw}}]}}]}),
        sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]\n\n",
    ]


class ScriptedTransport(object):
    """按顺序为每个请求返回一份响应脚本的假传输。

    每个响应本身用一个 `FakeStreamTransport` 承载（真正复用阶段 1 的脚手架）。
    脚本用尽后重复最后一个响应（便于「循环上限」用例）。
    """

    def __init__(self, scripts):
        self._transports = [FakeStreamTransport(s) for s in scripts]
        self.index = 0

    def post_stream(self, request):
        idx = min(self.index, len(self._transports) - 1)
        self.index += 1
        return self._transports[idx].post_stream(request)


def make_provider(scripts, provider_name="openai", session_key=""):
    """用假传输装一个可用配置的 provider。"""
    base = llm_config.LLMConfig({
        "supported": True, "enabled": True,
        "api_base": "https://api.example.com/v1", "api_key": "sk-test",
        "model": "fake-model",
    })
    config = ModelConfig(base, {"provider": provider_name, "session_key": session_key})
    return llm.build_provider(config, ScriptedTransport(scripts))


def error_provider(code=UPSTREAM_ERROR, message="上游服务错误（假模型）"):
    """构造一个调用即抛结构化 ModelError 的 provider（不触网、无需 HTTP 异常）。"""
    base = llm_config.LLMConfig({
        "supported": True, "enabled": True,
        "api_base": "https://api.example.com/v1", "api_key": "sk-test",
        "model": "fake-model",
    })
    config = ModelConfig(base, {"provider": "openai"})
    return llm.build_provider(config,
                              FakeStreamTransport(error=ModelError(code, message)))


class FakeToolBackend(object):
    """假工具执行后端：按工具名返回预置信封，并记录调用。"""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        envelope = self.responses.get(name)
        if envelope is None:
            return False, {"ok": False, "error": {
                "code": "USAGE", "message": "假后端没有登记工具 %s" % name}}
        return bool(envelope.get("ok")), envelope


#: 常用假数据：两个任务（一个成功、一个运行中）
SAMPLE_TASKS = {
    "ok": True,
    "tasks": [
        {"task_id": "2026-aaaaaaaa", "status": "succeeded", "data_version": "v1"},
        {"task_id": "2026-bbbbbbbb", "status": "running", "data_version": "v1"},
    ],
    "total": 2,
}
