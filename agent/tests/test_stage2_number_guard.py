# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.3：数字溯源护栏（字段 + 上下文级）。

必须同时有：
  * 正向断言 —— 正常路径确实放行（工具返回过的数字、用户原话数字、数组长度）；
  * 负向断言 —— 编造数字被拦，且报告带出上下文片段与字段来源。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import number_guard as ng  # noqa: E402


def envelope_step(tool, envelope, ok=True):
    return {"tool": tool, "args": {}, "ok": ok, "envelope": envelope}


class TestExtraction(unittest.TestCase):
    def test_numbers_in_text_strips_thousands(self):
        self.assertEqual(["100830", "5"],
                         ng.numbers_in_text("共 100,830 行，隔离 5 条"))

    def test_normalize_full_width_comma(self):
        self.assertEqual(["2024"], ng.numbers_in_text("202，4"))

    def test_array_length_is_authorized(self):
        auth = ng.AuthorizedNumbers()
        ng.index_value({"schemes": [{"a": 1}, {"a": 2}]}, auth)
        self.assertTrue(auth.has("2"))          # 数组长度


class TestFieldContextProvenance(unittest.TestCase):
    def test_provenance_carries_field_path_and_context(self):
        steps = [envelope_step("get_task_result",
                               {"ok": True, "counts": {"quarantine": {"total": 42}}})]
        auth = ng.build_authorized("结果如何", steps)
        self.assertTrue(auth.has("42"))
        paths = [p["path"] for p in auth.provenance("42")]
        self.assertIn("get_task_result.counts.quarantine.total", paths)
        contexts = [p["context"] for p in auth.provenance("42")]
        self.assertTrue(any("get_task_result" in c for c in contexts))

    def test_question_numbers_authorized_under_question_path(self):
        auth = ng.build_authorized("给我 5 条", [])
        paths = [p["path"] for p in auth.provenance("5")]
        self.assertIn(ng.QUESTION_PATH, paths)

    def test_args_are_not_authorized(self):
        """模型自己填的 args 不算「结果数字」，不能给它背书。"""
        step = {"tool": "get_samples", "args": {"n": 999},
                "ok": True, "envelope": {"ok": True}}
        auth = ng.build_authorized("取样", [step])
        self.assertFalse(auth.has("999"))


class TestPositivePath(unittest.TestCase):
    def test_tool_sourced_numbers_pass(self):
        steps = [envelope_step("get_task_result",
                               {"ok": True,
                                "scores": {"after": {"Accurate": 0.91}},
                                "counts": {"quarantine": {"total": 7}}})]
        auth = ng.build_authorized("结果如何？", steps)
        final = "准确率 0.91，隔离了 7 行。"
        self.assertEqual([], ng.find_unauthorized(final, auth))

    def test_question_numbers_pass(self):
        auth = ng.build_authorized("给我 5 条", [])
        self.assertEqual([], ng.find_unauthorized("好的，这就给你 5 条。", auth))

    def test_legacy_convenience_function(self):
        steps = [envelope_step("list_schemes", {"ok": True, "total": 2})]
        self.assertEqual(set(), ng.unauthorized_numbers("共 2 个方案", "有哪些方案", steps))


class TestNegativePath(unittest.TestCase):
    def test_fabricated_number_blocked_with_context(self):
        steps = [envelope_step("list_schemes", {"ok": True, "total": 2})]
        auth = ng.build_authorized("有哪些方案？", steps)
        records = ng.find_unauthorized("方案有 2 个，其中编号 888888 待定。", auth)
        numbers = [r["number"] for r in records]
        self.assertIn("888888", numbers)
        rec = [r for r in records if r["number"] == "888888"][0]
        self.assertIn("888888", rec["snippet"])       # 带出上下文片段
        self.assertNotIn("2", numbers)                # 合法数字不被误报

    def test_no_false_positive_on_clean_answer(self):
        steps = [envelope_step("get_task_result",
                               {"ok": True, "scores": {"after": {"Accurate": 0.8}}})]
        auth = ng.build_authorized("?", steps)
        self.assertEqual([], ng.find_unauthorized("准确率 0.8。", auth))


class TestDescribe(unittest.TestCase):
    def test_describe_mentions_number_and_snippet(self):
        records = [{"number": "888888", "snippet": "…编号 888888 待定…"}]
        text = ng.describe(records)
        self.assertIn("888888", text)
        self.assertIn("待定", text)


if __name__ == "__main__":
    unittest.main()
