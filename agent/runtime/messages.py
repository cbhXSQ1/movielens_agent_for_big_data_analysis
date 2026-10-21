# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· 消息桥：LangGraph 消息 ↔ 模型接入层的 OpenAI 消息。

分工：
  * 图状态里用 LangGraph 的 `add_messages` 累积 **langchain 消息对象**
    （HumanMessage / AIMessage / ToolMessage / SystemMessage），检查点天然持久化；
  * 模型接入层（`agent/llm/`）只认 **OpenAI 原始 dict**（含 tool_calls / tool role）。

因此需要在两者之间做一次转换，且要保住旧循环的**双协议**差异：
  * 原生 tool_calls 协议：assistant 带 tool_calls，工具结果用 role=tool 回灌；
  * 严格 JSON 协议：模型只输出 `{"action": ...}`，工具结果用 role=user 回灌
    （兼容不认识 tool role 的模型）。
协议标记存在 AIMessage.additional_kwargs["protocol"] 里，随检查点持久化。
"""

import json

from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage,
                                     ToolMessage)

PROTOCOL_NATIVE = "native"
PROTOCOL_STRICT = "strict_json"


def _text(content):
    """把消息内容压成字符串（本项目只用文本；异常形态退化成 JSON）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def to_openai(messages):
    """langchain 消息列表 → 模型接入层要的 OpenAI dict 列表。"""
    out = []
    last_protocol = PROTOCOL_NATIVE
    for message in messages:
        if isinstance(message, SystemMessage):
            out.append({"role": "system", "content": _text(message.content)})
        elif isinstance(message, HumanMessage):
            out.append({"role": "user", "content": _text(message.content)})
        elif isinstance(message, AIMessage):
            last_protocol = (message.additional_kwargs or {}).get(
                "protocol", PROTOCOL_NATIVE)
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                if last_protocol == PROTOCOL_STRICT:
                    call = tool_calls[0]
                    out.append({"role": "assistant", "content": json.dumps(
                        {"action": call.get("name"),
                         "args": call.get("args") or {}}, ensure_ascii=False)})
                else:
                    msg = {
                        "role": "assistant",
                        "content": _text(message.content) or None,
                        "tool_calls": [_openai_tool_call(c) for c in tool_calls],
                    }
                    # 思考型模型：带工具调用的助手消息必须把 reasoning_content 一起回传
                    reasoning = (message.additional_kwargs or {}).get("reasoning_content")
                    if reasoning:
                        msg["reasoning_content"] = reasoning
                    out.append(msg)
            else:
                out.append({"role": "assistant", "content": _text(message.content)})
        elif isinstance(message, ToolMessage):
            body = _text(message.content)
            if last_protocol == PROTOCOL_STRICT:
                out.append({"role": "user",
                            "content": "工具 %s 返回：%s" % (message.name or "", body)})
            else:
                out.append({"role": "tool",
                            "tool_call_id": message.tool_call_id or "",
                            "content": body})
        else:                                  # 未知类型：退回 user 文本
            out.append({"role": "user", "content": _text(getattr(message, "content", ""))})
    return out


def _openai_tool_call(call):
    """把一个 langchain tool_call 转成 OpenAI 形态。"""
    return {"id": call.get("id") or "",
            "type": "function",
            "function": {"name": call.get("name") or "",
                         "arguments": json.dumps(call.get("args") or {},
                                                 ensure_ascii=False)}}


def assistant_from_calls(text, tool_calls, protocol=PROTOCOL_NATIVE, reasoning=""):
    """由「文本 + 模型接入层的 ToolCall 事件」造一条 AIMessage。

    reasoning：思考型模型的推理内容（opencode-go 上的 deepseek-v4.1-flash 在
    带工具调用的往返里**要求回传**）。存进 additional_kwargs，随检查点持久化，
    回灌时由 to_openai 带回。
    """
    if not tool_calls:
        return AIMessage(content=text or "")
    calls = []
    for index, call in enumerate(tool_calls):
        calls.append({"name": call.name,
                      "args": call.arguments if isinstance(call.arguments, dict) else {},
                      "id": call.call_id or ("call_%d" % index),
                      "type": "tool_call"})
    extra = {"protocol": protocol}
    if reasoning:
        extra["reasoning_content"] = reasoning
    return AIMessage(content=text or "", tool_calls=calls,
                     additional_kwargs=extra)


def assistant_from_strict_action(name, args):
    """由严格 JSON 协议的一个动作造一条「带工具调用」的 AIMessage。"""
    return AIMessage(
        content=json.dumps({"action": name, "args": args or {}}, ensure_ascii=False),
        tool_calls=[{"name": name, "args": args or {}, "id": "call_0",
                     "type": "tool_call"}],
        additional_kwargs={"protocol": PROTOCOL_STRICT})


def tool_message(content, tool_call_id="", name=""):
    return ToolMessage(content=content, tool_call_id=tool_call_id or "", name=name or "")
