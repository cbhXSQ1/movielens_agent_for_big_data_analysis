#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/gateway_probe.py —— Agent v2 阶段 0：网关、协议与会话验证探测脚本。

用途（docs/agent/llm-agent-plan.md §2.1）：
  V1 协议探测：给 OpenAI 兼容网关发一版带 tools 的请求，判定"原生 tool_calls"还是"严格 JSON"。
  V2 会话验证：无状态多轮延续 + 并发任务会话隔离各一例；附带未知会话头容忍探测。
  V3 插件评估：@deepseek-ai/dsh-llm-deepseek 的静态评估结论（无网络请求）。

凭据纪律（2026-09-30 定）：
  * **不读取、不使用任何本机其它服务的 API key**（icspa 等一律禁用）。
  * 真实网关探测只认显式环境变量：
        GATEWAY_PROBE_BASE=<OpenAI 兼容端点>  GATEWAY_PROBE_KEY=<key>
    或项目既有的 AGENT_LLM_API_BASE / AGENT_LLM_API_KEY（llm_config 同源）。
  * 没有 key 时**不联网**，改跑内建 mock 网关（同一进程内实现原生 tool_calls 与
    严格 JSON 两种协议形态），验证的是"我们的解析层对两种协议都成立"；
    真实网关的最终结论留待 VM 上配好 opencode-go key 后一条命令补跑（--live）。

用法：
  python3 agent/gateway_probe.py                 # 离线：mock 双协议自检
  GATEWAY_PROBE_BASE=... GATEWAY_PROBE_KEY=... python3 agent/gateway_probe.py --live
  python3 agent/gateway_probe.py --json          # 输出 JSON（供文档/CI 引用）
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

MODEL = "deepseek-flash"
TIMEOUT = 60
MAX_BYTES = 256 * 1024


def key_summary(key):
    return ("%s…%s" % (key[:4], key[-4:])) if key else "(无)"


# ===========================================================================
# 内建 mock 网关（离线自检用；实现两种协议形态 + 会话行为）
# ===========================================================================

class MockGateway(object):
    """OpenAI 兼容 mock：行为可控，用来验证解析层的两态兼容。

    - tools_requested=True 且命中 tool_choice=auto 时 → 回原生 tool_calls（V1 形态 A）；
    - 否则原样走内容回答（严格 JSON 形态 B 由调用方自行组织文本）。
    - 无状态：只根据本次请求的 messages 回答；记录请求次数供并发隔离断言。
    """

    def __init__(self):
        self.calls = []

    def chat(self, payload):
        self.calls.append(payload)
        messages = payload.get("messages") or []
        last = (messages[-1] or {}).get("content", "") if messages else ""
        tools = payload.get("tools")
        if tools and payload.get("tool_choice") == "auto" and "加 30" in last:
            return {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "type": "function",
                    "id": "call_mock_1",
                    "function": {"name": "add",
                                 "arguments": json.dumps({"a": 12, "b": 30})},
                }],
            }, "finish_reason": "tool_calls"}]}
        if "幸运数字" in last:
            nums = [m.get("content", "") for m in messages if m.get("role") == "user"]
            if any("7" in n for n in nums):
                return {"choices": [{"message": {"role": "assistant",
                                                 "content": "7"}, "finish_reason": "stop"}]}
            if any("99" in n for n in nums):
                return {"choices": [{"message": {"role": "assistant",
                                                 "content": "99"}, "finish_reason": "stop"}]}
        if "1+1" in last:
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "2"}, "finish_reason": "stop"}]}
        return {"choices": [{"message": {"role": "assistant",
                                         "content": "mock: 已收到"}, "finish_reason": "stop"}]}


# ===========================================================================
# 请求（真实网关路径）
# ===========================================================================

def chat_completions(base, key, payload, extra_headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Authorization": "Bearer " + key}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(base + "/chat/completions", data=data,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return False, "HTTP %s: %s" % (exc.code,
                                       exc.read(300).decode("utf-8", "replace")[:300]), exc.code
    except Exception as exc:
        return False, "传输失败: %s" % exc, 0
    if len(raw) > MAX_BYTES:
        return False, "响应超限", 200
    try:
        return True, json.loads(raw.decode("utf-8", "replace")), 200
    except ValueError:
        return False, "响应非 JSON", 200


