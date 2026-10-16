# -*- coding: utf-8 -*-
"""阶段 5（ag-offline）· T5.1 / T5.2：离线直调通道（工具名 + 可选参数，不经模型）。

对齐 docs/agent/Agent_重构计划.md 阶段 5 与接口文档 §3.3「发消息（模式 offline）」：

T5.1 离线直调通道
  * 输入是**工具名 + 可选参数**（前端据工具表生成表单，用户可只填一部分）；
  * 校验工具名是否在工具表里；用**工具表里的默认值**补齐未填参数；做参数合法性
    校验（类型 / 范围 / 必填 / 枚举）；**不经模型**直接把参数交给工具执行；
  * 输出与 LLM 模式**同一套信封 / 事件形状** —— 回合结果字段、`message_delta` /
    `message_done`、`result_payload`、`node_added`、`error`、长任务 `task_status`
    全部复用阶段 3/4 的既有出口，前端无需两套渲染；
  * 失败如实报错（结构化错误码 `UNKNOWN_TOOL` / `USAGE` / `POLICY_REJECTED`），
    不编造、不静默回退。

T5.2 与 LLM 模式共用
  * 同一张工具表：参数形状、默认值、枚举、范围一律取自 `tool_specs` / `stream.tool_table`；
  * 同一个执行后端：工具调用走 `Runtime._wrap_backend`（内含 `tool_exec` 的
    `LocalToolBackend` 与 `LongTaskBackend`），与 LLM 主控循环**完全同路**；
  * 长任务：复用阶段 4 的 `LongTaskBackend`——直调长耗时工具同样登记长任务、写进
    对话上下文，并经**同一条对话流**推 `task_status`。

本模块只提供函数（`run_offline` / `validate_offline_call`），由 `Runtime.run_offline`
调用；不改动阶段 3/4 的数据模型与既有行为。
"""

import json
import time

from .. import explain, protocol, tool_policy, tool_specs
from . import long_task
from .graph import envelope_of, make_step_note
from .store import summarize
from .stream import (ERROR, MESSAGE_DELTA, MESSAGE_DONE, NODE_ADDED,
                     RESULT_PAYLOAD, tool_table)

# ---------------------------------------------------------------------------
# 结构化错误码（接口文档 §3.10 错误信封；只做加性扩展）
# ---------------------------------------------------------------------------

#: 工具名不在工具表里
UNKNOWN_TOOL = "UNKNOWN_TOOL"
#: 参数非法（类型 / 范围 / 必填 / 枚举 / 未知参数）——与 driver 退出码 2 同码
USAGE = "USAGE"
#: 策略层拒绝（与 LLM 循环同一套策略表）
POLICY_REJECTED = "POLICY_REJECTED"

#: 工具 → 中文解释出口类别（`explain.EXPLAINERS` 的键）。
#: 未登记的工具走通用成功说明（只用信封里真实存在的字段，不编造）。
TOOL_EXPLAIN_KIND = {
    "start_cleaning_task": "status",
    "start_clean_task": "status",
    "score_task": "status",
    "get_task_status": "status",
    "get_task_result": "result",
    "quick_clean_demo": "result",
    "get_samples": "samples",
    "list_schemes": "schemes",
}


# ---------------------------------------------------------------------------
# 参数校验与默认值补齐（T5.1）——纯函数，不执行任何工具
# ---------------------------------------------------------------------------

def tool_defaults(tool_name):
    """从**工具表**取该工具各参数的默认值（离线「补默认值」的唯一来源）。

    `stream.tool_table()` 是接口文档 §3.7 形态的唯一权威工具表，其 `default`
    字段来自工具实现的签名默认值 —— 与 LLM 模式看到的显式默认值同源。
    """
    for row in tool_table():
        if row["name"] == tool_name:
            return {p["name"]: p.get("default") for p in row["parameters"]}
    return None


def _reject(code, message, tool_name=None):
    """造一个结构化拒绝结果（纯 dict，不落节点、不执行）。"""
    return {"ok": False, "tool": tool_name, "arguments": {},
            "defaults_filled": {}, "error": {"code": code, "message": message}}


