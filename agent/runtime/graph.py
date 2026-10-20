# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· T3.3：把工具调用循环落到 LangGraph 的 StateGraph 上。

图结构（阶段 0 结论文档 §4「能做什么」的落点）：

    START → model ──(无工具调用)──────────────────────────────→ END
              ▲   └─(有工具调用)→ tools ─┐
              └──────────────────────────┘        （回灌后回到模型）

  * model 节点：经 `agent/llm/` 的抽象**流式**取回事件（TextDelta / ToolCall /
    StreamEnd），把「文本 + 工具调用」组装成一条 AIMessage；最终回答过数字护栏。
  * tools 节点：每个工具调用**先** `tool_policy.check_call`，通过才执行（本地
    执行后端，阶段 8 可换 MCP）；结果**外置**（大信封只留 ref）后回灌。
  * 双协议：原生 tool_calls 与严格 JSON（{"action"} / {"final"}）都在此支持。

护栏与上限（与旧循环同约定）：
  * 步数 MAX_STEPS、时长 MAX_LOOP_SECONDS、连续被拒 MAX_PLAN_RETRIES；
  * 数字护栏（`number_guard`）是**软提示**：本回合没有工具调用就跳过；有无法
    溯源的数字只给出提示与原文位置区间（`guard_warning` / `guard_spans`），
    不重写、不拦截；
  * 模型不可用 / 出错 → 直接在状态里置 error（**静默回退不做**）。

