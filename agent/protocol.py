# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.4：双协议解析 + 「是否发起任务」判定（独立件）。

从旧 LLM 循环（llm_loop.py）抽出，循环层与各阶段测试都改为引用本模块，
旧循环保留同名薄封装以不破坏现有调用方。

双协议形态（阶段 0 拍板 #1）：
  * 原生 tool_calls —— message.tool_calls 出现即走（工具结果用 role=tool 回灌）；
  * 严格 JSON      —— 模型每轮只输出 {"action": "<工具名>", "args": {...}}
                      或 {"final": "<回答>"}（兼容不认识 tool role 的模型）。

任务判定：哪些步骤算「这一步发起了一个任务」。写任务与查询任务都可能带
task_id，但语义不同 —— 只有真正发起任务的工具成功步才算 started=True。
"""

import json

#: 严格 JSON 协议的动作键（模型只允许输出这两个之一）
ACTION_KEY = "action"
FINAL_KEY = "final"

#: 会**发起**任务的工具（成功步 → task_started=True）。查询类工具即便带 task_id
#: 也只是「追问已存在的任务」，不算发起。
TASK_START_TOOLS = ("start_cleaning_task",)


def parse_model_response(content, message):
    """双形态解析：返回 ("tool", name, args) / ("final", text, None) / None（非法）。

    原生 tool_calls 优先；无 tool_calls 时按严格 JSON 读 content。
    """
    tool_calls = (message or {}).get("tool_calls") or []
    if tool_calls:
        call = tool_calls[0] or {}
        fn = (call.get("function") or {})
        name = fn.get("name") or ""
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        if name:
            return ("tool", name, args if isinstance(args, dict) else {})
        return None
    if not content or not content.strip():
        return None
    try:
        obj = json.loads(content)
    except ValueError:
        return None
    if isinstance(obj, dict):
        if FINAL_KEY in obj:
            return ("final", str(obj[FINAL_KEY]), None)
        if ACTION_KEY in obj and isinstance(obj.get("args"), dict):
            return ("tool", str(obj[ACTION_KEY]), obj["args"])
    return None


def step_task_id(step):
    """取一个步骤信封里的 task_id（没有返回 None）。"""
    env = (step or {}).get("envelope") or {}
    return env.get("task_id") if isinstance(env, dict) else None


def step_started_task(step):
    """这一步是否**发起**了任务（工具成功、且工具属于发起类）。"""
    if not isinstance(step, dict) or not step.get("ok"):
        return False
    return step.get("tool") in TASK_START_TOOLS


def task_info(result):
    """从循环结果里提取 (task_id, started)（http_api 落运行参数快照用）。

    规则：start_cleaning_task 成功步 → (task_id, started=True)；
    否则取最后一个带 task_id 的工具返回（status/result/samples/report 的
    追问场景），started=False。
    """
    steps = result.get("steps") if isinstance(result, dict) else result
    task_id, started = None, False
    for step in steps or []:
        tid = step_task_id(step)
        if step_started_task(step):
            task_id, started = tid or task_id, True
        elif tid:
            task_id = tid
    return task_id, started