def _check_value(key, value, prop):
    """单参数合法性校验；合法返回 None，非法返回中文原因。"""
    ptype = prop.get("type", "string")
    if ptype == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return "参数 %s 应为整数，得到 %r" % (key, value)
    elif ptype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "参数 %s 应为数字，得到 %r" % (key, value)
    elif ptype == "boolean":
        if not isinstance(value, bool):
            return "参数 %s 应为布尔值，得到 %r" % (key, value)
    else:                                   # string（含未声明 type 的缺省）
        if not isinstance(value, str):
            return "参数 %s 应为字符串，得到 %r" % (key, value)
    enum = prop.get("enum")
    if enum is not None and value not in enum:
        return "参数 %s 取值非法：%r（可选：%s）" % (
            key, value, "、".join(str(item) for item in enum))
    if ptype in ("integer", "number"):
        if prop.get("minimum") is not None and value < prop["minimum"]:
            return "参数 %s 小于下限 %s：%r" % (key, prop["minimum"], value)
        if prop.get("maximum") is not None and value > prop["maximum"]:
            return "参数 %s 超过上限 %s：%r" % (key, prop["maximum"], value)
    return None


def validate_offline_call(tool_name, params=None):
    """校验一次离线直调请求：工具名存在 → 填默认值 → 类型/范围/必填校验。

    返回纯 dict（**不执行任何工具**）：
        成功：{"ok": True,  "tool": name, "arguments": {...},
               "defaults_filled": {...}, "error": None}
        失败：{"ok": False, "tool": name, "arguments": {},
               "defaults_filled": {}, "error": {"code", "message"}}
    """
    spec = tool_specs.schema_for(tool_name)
    if spec is None:
        names = "、".join(tool_specs.tool_names())
        return _reject(UNKNOWN_TOOL,
                       "未注册的工具名：%r（可用工具：%s）" % (tool_name, names),
                       tool_name=tool_name)
    params_obj = spec.get("parameters") or {}
    props = params_obj.get("properties") or {}
    required = list(params_obj.get("required") or [])
    provided = dict(params or {})
    unknown = [key for key in provided if key not in props]
    if unknown:
        return _reject(USAGE, "未知参数：%s（该工具只接受：%s）" % (
            "、".join(unknown), "、".join(props) or "无"), tool_name=tool_name)
    defaults = tool_defaults(tool_name) or {}
    arguments = {}
    defaults_filled = {}
    for key, prop in props.items():
        value = provided.get(key)
        if value is None:
            if key in required:
                return _reject(USAGE, "缺少必填参数：%s" % key,
                               tool_name=tool_name)
            if defaults.get(key) is not None:      # 用工具表默认值补齐
                value = defaults[key]
                defaults_filled[key] = value
        if value is None:                          # 表里默认也是 None：交给工具自决
            continue
        problem = _check_value(key, value, prop)
        if problem:
            return _reject(USAGE, problem, tool_name=tool_name)
        arguments[key] = value
    return {"ok": True, "tool": tool_name, "arguments": arguments,
            "defaults_filled": defaults_filled, "error": None}


# ---------------------------------------------------------------------------
# 结果解释（无模型，用确定性解释出口；只用信封里真实存在的字段）
# ---------------------------------------------------------------------------

def explain_envelope(tool_name, envelope):
    """把工具返回信封翻译成人话（离线模式没有模型口述，用确定性解释代替）。"""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return explain.explain_error(envelope)
    kind = TOOL_EXPLAIN_KIND.get(tool_name)
    if kind and kind in explain.EXPLAINERS:
        return explain.EXPLAINERS[kind](envelope)
    return _generic_explain(tool_name, envelope)


def _generic_explain(tool_name, envelope):
    """通用成功说明：列出信封里真实存在的关键字段，不臆造、不补数。"""
    lines = ["工具 %s 执行成功。" % tool_name]
    for key in ("task_id", "status", "message", "note", "data_version",
                "total", "total_available", "count", "ok_count"):
        if envelope.get(key) is not None:
            lines.append("%s：%s" % (key, envelope[key]))
    return "\n".join(lines)


