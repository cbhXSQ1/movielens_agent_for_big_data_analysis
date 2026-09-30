# -*- coding: utf-8 -*-
"""第 5 节 Agent v2 阶段 2：策略层单测 —— 每工具的准入/拒绝、任务锁、依赖缺失。

覆盖计划 §阶段 2 验收：每个工具的准入/拒绝、任务锁、依赖缺失、发布互斥
（发布互斥属 v1.1 score(published)，用 PENDING 标注与未注册拒绝覆盖）。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.tool_policy import POLICY, check_call, describe_policy, rule_for  # noqa: E402
from agent.tool_specs import tool_names  # noqa: E402


class TestPolicyTable(unittest.TestCase):
    def test_every_tool_has_a_policy_rule(self):
        """工具表与策略表一一对应：每个已注册工具都有策略条目。"""
        for name in tool_names():
            with self.subTest(tool=name):
                self.assertIsNotNone(rule_for(name))

    def test_locking_tools_are_write_side(self):
        locks = sorted(n for n, r in POLICY.items() if r.get("locks"))
        # full 全流程 + v1.1 两项精细写任务
        self.assertEqual(["score_task", "start_clean_task", "start_cleaning_task"], locks)

    def test_read_only_tools_are_the_read_side(self):
        read_only = sorted(n for n, r in POLICY.items() if r.get("read_only"))
        self.assertEqual(
            ["get_report", "get_samples", "get_task_result", "get_task_status",
             "list_schemes", "list_tasks", "validate_config"], read_only)

    def test_raw_prereq_on_demo_and_start(self):
        self.assertTrue(POLICY["start_cleaning_task"].get("requires_raw"))
        self.assertTrue(POLICY["quick_clean_demo"].get("requires_raw"))


class TestCheckCall(unittest.TestCase):
    def test_unregistered_tool_rejected(self):
        ok, why = check_call("no_such_tool", {})
        self.assertFalse(ok)
        self.assertIn("未注册", why)

    def test_locking_tool_rejected_when_task_running(self):
        ok, why = check_call("start_cleaning_task",
                             {"running_task_id": "2026-t1", "raw_readable": True})
        self.assertFalse(ok)
        self.assertIn("任务锁", why)

    def test_locking_tool_allowed_when_idle(self):
        ok, why = check_call("start_cleaning_task",
                             {"running_task_id": None, "raw_readable": True})
        self.assertTrue(ok, why)

    def test_raw_missing_rejects_start_and_demo(self):
        for name in ("start_cleaning_task", "quick_clean_demo"):
            with self.subTest(tool=name):
                ok, why = check_call(name, {"running_task_id": None,
                                            "raw_readable": False,
                                            "raw_dir": "/no/such/dir"})
                self.assertFalse(ok)
                self.assertIn("原始数据不可读", why)

    def test_read_tools_parallel_allowed(self):
        """只读工具：任务在跑也不拦（可与写任务并行）。"""
        for name in ("get_task_status", "get_task_result", "get_samples",
                     "get_report", "list_tasks", "list_schemes", "validate_config"):
            with self.subTest(tool=name):
                ok, why = check_call(name, {"running_task_id": "2026-t1"})
                self.assertTrue(ok, why)

    def test_quick_demo_ignores_task_lock(self):
        """演示工具不占锁：有任务在跑也能跑（本地引擎，不碰集群状态）。"""
        ok, why = check_call("quick_clean_demo",
                             {"running_task_id": "2026-t1", "raw_readable": True})
        self.assertTrue(ok, why)

    def test_v11_tools_registered_with_lock(self):
        """v1.1 精细任务已转正：锁定写工具占用任务锁、读侧不受影响。"""
        for name in ("start_clean_task", "score_task"):
            with self.subTest(tool=name):
                rule = rule_for(name)
                self.assertIsNotNone(rule)
                self.assertTrue(rule.get("locks"))
        ok, why = check_call("start_clean_task", {"running_task_id": "2026-t1"})
        self.assertFalse(ok)                      # 锁生效
        ok, why = check_call("score_task", {"running_task_id": None})
        self.assertTrue(ok, why)

    def test_describe_policy_is_human_readable(self):
        self.assertIn("任务锁", describe_policy("start_cleaning_task"))
        self.assertIn("只读", describe_policy("get_task_result"))


if __name__ == "__main__":
    unittest.main()