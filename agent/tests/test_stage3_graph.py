# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· T3.3：工具调用循环落到 LangGraph 图上的单测。

全部用**假模型**（FakeStreamTransport 承载的脚本化 provider）驱动，工具执行用
假后端（可控、不碰 driver、不碰真实任务）。覆盖：多步调用、双协议、策略拒绝后
重规划、数字护栏（含正向断言）、模型不可用直接报错（不回退）、循环上限、
大结果外置。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import ARTIFACT_THRESHOLD, MAX_STEPS, Runtime, envelope_of  # noqa: E402
from agent.tests.stage3_helpers import (FakeToolBackend, SAMPLE_TASKS,  # noqa: E402
                                        error_provider, make_provider,
                                        text_response, tool_response)


class GraphTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage3_graph_")
        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self.rt.close)
        self.cid = self.rt.create_conversation(title="图测试")

    def backend(self, responses=None):
        data = {"list_tasks": SAMPLE_TASKS}
        if responses:
            data.update(responses)
        return FakeToolBackend(data)


class TestMultiStep(GraphTestBase):
    def test_tool_then_final(self):
        """先 list_tasks，拿到结果后给最终回答：工具经策略校验被执行、结果回灌。"""
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("最近共 2 个任务，其中 1 个在运行。"),
        ])
        backend = self.backend()
        result = self.rt.run_turn(self.cid, "之前跑过哪些任务？",
                                  provider=provider, tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual("llm", result["engine"])
        self.assertEqual(1, len(result["steps"]))
        self.assertEqual("list_tasks", result["steps"][0]["tool"])
        self.assertTrue(result["steps"][0]["ok"])
        self.assertEqual([("list_tasks", {})], backend.calls)
        self.assertEqual("最近共 2 个任务，其中 1 个在运行。", result["final"])

    def test_strict_json_action_then_final(self):
        """严格 JSON 协议：无 tool_calls，靠 {"action"} 走同一条循环。"""
        provider = make_provider([
            text_response('{"action": "list_tasks", "args": {}}'),
            text_response('{"final": "共 2 个任务。"}'),
        ])
        result = self.rt.run_turn(self.cid, "有哪些任务？",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual(1, len(result["steps"]))
        self.assertEqual("list_tasks", result["steps"][0]["tool"])
        self.assertEqual("共 2 个任务。", result["final"])

    def test_large_tool_result_externalized(self):
        """大信封外置：状态步骤里只留 ref，读回后数字仍可溯源。"""
        big = {"ok": True, "note": "x" * (ARTIFACT_THRESHOLD + 500),
               "tasks": [{"task_id": "2026-aaaaaaaa"}], "total": 7}
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("共 7 个任务。"),
        ])
        result = self.rt.run_turn(self.cid, "有多少任务？",
                                  provider=provider,
                                  tool_backend=FakeToolBackend({"list_tasks": big}),
                                  facts={})
        self.assertTrue(result["ok"], result)
        note = result["steps"][0]
        self.assertIn("artifact", note)
        self.assertNotIn("envelope", note)
        self.assertEqual(7, envelope_of(note, self.rt.artifacts)["total"])


class TestPolicy(GraphTestBase):
    def test_rejected_call_replans(self):
        """有任务在跑时提交 start_cleaning_task → 策略拒绝 → 回灌 → 模型改口。"""
        provider = make_provider([
            tool_response("start_cleaning_task", {}),
            text_response("当前有任务在运行，无法并发发起。"),
        ])
        backend = self.backend({"start_cleaning_task": {
            "ok": True, "task_id": "2026-cccccccc"}})
        result = self.rt.run_turn(
            self.cid, "再跑一次清洗", provider=provider, tool_backend=backend,
            facts={"running_task_id": "2026-999999-abc123"})
        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["steps"])          # 被拒，未执行
        self.assertEqual([], backend.calls)
        self.assertEqual("当前有任务在运行，无法并发发起。", result["final"])

    def test_policy_rejection_records_error_when_model_insists(self):
        """模型固执重复被拒调用 → 超过重试上限 → 结构化报错。"""
        provider = make_provider([
            tool_response("start_cleaning_task", {}),
            tool_response("start_cleaning_task", {}),
            tool_response("start_cleaning_task", {}),
        ])
        result = self.rt.run_turn(
            self.cid, "一直发", provider=provider, tool_backend=self.backend(),
            facts={"running_task_id": "2026-running"})
        self.assertFalse(result["ok"])
        self.assertEqual("POLICY_REJECTED", result["error"]["code"])


