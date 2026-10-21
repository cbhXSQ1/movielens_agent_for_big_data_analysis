# -*- coding: utf-8 -*-
"""阶段 1 模型接入层：供应商实现（opencode T1.2 / 纯 OpenAI T1.3）。

两者共用同一抽象（build_request + stream_chat），差异只在**请求装配**：

  纯 OpenAI（T1.3；DeepSeek 官方 API 即此类）
    标准 POST /v1/chat/completions；无状态，多轮靠请求携带完整 messages。

  opencode（T1.2）
    opencode Zen/Go 网关按**会话头**做上下文路由（gateway-probe.md V2）：
    随机会话 = 多轮在网关侧断开。Agent 侧只在每个请求带**稳定的**
    `x-agent-session: <会话键>`；传输层（agent/llm/transport.py）识别出
    opencode 网关（api_base 命中 opencode.ai）后，进程内经 curl 转发并把该头
    转成网关要求的 `x-opencode-session`（agent/gateway/curl_gateway.py），
    网关细节不渗进 Agent 循环（llm-agent-plan.md §2.1）。

错误处理（T1.4）：配置不可用 → 立刻抛 NOT_CONFIGURED；传输/解析异常 → 统一
分类成 ModelError 抛出，**不静默回退**到其它供应商或路径。
"""

import json

from .errors import ModelError, NOT_CONFIGURED, PROVIDER_UNKNOWN, classify_exception
from .sse import SseStreamParser
from .transport import HttpTransport

PROVIDER_OPENAI = "openai"
PROVIDER_OPENCODE = "opencode"

#: opencode 代理侧约定的会话键请求头（代理据此映射到网关会话 UUID）
SESSION_HEADER = "x-agent-session"

#: 思考档位允许值（仅这三档会透传为请求体 reasoning_effort；""/default/其它一律不发）
REASONING_EFFORTS = ("low", "medium", "high")


def normalize_reasoning_effort(value):
    """把「思考档位」归一成可透传的 reasoning_effort 值。

    仅接受 low / medium / high（忽略大小写与首尾空白）；缺省、空串、`default`
    及其它任何取值一律返回 None —— 表示**不发送**该字段（默认行为不变）。
    """
    if isinstance(value, str):
        value = value.strip().lower()
    return value if value in REASONING_EFFORTS else None


class BaseProvider(object):
    """模型接入抽象（T1.1）。普通类、显式循环；供应商可插拔。"""

    provider_name = "base"

    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or HttpTransport()

    def build_request(self, messages, tools=None, options=None):
        """把消息（含工具表）装配成传输层可用的请求 dict。"""
        raise NotImplementedError

    def stream_chat(self, messages, tools=None, options=None):
        """发送消息（含工具表）→ 流式接收事件序列。

        返回一个可迭代对象，依次产出 TextDelta（文本增量）/ ToolCall（工具调用
        请求）/ StreamEnd（结束）。配置不可用时**立即**抛 ModelError；传输与解析
        错误在迭代过程中同样以 ModelError 抛出（不静默回退）。
        """
        if not self.config.usable:
            raise ModelError(
                NOT_CONFIGURED,
                "大模型未配置或配置不完整（需要 api_base / api_key / model，"
                "且总开关与 enabled 打开）")
        return self._stream(messages, tools, options)

    def _stream(self, messages, tools, options):
        parser = SseStreamParser()
        try:
            request = self.build_request(messages, tools, options)
            for chunk in self.transport.post_stream(request):
                for event in parser.feed(chunk):
                    yield event
                if parser.done:
                    break
            for event in parser.finish():
                yield event
        except ModelError:
            raise                              # 已结构化，原样透传
        except Exception as exc:
            raise classify_exception(exc)

    def describe(self):
        """健康检查用的脱敏视图。"""
        view = self.config.describe()
        view["provider"] = self.provider_name
        return view


class OpenAIProvider(BaseProvider):
    """纯 OpenAI 兼容格式（DeepSeek 官方 API 属此类）。"""

    provider_name = PROVIDER_OPENAI

    def _headers(self):
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "text/event-stream",
        }
        if self.config.base.api_key:
            headers["Authorization"] = "Bearer " + self.config.base.api_key
        return headers

    def build_request(self, messages, tools=None, options=None):
        opts = options or {}
        body = {
            "model": opts.get("model") or self.config.base.model,
            "messages": messages,
            "stream": True,
            "temperature": opts.get("temperature", self.config.base.temperature),
        }
        # 默认不限制输出：0/未配置就不发 max_tokens，避免推理段吃光预算导致正文为空。
        max_tokens = int(opts.get("max_tokens") or self.config.base.max_tokens or 0)
        if max_tokens > 0:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        effort = normalize_reasoning_effort(opts.get("reasoning_effort"))
        if effort is not None:
            body["reasoning_effort"] = effort
        timeout = opts.get("timeout") or self.config.base.timeout_sec
        return {
            "url": self.config.endpoint(),
            "headers": self._headers(),
            "body": json.dumps(body, ensure_ascii=False).encode("utf-8"),
            "timeout": timeout,
        }


class OpencodeProvider(OpenAIProvider):
    """opencode 网关：在 OpenAI 形态上加**稳定会话头**做上下文适配。"""

    provider_name = PROVIDER_OPENCODE

    def _headers(self):
        headers = OpenAIProvider._headers(self)
        key = self.config.session_key
        if key:
            # 稳定会话键：同一段对话的每轮请求都带同一个值，传输层在 opencode
            # 分支把它转成同一网关会话 UUID，保证网关侧多轮延续（不随机、不每轮新建）。
            headers[SESSION_HEADER] = key
        return headers


def build_provider(config, transport=None):
    """按配置挑供应商；未知 provider 直接抛 PROVIDER_UNKNOWN。"""
    name = (getattr(config, "provider", "") or PROVIDER_OPENAI).strip().lower()
    if name == PROVIDER_OPENAI:
        return OpenAIProvider(config, transport)
    if name == PROVIDER_OPENCODE:
        return OpencodeProvider(config, transport)
    raise ModelError(PROVIDER_UNKNOWN,
                     "未知模型供应商：%s（支持 openai / opencode）" % name)
