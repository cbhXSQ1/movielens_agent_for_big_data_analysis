# -*- coding: utf-8 -*-
"""阶段 1 · 模型接入层（T1.1–T1.5）：委托新子包，不改动现有 llm_client / llm_config。

对外只暴露这几样：
    load / ModelConfig                  配置（T1.5：三级合并 + 脱敏）
    build_provider                      按配置挑供应商（T1.1）
    OpenAIProvider / OpencodeProvider   两供应商实现（T1.3 / T1.2）
    ModelError / 错误码常量             结构化错误（T1.4）
    TextDelta / ToolCall / StreamEnd    流式事件
    HttpTransport / FakeStreamTransport 传输层（后者是 TX.1 假模型脚手架）

用法：
    from agent.llm import load, build_provider, ModelError
    provider = build_provider(load())
    try:
        for event in provider.stream_chat(messages, tools=tools):
            ...
    except ModelError as err:
        envelope = err.to_envelope()
"""

from .config import ModelConfig, load, PROVIDER_OPENAI, PROVIDER_OPENCODE
from .errors import (ModelError, NOT_CONFIGURED, PROVIDER_UNKNOWN, AUTH_FAILED,
                     BAD_PATH, RATE_LIMITED, BAD_REQUEST, TIMEOUT,
                     NETWORK_UNREACHABLE, UPSTREAM_ERROR, HTTP_ERROR,
                     BAD_RESPONSE, MODEL_ERROR, classify_exception,
                     classify_http_code)
from .events import TextDelta, ToolCall, StreamEnd, event_to_dict
from .providers import (BaseProvider, OpenAIProvider, OpencodeProvider,
                        build_provider)
from .sse import SseStreamParser
from .transport import HttpTransport, FakeStreamTransport

__all__ = [
    "ModelConfig", "load", "PROVIDER_OPENAI", "PROVIDER_OPENCODE",
    "ModelError", "NOT_CONFIGURED", "PROVIDER_UNKNOWN", "AUTH_FAILED",
    "BAD_PATH", "RATE_LIMITED", "BAD_REQUEST", "TIMEOUT",
    "NETWORK_UNREACHABLE", "UPSTREAM_ERROR", "HTTP_ERROR", "BAD_RESPONSE",
    "MODEL_ERROR", "classify_exception", "classify_http_code",
    "TextDelta", "ToolCall", "StreamEnd", "event_to_dict",
    "BaseProvider", "OpenAIProvider", "OpencodeProvider", "build_provider",
    "SseStreamParser", "HttpTransport", "FakeStreamTransport",
]