QUIET = "--json" in sys.argv


def show(label, verdict, detail):
    if QUIET:
        return
    print("[%s] %s" % (label, verdict))
    print("      %s" % detail)


# ===========================================================================
# V1 协议探测
# ===========================================================================

TOOLS_PAYLOAD = {
    "model": MODEL,
    "messages": [{"role": "user",
                  "content": "请计算 12 加 30 的结果；如果提供了 add 工具，就调用它。"}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "add",
            "description": "两个整数相加",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
        },
    }],
    "tool_choice": "auto",
    "stream": False,
    "max_tokens": 300,
}


def classify_v1(body):
    """把网关响应归成三类：native-tool-calls / explicit-unsupported / tools-ignored。"""
    msg = (((body.get("choices") or [{}])[0] or {}).get("message") or {})
    if msg.get("tool_calls"):
        calls = [c.get("function", {}).get("name") for c in msg["tool_calls"]]
        return "native-tool-calls", calls
    if body.get("error"):
        return "explicit-unsupported", body["error"]
    return "tools-ignored", msg.get("content")


def probe_v1(base, key, live):
    if not QUIET:
        print("== V1 协议探测：带 tools 的 chat/completions（%s）==" % ("真实网关" if live else "内建 mock"))
    if live:
        ok, body, code = chat_completions(base, key, TOOLS_PAYLOAD)
        if not ok:
            show("V1", "网关拒绝或不可达", body)
            return {"verdict": "unreachable", "detail": body, "http_code": code}
        verdict, detail = classify_v1(body)
    else:
        gw = MockGateway()
        body = gw.chat(TOOLS_PAYLOAD)
        verdict, detail = classify_v1(body)
    tip = {"native-tool-calls": "→ 循环走原生 tool_calls 协议",
           "explicit-unsupported": "→ 走严格 JSON 协议",
           "tools-ignored": "→ 走严格 JSON 协议"}[verdict]
    show("V1", verdict, "detail=%r；%s" % (detail, tip))
    return {"verdict": verdict, "detail": detail, "tip": tip}


# ===========================================================================
# V2 会话验证
# ===========================================================================

def _ask(base, key, thread, live, gw=None, extra_headers=None, max_tokens=200):
    payload = {"model": MODEL, "messages": thread, "stream": False,
               "max_tokens": max_tokens}
    if live:
        ok, body, code = chat_completions(base, key, payload, extra_headers)
        if not ok:
            return None, body
        msg = (((body.get("choices") or [{}])[0] or {}).get("message") or {})
        return msg.get("content"), None
    body = gw.chat(payload)
    msg = (((body.get("choices") or [{}])[0] or {}).get("message") or {})
    return msg.get("content"), None