检查点：本图必须 `compile(checkpointer=...)` 才具备持久化（阶段 0 §5.2）。状态里
只放可序列化的小对象；工具大结果走 ArtifactStore 外置。
"""

import json
import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from .. import number_guard, protocol, tool_policy, tool_specs
from ..llm import ModelError
from . import messages as bridge
from .prompts import TOOL_SYSTEM

#: 循环上限与常量（集中可调；与旧 llm_loop.py 同约定）
MAX_STEPS = 6
#: 单回合总时长上限。推理型模型 + 多步工具调用在慢网关上常见 1~3 分钟；
#: 90 秒会把正常的多步任务误判成"循环上限"。放宽到 5 分钟。
MAX_LOOP_SECONDS = 300
MAX_PLAN_RETRIES = 2
MAX_FINAL_RETRIES = 1
MAX_TOOL_RESULT_CHARS = 2000
#: 工具信封超过此长度就外置存放，状态里只留 ref（阶段 0 §5.2 约束）
ARTIFACT_THRESHOLD = 4000


class RuntimeState(TypedDict, total=False):
    """图状态：messages 用 add_messages 累积（检查点持久化）；其余按键按回合重置。"""

    messages: Annotated[list, add_messages]
    steps: list               # 本回合工具步骤（小信封内联，大信封只留 artifact ref）
    calls: int                # 本回合模型→工具轮数
    plan_retries: int         # 连续被策略拒绝的计数
    final_retries: int        # 历史字段（软护栏不再重写）
    deadline: float           # 本回合绝对时长上限
    question: str             # 本回合用户原话（数字护栏的一处合法来源）
    conversation_id: str
    limit_reached: bool
    blocked: str              # 历史字段（数字护栏不再走拦截）
    final: str
    guard_warning: str        # 未溯源数字的软提示（无提示为 None/缺省）
    guard_spans: list         # [{"number","start","end"}]，原文中的位置区间
    error: dict


class NullProvider(object):
    """只用于「读检查点消息」等不调用模型的场合。"""

    def stream_chat(self, messages, tools=None, options=None):
        raise ModelError("MODEL_ERROR", "占位 provider 不应被调用")


def build_tool_schemas():
    """把 TOOL_SPECS 转成 OpenAI 原生 tools 数组（供模型 function calling）。"""
    out = []
    for name in tool_specs.tool_names():
        spec = tool_specs.schema_for(name)
        out.append({"type": "function",
                    "function": {"name": spec["name"],
                                 "description": spec["description"],
                                 "parameters": spec["parameters"]}})
    return out


def _clip(value, limit=MAX_TOOL_RESULT_CHARS):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit] + ("…（截断）" if len(text) > limit else "")


def _collect_events(events, on_delta=None):
    """把模型事件流聚成 (文本, 推理内容, [ToolCall], finish_reason)。

    阶段 3b：边收边把文本增量经 `on_delta(text)` 交给上层 —— 这就是
    `message_delta` 的**逐段**来源（不改变聚合结果，纯旁路回调）。
    阶段 4+（真机联调）：思考型模型的 `reasoning_content` 单独聚合，工具往返时回传。
    """
    text_parts = []
    reasoning_parts = []
    tool_calls = []
    finish = "stop"
    for event in events:
        kind = getattr(event, "kind", None)
        if kind == "text_delta":
            text = event.text or ""
            if text and on_delta is not None:
                on_delta(text)
            text_parts.append(text)
        elif kind == "reasoning_delta":
            reasoning_parts.append(event.text or "")
        elif kind == "tool_call":
            tool_calls.append(event)
        elif kind == "done":
            finish = getattr(event, "finish_reason", "stop") or "stop"
    return "".join(text_parts), "".join(reasoning_parts), tool_calls, finish


def _parse_content(content):
    """解析文本形态的模型输出：返回 ("tool", name, args) / ("final", text) / None。

    严格 JSON 协议优先；不是 JSON 就**整体当作最终回答**（更宽容，不轻易失败）。
    """
    stripped = (content or "").strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except ValueError:
        return ("final", content)
    if isinstance(obj, dict):
        if protocol.FINAL_KEY in obj:
            return ("final", str(obj[protocol.FINAL_KEY]))
        if protocol.ACTION_KEY in obj and isinstance(obj.get("args"), dict):
            return ("tool", str(obj[protocol.ACTION_KEY]), obj["args"])
    return ("final", content)


def envelope_of(note, artifacts):
    """从一个步骤 note 取回完整信封（内联的直接给；外置的从 artifact 读回）。"""
    if not isinstance(note, dict):
        return {}
    if note.get("envelope") is not None:
        return note["envelope"]
    if note.get("artifact"):
        loaded = artifacts.load(note["artifact"])
        if loaded is not None:
            return loaded
    return {}


def _make_step_note(name, args, ok, envelope, artifacts):
    """造一个步骤 note：小信封内联，大信封外置只留 ref。"""
    note = {"tool": name, "ok": bool(ok),
            "summary": _clip(envelope, 400)}
    blob = json.dumps(envelope, ensure_ascii=False)
    if len(blob) > ARTIFACT_THRESHOLD:
        note["artifact"] = artifacts.save(envelope, kind="tool_result")
    else:
        note["envelope"] = envelope
    return note


def make_step_note(name, args, ok, envelope, artifacts):
    """公开的步骤 note 构造（阶段 5 离线直调与图**共用同一套步骤落法**）。

    离线直调不经模型，但结果信封 / 步骤结构要与 LLM 模式完全同形，故复用这里
    （小信封内联、大信封外置只留 ref），避免两套实现漂移。
    """
    return _make_step_note(name, args, ok, envelope, artifacts)


def build_graph(provider, tool_backend, artifacts, checkpointer, facts,
                on_delta=None, options=None):
    """编译一张绑定「本轮依赖」的运行时图（检查点持久化）。

    每次 run_turn 传入不同的 provider / tool_backend / facts，因此按回合编译；
    编译开销很小，检查点连接复用（阶段 0 探针已验证可重复 compile）。

    阶段 3b：`on_delta(text)` 是模型流式文本增量的旁路回调（→ `message_delta`）。
    `options` 是透传给模型请求的可选装载项（如 `reasoning_effort`）；缺省 None 时
    请求装配与既有行为完全一致。
    """
    facts = facts or {}
    schemas = build_tool_schemas()

    def model_node(state):
        # 已有终态（错误 / 上限 / 拦截）时不再调模型
        if state.get("error") or state.get("limit_reached") or state.get("blocked"):
            return {}
        if (state.get("calls", 0) >= MAX_STEPS
                or time.time() > state.get("deadline", time.time() + MAX_LOOP_SECONDS)):
            return {"limit_reached": True}
        openai_messages = bridge.to_openai(
            [SystemMessage(content=TOOL_SYSTEM)] + list(state.get("messages") or []))
        try:
            events = provider.stream_chat(openai_messages, tools=schemas,
                                          options=options)
            text, reasoning, tool_calls, _finish = _collect_events(events, on_delta)
        except ModelError as exc:
            return {"error": exc.to_envelope()["error"]}
        except Exception as exc:                    # 传输/解析异常：如实报错，不回退
            return {"error": {"code": "MODEL_ERROR",
                              "message": "模型调用异常：%s" % exc}}

        if tool_calls:                              # 原生 tool_calls
            ai = bridge.assistant_from_calls(text, tool_calls, bridge.PROTOCOL_NATIVE, reasoning)
            return {"messages": [ai], "calls": state.get("calls", 0) + 1}

        parsed = _parse_content(text)
        if parsed is not None and parsed[0] == "tool":   # 严格 JSON 的 {"action"}
            ai = bridge.assistant_from_strict_action(parsed[1], parsed[2])
            return {"messages": [ai], "calls": state.get("calls", 0) + 1}
        if parsed is None:                          # 空输出：如实报错
            return {"error": {"code": "MODEL_ERROR", "message": "模型未给出有效回答"}}

        final_text = parsed[1]
        result = {"messages": [AIMessage(content=final_text)], "final": final_text}
        guarded = _guard_final(final_text, state)
        if guarded is not None:                     # 软提示：不重写、不拦截
            result.update(guarded)
        return result

    def _guard_final(final_text, state):
        """数字护栏（软）：有无法溯源的数字 → 提示 + 原文区间；无问题返回 None。

        本回合**没有工具调用**（`steps` 为空）时完全跳过 —— 模型直接回答里的
        数字无从对照工具结果，不该被提示。
        """
        if not (state.get("steps") or []):
            return None
        steps_for_guard = [
            {"tool": note.get("tool"),
             "envelope": envelope_of(note, artifacts)}
            for note in (state.get("steps") or [])]
        authorized = number_guard.build_authorized(state.get("question", ""),
                                                   steps_for_guard)
        records = number_guard.find_unauthorized(final_text, authorized)
        if not records:
            return None
        return {
            "guard_warning": ("以下数字未能在工具结果中找到："
                              + number_guard.describe(records)),
            "guard_spans": [{"number": rec["number"],
                             "start": rec["start"], "end": rec["end"]}
                            for rec in records],
        }

    def tools_node(state):
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        notes = list(state.get("steps") or [])
        plan_retries = state.get("plan_retries", 0)
        out_messages = []
        for call in calls:
            name = call.get("name") or ""
            args = call.get("args") or {}
            call_id = call.get("id") or ""
            ok_why = tool_policy.check_call(name, facts)
            if not ok_why[0]:                        # 策略拒绝 → 回灌，要求重规划
                plan_retries += 1
                if plan_retries > MAX_PLAN_RETRIES:
                    out_messages.append(bridge.tool_message(
                        "策略拒绝：%s" % ok_why[1], call_id, name))
                    return {"messages": out_messages, "steps": notes,
                            "plan_retries": plan_retries,
                            "error": {"code": "POLICY_REJECTED",
                                      "message": "模型连续提交被策略拒绝（%s）"
                                                 % ok_why[1]}}
                out_messages.append(bridge.tool_message(
                    "策略拒绝：%s。请换方案或向用户解释。" % ok_why[1], call_id, name))
                continue
            ok, envelope = tool_backend.execute(name, args)
            notes.append(_make_step_note(name, args, ok, envelope, artifacts))
            out_messages.append(bridge.tool_message(
                _clip(envelope), call_id, name))
        return {"messages": out_messages, "steps": notes, "plan_retries": plan_retries}

    def route_after_model(state):
        if state.get("error") or state.get("limit_reached") or state.get("blocked"):
            return END
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and (getattr(last, "tool_calls", None) or []):
            return "tools"
        if isinstance(last, HumanMessage):           # 历史兜底（软护栏不再走这条）
            return "model"
        return END

    graph = StateGraph(RuntimeState)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route_after_model,
                                {"tools": "tools", "model": "model", END: END})
    graph.add_edge("tools", "model")
    return graph.compile(checkpointer=checkpointer)
