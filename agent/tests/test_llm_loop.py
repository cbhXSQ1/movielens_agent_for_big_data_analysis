# -*- coding: utf-8 -*-
"""第 5 节 Agent v2 阶段 3：LLM 循环单测（Mock 模型）—— 多步序列、模型失败
回退规则路径、策略拒绝后要求重规划、双协议形态解析、限步护栏。

工具执行是**真实** tools.py + 真实 driver（确定性代码，符合计划：
"工具执行仍是确定性代码；模型不生产数字"）；只有"模型"是注入的 mock。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

import json

from agent import llm_loop  # noqa: E402


def native_tool_response(name, args):
    """构造原生 tool_calls 形态的模型响应。"""
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"type": "function", "id": "call_1",
                        "function": {"name": name,
                                     "arguments": json.dumps(args)}}]},
        "finish_reason": "tool_calls"}]}


def strict_json_response(obj):
    """构造严格 JSON 协议形态的模型响应。"""
    return {"choices": [{"message": {"role": "assistant",
                                     "content": json.dumps(obj)},
                         "finish_reason": "stop"}]}


class ScriptedChat(object):
    """按脚本依次响应的 mock 模型；返回 (body, None) 或 (None, err)。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, cfg, payload):
        self.calls.append(payload)
        if not self.responses:
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "超步兜底"}}]}, None
        item = self.responses.pop(0)
        if isinstance(item, tuple) and item[0] is None:
            return (None, item[1])
        return item, None


class TestParseModelResponse(unittest.TestCase):
    def test_native_tool_calls(self):
        kind, name, args = llm_loop._parse_model_response(
            None, {"tool_calls": [{"function": {"name": "list_tasks",
                                                "arguments": "{}"}}]})
        self.assertEqual(("tool", "list_tasks", {}), (kind, name, args))

    def test_strict_json_final(self):
        kind, text, _ = llm_loop._parse_model_response('{"final": "好的"}', {})
        self.assertEqual(("final", "好的", None), (kind, text, _))

    def test_strict_json_action(self):
        kind, name, args = llm_loop._parse_model_response(
            '{"action": "get_report", "args": {"task_id": "t1", "fmt": "md"}}', {})
        self.assertEqual(("tool", "get_report",
                          {"task_id": "t1", "fmt": "md"}), (kind, name, args))

    def test_invalid_returns_none(self):
        self.assertIsNone(llm_loop._parse_model_response("不是 JSON", {}))
        self.assertIsNone(llm_loop._parse_model_response("", {"tool_calls": None}))


class TestLoopMultiStep(unittest.TestCase):
    def test_native_tool_calls_then_final(self):
        """原生协议多步：model 先调 validate_config，拿到结果后给 final。"""
        fake = ScriptedChat([
            native_tool_response("validate_config", {}),
            strict_json_response({"final": "默认配置合法，可以发起任务。"}),
        ])
        result = llm_loop.run_loop("配置合法吗？", cfg=None, chat=fake)
        self.assertTrue(result["ok"])
        self.assertEqual("llm", result["engine"])
        self.assertEqual("默认配置合法，可以发起任务。", result["final"])
        self.assertEqual(1, len(result["steps"]))
        step = result["steps"][0]
        self.assertEqual("validate_config", step["tool"])
        self.assertTrue(step["ok"])

    def test_strict_json_action_then_final(self):
        """严格 JSON 协议：无 tool_calls，靠 {"action"} 走同一条循环。"""
        fake = ScriptedChat([
            strict_json_response({"action": "list_schemes", "args": {}}),
            strict_json_response({"final": "已登记 2 个方案。"}),
        ])
        result = llm_loop.run_loop("有哪些方案？", cfg=None, chat=fake)
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(result["steps"]))
        self.assertEqual("list_schemes", result["steps"][0]["tool"])


class TestFallback(unittest.TestCase):
    def test_model_failure_falls_back_to_rules(self):
        """模型不可用 → 回退回调（规则路径）并如实标注。"""
        seen = {}
        def fallback(question):
            seen["q"] = question
            return {"ok": True, "reply": "规则回答"}
        fake = ScriptedChat([(None, "网络不可达")])
        result = llm_loop.run_loop("清洗数据", cfg=None, chat=fake,
                                   fallback=fallback)
        self.assertTrue(result["fallback"])
        self.assertEqual("清洗数据", seen.get("q"))
        self.assertEqual("规则回答", result.get("reply"))
        self.assertIn("网络不可达", result.get("reason", ""))

    def test_no_fallback_marks_rules_engine(self):
        fake = ScriptedChat([(None, "网络不可达")])
        result = llm_loop.run_loop("清洗数据", cfg=None, chat=fake)
        self.assertTrue(result["fallback"])
        self.assertEqual("rules", result.get("engine"))
        self.assertFalse(result["llm"]["used"])

    def test_loop_limit_reaches_fallback(self):
        """模型永远要工具 → 步数上限截断并回退（不限时，避免拉起真实任务）。"""
        fake = ScriptedChat([native_tool_response("get_task_status",
                                                  {"task_id": "no-such-task"})
                             ] * llm_loop.MAX_STEPS)
        result = llm_loop.run_loop("一直查？", cfg=None, chat=fake)
        self.assertTrue(result["fallback"])
        self.assertIn("上限", result.get("reason", ""))