def probe_v2(base, key, live):
    if not QUIET:
        print("== V2 会话验证：多轮延续 + 并发隔离 + 未知会话头（%s）==" % ("真实网关" if live else "内建 mock"))
    gw = MockGateway() if not live else None
    out = {}

    thread_a = [
        {"role": "user", "content": "我的幸运数字是 7，请记住它。"},
        {"role": "assistant", "content": "好的，我已记住你的幸运数字是 7。"},
        {"role": "user", "content": "我的幸运数字是多少？只回答数字。"},
    ]
    content, err = _ask(base, key, thread_a, live, gw)
    ok1 = content is not None and "7" in str(content)
    show("V2.1", "多轮延续（单请求全历史）%s" % ("通过" if ok1 else "异常"),
         "回答=%r" % (content or err))
    out["multi_turn"] = {"ok": ok1, "answer": content or err}

    _c, _e = _ask(base, key, [{"role": "user", "content": "我的幸运数字是 99，请记住它。"}],
                  live, gw)
    ca, ea = _ask(base, key, thread_a + [{"role": "assistant", "content": content or "7"},
                                         {"role": "user",
                                          "content": "我的幸运数字是多少？只回答数字。"}],
                  live, gw)
    cb, eb = _ask(base, key, [
        {"role": "user", "content": "我的幸运数字是 99，请记住它。"},
        {"role": "assistant", "content": _c or "好的，记住 99。"},
        {"role": "user", "content": "我的幸运数字是多少？只回答数字。"},
    ], live, gw)
    ok_a = ca is not None and "7" in str(ca)
    ok_b = cb is not None and "99" in str(cb)
    show("V2.2", "并发会话隔离 %s" % ("通过" if (ok_a and ok_b) else "异常"),
         "A=%r | B=%r" % (ca or ea, cb or eb))
    out["concurrency"] = {"ok": ok_a and ok_b, "a": ca or ea, "b": cb or eb}

    content, err = _ask(base, key,
                        [{"role": "user", "content": "1+1 等于几？只回答数字。"}],
                        live, gw,
                        extra_headers={"x-opencode-session": "probe-isolation-check"},
                        max_tokens=50)
    ok3 = content is not None
    show("V2.3", "未知会话头 %s" % ("被容忍（忽略）" if ok3 else "被拒绝"),
         "回答=%r" % (content or err))
    out["unknown_header"] = {"ok": ok3, "answer": content or err}
    return out


# ===========================================================================
# V3 插件评估（静态，无网络）
# ===========================================================================

def probe_v3():
    if not QUIET:
        print("== V3 插件评估：@deepseek-ai/dsh-llm-deepseek（静态） ==")
    verdict = {
        "name": "@deepseek-ai/dsh-llm-deepseek",
        "version": "0.1.1-rc.2",
        "license": "MIT",
        "source": "github.com/deepseek-ai/deepseek-harness → packages/llm/llm-deepseek",
        "layer": "DeepSeek chat-completions 适配器（harness LLM seam 内）：SSE 流式解析、"
                 "流式 tool_calls（delta.tool_calls）聚合、Files API/上传。"
                 "不是会话管理器，不做重试编排。",
        "installability": "Node.js ESM + 约 12 个 cordis 生态 peer 依赖；MIT 可装，但与工程"
                          "「Python 标准库、零第三方依赖、零构建」红线冲突",
        "reuse_for_llm_client": "语言隔离（TS/ESM vs Python stdlib urllib）无法直接复用；"
                                "非流式（stream:false）场景用不到其 SSE 解析",
        "conflict_with_own_loop": "无冲突（不同层、不同语言）；为采用而引入 Node 侧车，"
                                  "维护成本大于收益",
        "conclusion": "不采用。借鉴点：流式 tool_calls 解析印证「协议原生支持 tools」，"
                      "与 V1 探测同结论；会话与重试不在插件层，仍由自产循环负责。",
    }
    if not QUIET:
        print("      " + json.dumps(verdict, ensure_ascii=False))
    return verdict


# ===========================================================================
# 入口
# ===========================================================================

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    live = "--live" in argv

    base = os.environ.get("GATEWAY_PROBE_BASE") or os.environ.get("AGENT_LLM_API_BASE")
    key = os.environ.get("GATEWAY_PROBE_KEY") or os.environ.get("AGENT_LLM_API_KEY")
    if live and not (base and key):
        sys.stderr.write("--live 需要 GATEWAY_PROBE_BASE 与 GATEWAY_PROBE_KEY"
                         "（或 AGENT_LLM_API_BASE / AGENT_LLM_API_KEY）\n")
        return 2
    if not live:
        sys.stderr.write("[offline] 未提供网关凭据，改跑内建 mock（真实网关结论"
                         "请配好 key 后用 --live 补跑）\n")

    report = {"mode": "live" if live else "mock",
              "model": MODEL, "date": "2026-09-30",
              "key": key_summary(key) if key else "(未提供，未联网)"}
    report["v1"] = probe_v1(base or "", key or "", live)
    report["v2"] = probe_v2(base or "", key or "", live)
    report["v3"] = probe_v3()

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())