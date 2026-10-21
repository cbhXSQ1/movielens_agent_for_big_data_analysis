# -*- coding: utf-8 -*-
"""阶段 1 模型接入层：流式事件模型（T1.1 的「流式接收」出口）。

统一约定消费方只看到三种事件（纯数据类，无第三方依赖）：
    TextDelta   —— 文本增量片段（对应接口文档 §3.6 的 message_delta）
    ToolCall    —— **已聚合完成**的工具调用请求（工具名 + 参数）
    StreamEnd   —— 本次流结束（携带 finish_reason）

供应商只负责把这些事件按流式顺序 yield 出去；消费方（阶段 3 运行时）
据此组装对话、触发工具执行与推送，不必关心供应商与协议差异。
"""

EVENT_TEXT_DELTA = "text_delta"
EVENT_REASONING_DELTA = "reasoning_delta"
EVENT_TOOL_CALL = "tool_call"
EVENT_DONE = "done"


class TextDelta(object):
    """流式文本增量片段。"""

    kind = EVENT_TEXT_DELTA

    def __init__(self, text):
        self.text = text or ""

    def to_dict(self):
        return {"type": self.kind, "text": self.text}

    def __repr__(self):
        return "TextDelta(%r)" % (self.text,)


class ReasoningDelta(object):
    """思考型模型的**推理内容**增量片段。

    不给用户看，但思考型网关（如 opencode-go 上的 deepseek-v4.1-flash）在
    「带工具调用的助手消息」往返时要求把上一条的 reasoning_content 原样回传，
    否则报 `The reasoning_content in the thinking mode must be passed back`。
    故这里也作为一种事件捕获，交由运行时随助手消息保存、回灌时带回。
    """

    kind = EVENT_REASONING_DELTA

    def __init__(self, text):
        self.text = text or ""

    def to_dict(self):
        return {"type": self.kind, "text": self.text}

    def __repr__(self):
        return "ReasoningDelta(%r)" % (self.text,)


class ToolCall(object):
    """一次工具调用请求：工具名 + 已解析参数（流式分片已聚合）。"""

    kind = EVENT_TOOL_CALL

    def __init__(self, name, arguments, call_id="", raw_arguments=""):
        self.name = name or ""
        self.arguments = arguments if isinstance(arguments, dict) else {}
        self.call_id = call_id or ""
        #: 聚合前的原始 arguments 字符串（参数非 JSON 时供排错，不参与调用）
        self.raw_arguments = raw_arguments or ""

    def to_dict(self):
        return {"type": self.kind, "id": self.call_id, "name": self.name,
                "arguments": self.arguments}

    def __repr__(self):
        return "ToolCall(%r, %r)" % (self.name, self.arguments)


class StreamEnd(object):
    """一次流式响应的结束标记。"""

    kind = EVENT_DONE

    def __init__(self, finish_reason="stop"):
        self.finish_reason = finish_reason or "stop"

    def to_dict(self):
        return {"type": self.kind, "finish_reason": self.finish_reason}

    def __repr__(self):
        return "StreamEnd(%r)" % (self.finish_reason,)


def event_to_dict(event):
    """把事件转成可 JSON 化的 dict（推给前端 / 落事件日志用）。"""
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return {"type": "unknown", "repr": repr(event)}