class TestPolicyRejection(unittest.TestCase):
    def test_rejected_call_triggers_replan(self):
        """有任务在跑时提交 start_cleaning_task → 策略拒绝 → 回灌 → model 改口。"""
        fake = ScriptedChat([
            native_tool_response("start_cleaning_task", {}),
            strict_json_response({"final": "当前有任务在运行，无法并发发起。"}),
        ])
        result = llm_loop.run_loop("再跑一次清洗",
                                   cfg=None, chat=fake,
                                   facts={"running_task_id": "2026-999999-abc123"})
        # 工具未被执行：steps 为空，final 是模型重新组织的话
        self.assertTrue(result["ok"])
        self.assertEqual([], result["steps"])
        self.assertEqual("当前有任务在运行，无法并发发起。", result["final"])
        # 模型第二轮的输入里包含了拒绝原因
        second_payload = fake.calls[1]
        joined = " ".join(m.get("content") or "" for m in second_payload["messages"])
        self.assertIn("策略拒绝", joined)


class TestFinalNumberGuard(unittest.TestCase):
    """阶段 5：回答里的每个数字必须能在本会话工具结果里找到。"""

    def test_fabricated_number_blocked_after_retry(self):
        """模型坚持编数字 → 拦截；编造的 888888 必须进 blocked 原因。"""
        fake = ScriptedChat([
            native_tool_response("list_schemes", {}),
            strict_json_response({"final": "清洗方案有 2 个，其中编号 888888 待定。"}),
            strict_json_response({"final": "清洗方案有 2 个，其中编号 888888 待定。"}),
        ])
        result = llm_loop.run_loop("有哪些方案？", cfg=None, chat=fake)
        self.assertFalse(result["ok"])
        self.assertIsNone(result.get("final"))
        self.assertIn("888888", result.get("blocked", ""))
        # 模型确实被要求重写了一次（3 次调用 = 工具 + final + 重试 final）
        self.assertEqual(3, len(fake.calls))

    def test_tool_sourced_numbers_pass(self):
        """final 里的数字全部来自工具返回（list_schemes 的版本号）→ 放行。"""
        fake = ScriptedChat([
            native_tool_response("list_schemes", {}),
            strict_json_response({"final": "列表里版本 1.0.0 的方案可以直接用。"}),
        ])
        result = llm_loop.run_loop("方案版本是多少？", cfg=None, chat=fake)
        self.assertTrue(result["ok"])
        self.assertIn("1.0.0", result["final"])

    def test_question_numbers_are_authorized(self):
        """用户原话里的数字合法（追问「给我 5 条」的 5 不被当编造）。"""
        fake = ScriptedChat([
            strict_json_response({"final": "好的，这就给你 5 条。"}),
        ])
        result = llm_loop.run_loop("给我 5 条样本", cfg=None, chat=fake)
        self.assertTrue(result["ok"])


class TestFallbackPreservesRulesOk(unittest.TestCase):
    def test_rules_success_not_overwritten_by_fallback_mark(self):
        """模型失败 → 规则路径成功（ok=True）→ 标注 fallback 但保留 ok 与 reply。"""
        fake = ScriptedChat([(None, "网络不可达")])
        result = llm_loop.run_loop(
            "有哪些方案？", cfg=None, chat=fake,
            fallback=lambda q: {"ok": True, "reply": "有 2 个方案",
                                "intent": "list_schemes"})
        self.assertTrue(result["fallback"])
        self.assertTrue(result["ok"])                    # 规则的成功不被覆盖
        self.assertEqual("有 2 个方案", result["reply"])
        self.assertIn("网络不可达", result["reason"])


class TestLoopTaskInfo(unittest.TestCase):
    def test_started_task_detected(self):
        """start_cleaning_task 成功步 → _loop_task_info 认出发起与 task_id。"""
        result = {"steps": [
            {"tool": "start_cleaning_task", "ok": True,
             "envelope": {"ok": True, "task_id": "2026-t1"}},
        ]}
        tid, started = llm_loop._loop_task_info(result)
        self.assertEqual("2026-t1", tid)
        self.assertTrue(started)

    def test_query_step_carries_task_id(self):
        result = {"steps": [
            {"tool": "get_task_status", "ok": True,
             "envelope": {"ok": True, "task_id": "2026-t2"}},
        ]}
        tid, started = llm_loop._loop_task_info(result)
        self.assertEqual("2026-t2", tid)
        self.assertFalse(started)


if __name__ == "__main__":
    unittest.main()