# -*- coding: utf-8 -*-
"""阶段 1 模型接入层：OpenAI 兼容 SSE 流式解析与工具调用聚合（T1.1/T1.3 核心）。

把传输层产出的文本片段喂进 SseStreamParser → 得到事件：

  * data: {...,"choices":[{"delta":{"content":...}}]}  → TextDelta
  * data: {...,"delta":{"tool_calls":[...]}}           → 按 index 分片累积，
                                                          完成后聚成 ToolCall
  * data: [DONE] 或 finish_reason 出现                   → 冲刷 ToolCall + StreamEnd
  * data: {"error": {...}}                              → 抛 ModelError(MODEL_ERROR)

关键点：工具调用的 function.arguments 在流里是**字符串分片**，必须按 index
累积后再 json.loads 成 dict —— 这正是 T1.1「工具调用请求」的落点。
"""

import json

from .errors import ModelError, BAD_RESPONSE, MODEL_ERROR
from .events import TextDelta, ReasoningDelta, ToolCall, StreamEnd

DONE_SENTINEL = "[DONE]"


class SseStreamParser(object):
    """把 SSE 文本片段增量喂进 feed()，逐次拿回事件；末尾调 finish() 收尾。"""

    def __init__(self):
        self._buf = ""
        self._tools = {}          # index -> {"id", "name", "args"}
        self._tool_order = []
        self._flushed = False
        self._done = False
        self.finish_reason = None

    @property
    def done(self):
        """已经看到 [DONE] / finish_reason，可以停止读流。"""
        return self._done

    def feed(self, chunk):
        """喂入一个文本片段，返回本次能解析出的事件列表。"""
        events = []
        self._buf += chunk or ""
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            events.extend(self._consume_line(line.rstrip("\r")))
        return events

    def finish(self):
        """流结束：处理残留缓冲，冲刷未发的工具调用，补齐 StreamEnd。"""
        events = []
        if self._buf:
            events.extend(self._consume_line(self._buf.rstrip("\r")))
            self._buf = ""
        events.extend(self._flush_tools())
        if not self._done:
            self._done = True
            events.append(StreamEnd(self.finish_reason or "stop"))
        return events

    # ---- 内部 ----

    def _consume_line(self, line):
        text = (line or "").strip()
        if not text or text.startswith(":"):     # 空行 / keep-alive 注释
            return []
        if not text.startswith("data:"):         # event: / id: 等字段忽略
            return []
        payload = text[5:].strip()
        if payload == DONE_SENTINEL:
            events = self._flush_tools()
            if not self._done:
                self._done = True
                events.append(StreamEnd(self.finish_reason or "stop"))
            return events
        try:
            obj = json.loads(payload)
        except ValueError:
            raise ModelError(BAD_RESPONSE,
                             "流式响应不是合法 JSON 片段：%s" % payload[:120])
        if isinstance(obj, dict) and obj.get("error"):
            err = obj["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            detail = (json.dumps(err, ensure_ascii=False)[:300]
                      if isinstance(err, dict) else "")
            raise ModelError(MODEL_ERROR, "网关返回错误：%s" % (message or "未知"),
                             detail=detail)
        events = []
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                events.append(TextDelta(delta["content"]))
            # 思考型模型：推理内容单独成事件（工具往返时必须回传，见 events.ReasoningDelta）
            if delta.get("reasoning_content"):
                events.append(ReasoningDelta(delta["reasoning_content"]))
            for piece in delta.get("tool_calls") or []:
                self._accumulate_tool(piece)
            reason = choice.get("finish_reason")
            if reason:
                self.finish_reason = reason
                events.extend(self._flush_tools())
        return events

    def _accumulate_tool(self, piece):
        index = piece.get("index")
        if index is None:
            index = len(self._tool_order)
        if index not in self._tools:
            self._tools[index] = {"id": "", "name": "", "args": ""}
            self._tool_order.append(index)
        slot = self._tools[index]
        if piece.get("id"):
            slot["id"] = piece["id"]
        fn = piece.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["args"] += fn["arguments"]

    def _flush_tools(self):
        """把累积的工具调用按顺序聚合成 ToolCall 事件（只冲一次）。"""
        if self._flushed:
            return []
        self._flushed = True
        events = []
        for index in self._tool_order:
            slot = self._tools[index]
            raw = slot["args"]
            arguments = {}
            if raw.strip():
                try:
                    arguments = json.loads(raw)
                except ValueError:
                    arguments = {}
            events.append(ToolCall(
                slot["name"], arguments if isinstance(arguments, dict) else {},
                call_id=slot["id"], raw_arguments=raw))
        return events
