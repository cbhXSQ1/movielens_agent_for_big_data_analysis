# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.4：双协议解析与「是否发起任务」判定。

覆盖两种形态（原生 tool_calls / 严格 JSON）都能解析；任务判定区分
「发起任务」与「查询已存在任务」；旧循环的薄封装与独立模块一致。
"""
import json
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import protocol  # noqa: E402


class TestParseTwoForms(unittest.TestCase):
    def test_native_tool_calls(self):
        msg = {"tool_calls": [{"function": {"name": "list_tasks",
                                            "arguments": "{}"}}]}
        self.assertEqual(("tool", "list_tasks", {}),
                         protocol.parse_model_response(None, msg))

    def test_native_bad_arguments_defaults_empty(self):
        msg = {"tool_calls": [{"function": {"name": "get_report",
                                            "arguments": "not-json"}}]}
        self.assertEqual(("tool", "get_report", {}),
                         protocol.parse_model_response(None, msg))

    def test_strict_json_final(self):
        self.assertEqual(("final", "好的", None),
                         protocol.parse_model_response('{"final": "好的"}', {}))

    def test_strict_json_action(self):
        self.assertEqual(("tool", "get_report", {"task_id": "t1"}),
                         protocol.parse_model_response(
                             '{"action": "get_report", "args": {"task_id": "t1"}}', {}))

    def test_invalid_returns_none(self):
        self.assertIsNone(protocol.parse_model_response("不是 JSON", {}))
        self.assertIsNone(protocol.parse_model_response("", {"tool_calls": None}))


class TestTaskJudgement(unittest.TestCase):
    def test_start_step_detected(self):
        step = {"tool": "start_cleaning_task", "ok": True,
                "envelope": {"ok": True, "task_id": "2026-t1"}}
        self.assertTrue(protocol.step_started_task(step))

    def test_query_step_is_not_a_start(self):
        step = {"tool": "get_task_status", "ok": True,
                "envelope": {"ok": True, "task_id": "2026-t2"}}
        self.assertFalse(protocol.step_started_task(step))

    def test_failed_start_not_counted(self):
        step = {"tool": "start_cleaning_task", "ok": False,
                "envelope": {"ok": False}}
        self.assertFalse(protocol.step_started_task(step))

    def test_task_info_aggregates(self):
        result = {"steps": [
            {"tool": "start_cleaning_task", "ok": True,
             "envelope": {"ok": True, "task_id": "2026-t1"}},
        ]}
        self.assertEqual(("2026-t1", True), protocol.task_info(result))

    def test_task_info_query_only(self):
        result = {"steps": [
            {"tool": "get_task_status", "ok": True,
             "envelope": {"ok": True, "task_id": "2026-t2"}},
        ]}
        self.assertEqual(("2026-t2", False), protocol.task_info(result))


if __name__ == "__main__":
    unittest.main()
