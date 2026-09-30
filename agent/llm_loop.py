# -*- coding: utf-8 -*-
"""第 5 节 Agent v2：LLM 主控循环（阶段 3）—— 历史装配 / 工具调用 / 策略 /
执行 / 回灌 / 限步限时 / 回退规则路径。

架构（docs/agent/llm-agent-plan.md §2）：
  LLM 循环：对话历史 + 工具表 → 模型 → 工具调用 → 策略校验 → 执行 →
            结果回灌 → 循环 → 基于结果的最终回答
  模型只做两件事：选工具并填参数、拿到结果后组织回答。数字只来自工具结果。

协议双形态（阶段 0 拍板 #1，gateway-probe.md V1）：
  * 原生 tool_calls —— message.tool_calls 出现即走（工具结果用 role=tool 回灌）；
  * 严格 JSON —— 模型每轮只输出 {"action": "<工具名>", "args": {...}} 或
    {"final": "<回答>"}（结果用 role=user 回灌，兼容不认识 tool role 的模型）。
  两种形态共用循环/策略/工具表，只差解析与回灌装配。

护栏（阶段 3 内建，阶段 5 再上输出数字校验器）：
  * check_call 前置策略：被拒 → 拒绝原因回灌给模型要求重规划（最多让重试
    MAX_PLAN_RETRIES 次，超限如实说明并终止）；
  * MAX_STEPS / MAX_LOOP_SECONDS / HISTORY_WINDOW（拍板 #4/#5）；
  * 工具结果回灌截断 MAX_TOOL_RESULT_CHARS，防上下文爆炸；
  * 任何模型故障（不可用/非法/超时/超步）→ fallback 回调（规则路径），
    如实标注；没有 fallback 时返回带 fallback 标志的结果。
"""

import json
import time

from . import llm_client
from . import tool_policy
from . import tool_specs

#: 拍板 #4/#5：循环上限与历史窗口（集中可调）
MAX_STEPS = 6
MAX_LOOP_SECONDS = 90
HISTORY_WINDOW = 8
MAX_LLM_PLAN_RETRIES = 2
MAX_TOOL_RESULT_CHARS = 2000

#: 严格 JSON 协议的动作键（模型只允许输出这两个之一）
ACTION_KEY = "action"
FINAL_KEY = "final"

#: 工具调用的系统提示词（原生与严格 JSON 共用的骨架）
TOOL_SYSTEM = """你是数据治理 Agent 的编排器。把用户需求拆成工具调用，最后基于
工具返回的真实结果组织回答。

硬性约束：
1) 只能调用工具表里的工具；参数必须符合工具的 JSON Schema，句子里没有的信息
   不要补全、不要推测。
2) 你**不生成任何业务数字**：分数、行数、隔离数一律来自工具返回结果。
3) 没有合适工具时，用一句话如实说明「这个超出我的工具能力」，不要编造。
4) 工具被拒绝时（原因会回灌给你），重新选一个可行的方案或向用户解释，
   不要重复提交被拒的调用。
"""


def _build_tool_specs():
    """把 TOOL_SPECS 转成 OpenAI 原生 tools 数组（阶段 0 拍板 #1 的原生形态）。"""
    out = []
    for name in tool_specs.tool_names():
        spec = tool_specs.schema_for(name)
        out.append({"type": "function",
                    "function": {"name": spec["name"],
                                 "description": spec["description"],
                                 "parameters": spec["parameters"]}})
    return out


def _execute_tool(name, args):
    """执行一个工具调用，返回 (ok, 结果文本/信封)。只传 schema 声明过的参数。"""
    spec = tool_specs.schema_for(name)
    if spec is None:
        return False, {"ok": False, "error": {"code": "USAGE",
                                              "message": "未注册工具：%s" % name}}
    fn = getattr(tool_specs.tools_module(), tool_specs.SPEC_TO_FUNCTION[name])
    allowed = set(spec["parameters"]["properties"])
    clean = {k: v for k, v in (args or {}).items()
             if k in allowed and v is not None}
    try:
        envelope = fn(**clean)
    except TypeError as exc:
        return False, {"ok": False, "error": {"code": "USAGE",
                                              "message": "参数不合法：%s" % exc}}
    return bool(envelope and envelope.get("ok")), (envelope or {})


def _clip(text, limit=MAX_TOOL_RESULT_CHARS):
    text = json.dumps(text, ensure_ascii=False) if not isinstance(text, str) else text
    return text[:limit] + ("…（截断）" if len(text) > limit else "")


def _parse_model_response(content, message):
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


