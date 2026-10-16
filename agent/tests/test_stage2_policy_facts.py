# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.2：策略层适配 + 修复「原始数据可读从不触发」。

覆盖：
  * 策略表与工具表一一对应（双向，无孤儿规则）；
  * check_call 仍是纯函数（空 facts 不抛、不碰环境）；
  * tool_facts 真实收集事实后，requires_raw 规则**确实会触发**（正/负例）；
  * 三态语义：已确认不可读 → 拦；已确认可读 → 放；未知 → 不拦（交 driver）。
  * http_api 的 LLM 路径确实把事实接进了循环（集成断言）。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api, tool_facts  # noqa: E402
from agent.tool_policy import POLICY, check_call, rule_for  # noqa: E402
from agent.tool_specs import TOOL_SPECS  # noqa: E402


class TestPolicyTableConsistency(unittest.TestCase):
    def test_policy_and_specs_one_to_one(self):
        self.assertEqual(set(TOOL_SPECS), set(POLICY))

    def test_no_orphan_policy_rule(self):
        for name in POLICY:
            with self.subTest(tool=name):
                self.assertIsNotNone(rule_for(name))


class TestFactsCollection(unittest.TestCase):
    def test_pick_running_task(self):
        tasks = {"tasks": [
            {"task_id": "t-done", "status": "succeeded"},
            {"task_id": "t-run", "status": "running"},
            {"task_id": "t-q", "status": "queued"},
        ]}
        self.assertEqual("t-run", tool_facts.pick_running_task(tasks))

    def test_no_running_task_returns_none(self):
        tasks = {"tasks": [{"task_id": "t", "status": "succeeded"}]}
        self.assertIsNone(tool_facts.pick_running_task(tasks))

    def test_probe_reports_missing_and_readable(self):
        self.assertEqual((False, "/x"), tool_facts.probe_raw_readable(
            "/x", exists=lambda p: False))
        self.assertEqual((True, "/x"), tool_facts.probe_raw_readable(
            "/x", exists=lambda p: True))

    def test_probe_unknown_when_no_dir(self):
        self.assertEqual((None, None), tool_facts.probe_raw_readable(""))

    def test_collect_shape(self):
        facts = tool_facts.collect(tasks={"tasks": []}, raw_dir="/x",
                                   exists=lambda p: True)
        self.assertIn("running_task_id", facts)
        self.assertIn("raw_readable", facts)
        self.assertIn("raw_dir", facts)
        self.assertTrue(facts["raw_readable"])
        self.assertIsNone(facts["running_task_id"])


class TestRawRuleActuallyFires(unittest.TestCase):
    """修复计划 §5 死点：真实收集事实后，requires_raw 规则会触发。"""

    def _facts(self, readable):
        return tool_facts.collect(tasks={"tasks": []}, raw_dir="/data/raw",
                                  exists=lambda p: readable)

    def test_missing_raw_rejects_after_real_probe(self):
        facts = self._facts(False)
        ok, why = check_call("start_cleaning_task", facts)
        self.assertFalse(ok)
        self.assertIn("原始数据不可读", why)
        self.assertIn("/data/raw", why)

    def test_readable_raw_allows_after_real_probe(self):
        ok, why = check_call("start_cleaning_task", self._facts(True))
        self.assertTrue(ok, why)

    def test_unknown_raw_does_not_block(self):
        """未知（None）不在策略层拦截 —— 交给 driver 的 DEPENDENCY_MISSING。"""
        facts = tool_facts.collect(tasks={"tasks": []}, raw_dir="")
        self.assertIsNone(facts["raw_readable"])
        ok, why = check_call("start_cleaning_task", facts)
        self.assertTrue(ok, why)

    def test_check_call_is_pure_no_environment_needed(self):
        """纯函数：不传任何事实也不抛异常，不看文件系统。"""
        ok, why = check_call("start_cleaning_task", {})
        self.assertTrue(ok, why)


class TestHttpPathWiresRuntime(unittest.TestCase):
    def test_default_facts_factory_reads_real_task_list(self):
        """运行时默认事实收集确实读真实任务列表（策略规则才会触发）。"""
        from agent.runtime.runtime import default_facts_factory
        original = http_api.tools.list_tasks
        http_api.tools.list_tasks = lambda: {"tasks": [
            {"task_id": "t-run", "status": "running"}]}
        try:
            facts = default_facts_factory()
        finally:
            http_api.tools.list_tasks = original
        self.assertEqual("t-run", facts["running_task_id"])
        self.assertIn("raw_readable", facts)

    def test_chat_routes_through_runtime(self):
        """http_api 自然语言入口走 runtime：建对话、跑一轮、映射信封。"""
        seen = {}

        class FakeRuntime(object):
            def create_conversation(self, title=None):
                seen["title"] = title
                return "c0099"

            def run_turn(self, conversation_id, text):
                seen["cid"] = conversation_id
                seen["text"] = text
                return {"ok": True, "reply": "好", "final": "好", "steps": [],
                        "node_id": 7, "task_id": None, "task_started": False,
                        "error": None}

            def recall_opts(self, conversation_id):
                return {}

            def remember_opts(self, conversation_id, opts):
                return opts

        old = http_api._RUNTIME
        http_api._RUNTIME = FakeRuntime()
        try:
            env = http_api._run_runtime_chat("跑个任务", {}, None,
                                             conversation_id_created=True)
        finally:
            http_api._RUNTIME = old
        self.assertEqual("c0099", env["conversation_id"])
        self.assertEqual("好", env["reply"])
        self.assertEqual("跑个任务", seen["text"])


if __name__ == "__main__":
    unittest.main()