def result_payload(conversation_id, tool_name, arguments, envelope):
    """接口文档 §3.9「结构化结果」：结果板块数据（与长任务同一映射）。"""
    name, summary = long_task.describe_task(tool_name, arguments)
    if envelope.get("long_task"):
        status = long_task.RUNNING
    elif envelope.get("ok"):
        status = long_task.SUCCEEDED
    else:
        status = long_task.FAILED
    record = {
        "agent_task_id": envelope.get("agent_task_id"),
        "hadoop_task_id": envelope.get("task_id"),
        "conversation_id": conversation_id, "tool": tool_name,
        "args": arguments, "name": name, "summary": summary,
        "status": status, "stage": long_task.STATUS_CN.get(status, status),
        "progress_percent": 0,
        "estimated_seconds": long_task.estimated_seconds_for(tool_name),
        "updated_at": time.time(),
    }
    return long_task.result_payload(record, envelope)


# ---------------------------------------------------------------------------
# 直调主流程（T5.1 / T5.2）
# ---------------------------------------------------------------------------

def _node_payload(node):
    """node_added 事件字段（接口文档 §3.6：编号、父节点、角色、时间）。"""
    return {"node_id": node["id"], "parent_id": node["parent_id"],
            "role": node["role"], "created_at": node["created_at"]}


def _request_text(tool_name, arguments):
    return "离线直调工具 %s\n参数：%s" % (
        tool_name, json.dumps(arguments, ensure_ascii=False, sort_keys=True))


def _facts_or(runtime, conversation_id, facts):
    if facts is not None:
        return facts
    try:
        return runtime._facts(conversation_id)
    except Exception:
        return {}


def _error_result(runtime, conversation_id, error, publish=None):
    """开跑前的失败（对话不存在 / 校验不过 / 策略拒绝）：不落节点，如实报错。"""
    message = (error or {}).get("message") or "离线直调失败"
    if publish is not None:
        publish(ERROR, error=error)
    return {"ok": False, "engine": "offline", "mode": "offline",
            "conversation_id": conversation_id, "steps": [],
            "final": None, "reply": message, "error": error,
            "llm": {"used": False, "steps": [], "note": message}}


def _finish_exec_error(runtime, conversation_id, assistant_node, envelope,
                       steps, publish):
    """工具本身执行失败（ok=false）：占位节点回填错误说明，并推 error 事件。"""
    error = (envelope or {}).get("error") or {
        "code": "TASK_FAILED", "message": "工具执行失败"}
    message = error.get("message") or "工具执行失败"
    assistant_node = runtime.store.update_node(
        assistant_node["id"], summary=summarize(message), content=message)
    runtime.store.update_conversation(
        conversation_id, current_node_id=assistant_node["id"])
    publish(ERROR, error=error, node_id=assistant_node["id"])
    result = {"ok": False, "engine": "offline", "mode": "offline",
              "conversation_id": conversation_id,
              "user_node_id": assistant_node["parent_id"],
              "node_id": assistant_node["id"],
              "final": None, "reply": message, "steps": steps,
              "task_id": None, "task_started": False, "error": error,
              "llm": {"used": False, "steps": steps, "note": message}}
    return runtime._with_long_tasks(conversation_id, result)