def _chat_impl(cfg, payload):
    """默认模型调用：OpenAI 兼容 chat/completions（原生 tools 形态）。"""
    return llm_client.chat_messages(cfg, payload["messages"],
                                    tools=payload.get("tools"))


def run_loop(question, cfg, context=None, fallback=None, chat=None,
             facts=None):
    """LLM 主控循环。返回信封：

        {"ok": True, "final": str, "steps": [...], "engine": "llm",
         "llm": {"used": True, "note": "..."}}
        {"ok": False, "final": ..., "steps": [...], "fallback": True,
         "engine": "rules", ...}   ← 模型故障/超限，交给规则路径
    参数：
        chat:   可注入的调用函数（单测用）；默认走 llm_client.chat，
                会话消息由本循环直接拼（无状态用法，阶段 0 拍板 #2）。
        facts:  策略校验事实 {"running_task_id", "raw_readable", "raw_dir"}；
                缺省由循环收集（list_tasks 判运行中）。
        fallback: 无模型/模型全败时调用（默认 None → 返回 fallback 标志）。
    """
    started = time.time()
    steps = []
    messages = [{"role": "user", "content": question}]
    use_native = None                  # 第一次成功响应后锁定协议形态
    plan_retries = 0

    def call_model():
        tools = _build_tool_specs()
        payload = {"messages": messages, "stream": False}
        if use_native is not False:
            payload["tools"] = tools
        return (chat or _chat_impl)(cfg, payload)

    while True:
        if len(steps) >= MAX_STEPS or time.time() - started > MAX_LOOP_SECONDS:
            break
        got, err = call_model()
        if got is None:
            return _fallback(question, fallback, steps, err or "模型调用失败")
        message = (got.get("choices") or [{}])[0].get("message") or {}
        content = message.get("content")
        parsed = _parse_model_response(content, message)
        if parsed is None:
            return _fallback(question, fallback, steps,
                             "模型输出既不是 tool_calls 也不是合法 JSON，已回退")
        kind, a, b = parsed
        if kind == "final":
            return {"ok": True, "final": a, "steps": steps,
                    "engine": "llm",
                    "llm": {"used": True, "note": "由大模型编排 %d 步工具调用"
                                                 % len(steps)}}
        name, args = a, b
        # ---- 策略前置校验 ----
        ok_why = tool_policy.check_call(name, facts or {})
        if not ok_why[0]:
            plan_retries += 1
            if plan_retries > MAX_LLM_PLAN_RETRIES:
                return _fallback(question, fallback, steps,
                                 "模型连续 %d 次提交被策略拒绝（%s）"
                                 % (MAX_LLM_PLAN_RETRIES + 1, ok_why[1]))
            messages.append({"role": "assistant",
                             "content": json.dumps(
                                 {"action": name, "args": args},
                                 ensure_ascii=False)})
            messages.append({"role": "user",
                             "content": "策略拒绝：%s。请换方案或向用户解释。"
                                        % ok_why[1]})
            continue
        # ---- 执行工具并回灌 ----
        ok, envelope = _execute_tool(name, args)
        steps.append({"tool": name, "args": args, "ok": ok,
                      "summary": _clip(envelope, 400)})
        result_text = _clip(envelope)
        if use_native is not False and (message.get("tool_calls")):
            # 原生协议：assistant 消息须带原 tool_calls，结果用 role=tool
            messages.append({"role": "assistant", "content": content,
                             "tool_calls": message["tool_calls"]})
            messages.append({"role": "tool",
                             "tool_call_id": message["tool_calls"][0].get("id", ""),
                             "content": result_text})
            use_native = True
        else:
            messages.append({"role": "assistant",
                             "content": json.dumps(
                                 {"action": name, "args": args},
                                 ensure_ascii=False)})
            messages.append({"role": "user",
                             "content": "工具 %s 返回：%s" % (name, result_text)})
            use_native = False
        # 历史窗口裁剪（只压 user 侧旧轮，保留最近 HISTORY_WINDOW 轮）
        if len(messages) > (HISTORY_WINDOW + 1) * 2:
            messages = messages[-(HISTORY_WINDOW * 2):]

    return _fallback(question, fallback, steps,
                     "达到循环上限（步数 %d / 时长 %d 秒），已回退规则路径"
                     % (MAX_STEPS, MAX_LOOP_SECONDS))


def _fallback(question, fallback, steps, reason):
    base = {"ok": False, "steps": steps, "fallback": True, "reason": reason}
    if callable(fallback):
        env = fallback(question)
        if isinstance(env, dict):
            env.update(base)
            return env
    base.update({"engine": "rules",
                 "llm": {"used": False, "note": "已回退规则路径：%s" % reason}})
    return base