class TestNumberGuard(GraphTestBase):
    def test_fabricated_number_soft_warning_with_span(self):
        """模型编造 888888（工具没返回过）→ 正常放行，只给提示与原文区间。"""
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("任务编号 888888 待定。"),
        ])
        result = self.rt.run_turn(self.cid, "有哪些任务？",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual("任务编号 888888 待定。", result["final"])
        self.assertIn("888888", result["guard_warning"])
        self.assertIsNone(result["error"])
        spans = result["guard_spans"]
        self.assertEqual(1, len(spans))
        self.assertEqual("888888", result["final"][spans[0]["start"]:spans[0]["end"]])
        self.assertIn("标红", result["llm"]["note"])

    def test_no_tool_calls_skips_guard(self):
        """本回合没有工具调用：即使回答里有数字，也不提示。"""
        provider = make_provider([text_response("任务编号 888888 待定。")])
        result = self.rt.run_turn(self.cid, "随便聊聊",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["guard_warning"])
        self.assertEqual([], result["guard_spans"])

    def test_list_markers_produce_no_spans(self):
        """列表序号是排版编号，不产生标红区间。"""
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("1. 先看任务\n2. 再跑清洗\n3. 最后汇报"),
        ])
        result = self.rt.run_turn(self.cid, "下一步做什么？",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["guard_warning"])
        self.assertEqual([], result["guard_spans"])

    def test_tool_sourced_numbers_pass(self):
        """正向断言：回答里的数字都来自工具结果 → 放行（护栏不误伤）。"""
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("共 2 个任务。"),
        ])
        result = self.rt.run_turn(self.cid, "有几个任务？",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertTrue(result["ok"], result)
        self.assertIn("2", result["final"])
        self.assertIsNone(result["guard_warning"])
        self.assertEqual([], result["guard_spans"])

    def test_thousands_separator_is_traced(self):
        """工具返回 100830、回答写成 100,830：规范化后仍算可溯源。"""
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("共 100,830 行。"),
        ])
        result = self.rt.run_turn(
            self.cid, "有多少行？", provider=provider,
            tool_backend=self.backend({"list_tasks": {"ok": True, "total": 100830}}),
            facts={})
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["guard_warning"])

    def test_question_numbers_are_authorized(self):
        """正向断言：追问「给我 5 条」的 5 来自用户原话，不被当编造。"""
        provider = make_provider([text_response("好的，这就给你 5 条。")])
        result = self.rt.run_turn(self.cid, "给我 5 条样本",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertTrue(result["ok"], result)


class TestFailureModes(GraphTestBase):
    def test_model_unavailable_errors_without_fallback(self):
        """模型不可用 → 直接结构化报错（不静默回退规则路径）。"""
        result = self.rt.run_turn(self.cid, "清洗数据",
                                  provider=error_provider(),
                                  tool_backend=self.backend(), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("UPSTREAM_ERROR", result["error"]["code"])
        self.assertIsNone(result["final"])
        self.assertNotIn("fallback", result)

    def test_loop_limit(self):
        """模型永远要工具 → 达到步数上限 → 结构化报错（不限时）。"""
        provider = make_provider([tool_response("list_tasks", {})])
        result = self.rt.run_turn(self.cid, "一直查？",
                                  provider=provider, tool_backend=self.backend(),
                                  facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("LOOP_LIMIT", result["error"]["code"])
        self.assertEqual(MAX_STEPS, len(result["steps"]))


if __name__ == "__main__":
    unittest.main()
