# -*- coding: utf-8 -*-
"""结构化错误码扩充与细类单测（Task 5）。

- `STRUCTURED_ERROR_CODES` 收录远端可能出现的全部结构化码（本端词汇表对齐）；
- `REASON_KINDS` 与远端读取层共用一套细类取值；
- `describe_error(error)` 生成面向用户的**可读中文**说明，回退顺序为：
  已知码 → 远端 `error.message` → 原始码；文案不得出现「冒烟/对账/口径」。
- `normalize_error` 语义无损：未知码原样透传，既有映射不变。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_contract as c  # noqa: E402


NEW_CODES = (
    "ABORTED",
    "FILE_NOT_FOUND",
    "FORBIDDEN_PATH",
    "CONTENT_TOO_LARGE",
    "PRODUCT_INVALID",
    "CANCEL_FAILED",
    "UNAUTHORIZED",
    "FORBIDDEN_HOST",
    "WAIT_LIMIT_REACHED",
    "INTERNAL_ERROR",
    "NOT_PREVIEWABLE",
)

EXISTING_CODES = (
    "USAGE",
    "TASK_FAILED",
    "TASK_NOT_FOUND",
    "TASK_NOT_FINISHED",
    "VERSION_CONFLICT",
    "DEPENDENCY_MISSING",
    "TASK_ALREADY_RUNNING",
    "DRIVER_NO_OUTPUT",
    "DRIVER_BAD_OUTPUT",
    "DRIVER_UNREACHABLE",
    "DRIVER_TIMEOUT",
    "MCP_UNAVAILABLE",
)

FORBIDDEN_WORDS = ("冒烟", "对账", "口径")


class TestStructuredErrorCodes(unittest.TestCase):
    def test_new_codes_present(self):
        for code in NEW_CODES:
            with self.subTest(code=code):
                self.assertIn(code, c.STRUCTURED_ERROR_CODES)

    def test_existing_codes_kept(self):
        for code in EXISTING_CODES:
            with self.subTest(code=code):
                self.assertIn(code, c.STRUCTURED_ERROR_CODES)


class TestReasonKinds(unittest.TestCase):
    def test_reason_kinds_exact_set(self):
        self.assertEqual(
            {"control", "sidecar", "residue", "directory", "not_regular_file"},
            set(c.REASON_KINDS))
        self.assertEqual(
            ("control", "sidecar", "residue", "directory", "not_regular_file"),
            tuple(c.REASON_KINDS))


class TestDescribeError(unittest.TestCase):
    def test_known_code_gives_readable_chinese(self):
        text = c.describe_error({"code": "TASK_NOT_FOUND", "message": "raw-en-body"})
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())
        self.assertIn("任务", text)
        # 已知码优先本端说明，不直接回退到远端 message / 原始码
        self.assertNotEqual("TASK_NOT_FOUND", text)
        self.assertNotIn("raw-en-body", text)

    def test_known_code_as_bare_string(self):
        self.assertEqual(c.describe_error("ABORTED"), c.describe_error({"code": "ABORTED"}))

    def test_unknown_code_falls_back_to_message(self):
        text = c.describe_error({"code": "SOME_FUTURE_CODE", "message": "远端给的说明"})
        self.assertEqual("远端给的说明", text)

    def test_unknown_code_without_message_returns_raw_code(self):
        self.assertEqual("SOME_FUTURE_CODE", c.describe_error({"code": "SOME_FUTURE_CODE"}))
        self.assertEqual("SOME_FUTURE_CODE", c.describe_error("SOME_FUTURE_CODE"))

    def test_empty_or_missing_is_readable_fallback(self):
        for value in ({}, None, "", {"message": ""}):
            with self.subTest(value=value):
                text = c.describe_error(value)
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip())

    def test_known_driver_native_code_has_explanation(self):
        self.assertEqual(c.describe_error({"code": "CONFIG_INVALID"}),
                         c.describe_error("CONFIG_INVALID"))
        self.assertNotEqual("CONFIG_INVALID", c.describe_error({"code": "CONFIG_INVALID"}))

    def test_all_known_codes_are_readable_and_wording_safe(self):
        for code in c.STRUCTURED_ERROR_CODES + getattr(c, "DRIVER_NATIVE_CODES", ()):
            with self.subTest(code=code):
                text = c.describe_error({"code": code})
                self.assertTrue(text.strip())
                self.assertNotEqual(code, text)
                for bad in FORBIDDEN_WORDS:
                    self.assertNotIn(bad, text)


class TestNormalizeSemanticsPreserving(unittest.TestCase):
    def test_new_structured_code_passes_through_unchanged(self):
        err = {"code": "FILE_NOT_FOUND", "message": "不存在"}
        out = c.normalize_error(err)
        self.assertEqual("FILE_NOT_FOUND", out["code"])
        self.assertEqual("不存在", out["message"])

    def test_unknown_code_still_passes_through(self):
        out = c.normalize_error({"code": "TOTALLY_UNKNOWN", "message": "x"})
        self.assertEqual("TOTALLY_UNKNOWN", out["code"])

    def test_existing_exit_mapping_unchanged(self):
        out = c.normalize_error({"code": "SOME_DRIVER_CODE"},
                                exit_code=2)  # EXIT_USAGE
        self.assertEqual("USAGE", out["code"])
        self.assertEqual("SOME_DRIVER_CODE", out.get("driver_code"))


if __name__ == "__main__":
    unittest.main()