def _finish_ok(runtime, conversation_id, user_node, assistant_node, tool_name,
               arguments, defaults_filled, envelope, steps, info_steps,
               reply, publish):
    """成功回合：回填助手节点、流式推解释、推结构化结果，返回统一信封。"""
    task_id, started = protocol.task_info({"steps": info_steps})
    assistant_node = runtime.store.update_node(
        assistant_node["id"], summary=summarize(reply), content=reply)
    patch = {}
    if started and task_id:
        patch["task_id"] = task_id
    if patch:
        runtime.store.update_context(conversation_id, patch)
    runtime.store.update_conversation(
        conversation_id, current_node_id=assistant_node["id"])
    publish(MESSAGE_DONE, node_id=assistant_node["id"], text=reply)
    publish(RESULT_PAYLOAD,
            **result_payload(conversation_id, tool_name, arguments, envelope))
    result = {"ok": True, "engine": "offline", "mode": "offline",
              "conversation_id": conversation_id,
              "user_node_id": user_node["id"], "node_id": assistant_node["id"],
              "final": reply, "reply": reply, "steps": steps,
              "task_id": task_id, "task_started": bool(started), "error": None,
              "offline": {"tool": tool_name, "arguments": arguments,
                          "defaults_filled": defaults_filled},
              "llm": {"used": False, "steps": steps,
                      "note": "离线直调：未经模型，直接执行 %s" % tool_name}}
    return runtime._with_long_tasks(conversation_id, result)


def run_offline(runtime, conversation_id, tool_name, params=None,
                tool_backend=None, facts=None, enforce_policy=True,
                on_event=None):
    """离线直调一轮：工具名 + 可选参数 → 校验 → 补默认 → **不经模型**直接执行。

    与 `Runtime.run_turn` 同形：返回同一个回合信封、把同一套事件推进该对话的流。
    区别只在「没有模型」：参数来自请求而非模型规划，解释来自确定性解释出口。
    """
    publish = runtime._publisher(conversation_id, on_event)
    conv = runtime.store.get_conversation(conversation_id)
    if conv is None:
        return _error_result(runtime, conversation_id, {
            "code": "CONVERSATION_NOT_FOUND",
            "message": "对话不存在：%s" % conversation_id}, publish=publish)

    verdict = validate_offline_call(tool_name, params)
    if not verdict["ok"]:
        return _error_result(runtime, conversation_id, verdict["error"],
                             publish=publish)

    if enforce_policy:                          # 与 LLM 循环共用同一张策略表
        active_facts = _facts_or(runtime, conversation_id, facts)
        allowed, why = tool_policy.check_call(tool_name, active_facts or {})
        if not allowed:
            return _error_result(runtime, conversation_id, {
                "code": POLICY_REJECTED, "message": why}, publish=publish)

    arguments = verdict["arguments"]
    # 分支：本回合落在对话的**当前所在分支**上（父节点 = 当前所在节点）。旧后文不受影响。
    thread_id = runtime._thread_for(conv)
    user_node = runtime.store.add_node(
        conversation_id, conv.get("current_node_id"), role="user",
        summary="离线直调 %s" % tool_name,
        content=_request_text(tool_name, arguments), thread_id=thread_id)
    publish(NODE_ADDED, **_node_payload(user_node))
    assistant_node = runtime.store.add_node(
        conversation_id, user_node["id"], role="assistant", summary="",
        content="", thread_id=thread_id)
    publish(NODE_ADDED, **_node_payload(assistant_node))

    # 同一个执行后端：与 LLM 模式同一入口（内含本地后端 + 长任务装饰器）
    backend = runtime._wrap_backend(tool_backend or runtime._tool_backend,
                                    conversation_id)
    ok, envelope = backend.execute(tool_name, arguments)
    note = make_step_note(tool_name, arguments, ok, envelope, runtime.artifacts)
    steps = [note]
    info_steps = [{"tool": note.get("tool"), "ok": note.get("ok"),
                   "envelope": envelope_of(note, runtime.artifacts)}]

    if not ok:
        return _finish_exec_error(runtime, conversation_id, assistant_node,
                                  envelope, steps, publish)

    reply = explain_envelope(tool_name, envelope)
    for piece in explain.chunk_text(reply):     # 与 message_delta 同形的确定性分块
        publish(MESSAGE_DELTA, text=piece, node_id=assistant_node["id"])
    return _finish_ok(runtime, conversation_id, user_node, assistant_node,
                      tool_name, arguments, verdict["defaults_filled"],
                      envelope, steps, info_steps, reply, publish)
