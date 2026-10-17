# -*- coding: utf-8 -*-
"""远端工具表单测：22 项远端能力改判 source=mcp，本机仅留快速演示。

来源权威：`hadoop/mcpserver/tools.py` 的 `TOOLS`（能力自描述）。本表是 Agent
侧的权威形状；参数名/默认值/枚举/取值范围必须与远端一致。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import tool_specs, mcp_tools  # noqa: E402


class TestRemoteToolSpecs(unittest.TestCase):
    def test_remote_tool_count_and_source(self):
        self.assertEqual(22, len(mcp_tools.REMOTE_TOOL_SPECS))
        for name, spec in mcp_tools.REMOTE_TOOL_SPECS.items():
            with self.subTest(tool=name):
                self.assertEqual("mcp", spec["source"])
                self.assertEqual(name, spec["name"])

    def test_remote_tool_names(self):
        self.assertEqual(22, len(mcp_tools.remote_tool_names()))
        self.assertEqual(tuple(mcp_tools.REMOTE_TOOL_SPECS.keys()),
                         mcp_tools.remote_tool_names())

    def test_spec_for(self):
        self.assertIs(mcp_tools.REMOTE_TOOL_SPECS["get_samples"],
                      mcp_tools.spec_for("get_samples"))
        self.assertIsNone(mcp_tools.spec_for("quick_clean_demo"))
        self.assertIsNone(mcp_tools.spec_for("no_such_tool"))


class TestLocalOnly(unittest.TestCase):
    def test_local_only_is_quick_demo(self):
        self.assertEqual(("quick_clean_demo",), mcp_tools.LOCAL_ONLY_TOOLS)

    def test_tool_specs_routes_shared_tools_to_mcp(self):
        self.assertEqual("mcp", tool_specs.source_for("get_samples"))
        self.assertEqual("mcp", tool_specs.source_for("start_cleaning_task"))
        self.assertEqual("mcp", tool_specs.source_for("score_task"))
        self.assertEqual("local", tool_specs.source_for("quick_clean_demo"))

    def test_only_quick_demo_is_local(self):
        local = [n for n in tool_specs.tool_names()
                 if tool_specs.source_for(n) == "local"]
        self.assertEqual(["quick_clean_demo"], local)


class TestParamAlignment(unittest.TestCase):
    def test_samples_uses_limit_not_n(self):
        props = mcp_tools.REMOTE_TOOL_SPECS["get_samples"]["parameters"]["properties"]
        self.assertIn("limit", props)
        self.assertNotIn("n", props)
        self.assertEqual(5, props["limit"]["default"])
        self.assertEqual(0, props["limit"]["minimum"])
        self.assertEqual(2000, props["limit"]["maximum"])

    def test_samples_defaults_match_remote(self):
        props = mcp_tools.REMOTE_TOOL_SPECS["get_samples"]["parameters"]["properties"]
        self.assertEqual("cleaned", props["sample_type"]["default"])
        self.assertEqual(["cleaned", "quarantine"], props["sample_type"]["enum"])
        self.assertEqual("movies", props["table"]["default"])
        self.assertEqual(["movies", "ratings", "users"], props["table"]["enum"])

    def test_report_uses_format_not_fmt(self):
        props = mcp_tools.REMOTE_TOOL_SPECS["get_report"]["parameters"]["properties"]
        self.assertIn("format", props)
        self.assertNotIn("fmt", props)
        self.assertEqual("json", props["format"]["default"])
        self.assertEqual(["md", "json"], props["format"]["enum"])

    def test_no_foreground_or_exec_mode_on_start_tools(self):
        for name in ("start_cleaning_task", "start_clean_task", "score_task"):
            with self.subTest(tool=name):
                props = mcp_tools.REMOTE_TOOL_SPECS[name]["parameters"]["properties"]
                self.assertNotIn("foreground", props)
                self.assertNotIn("exec_mode", props)

    def test_score_task_requires_source(self):
        spec = mcp_tools.REMOTE_TOOL_SPECS["score_task"]
        self.assertEqual(["source"], spec["parameters"]["required"])
        self.assertEqual(["raw", "published", "task"],
                         spec["parameters"]["properties"]["source"]["enum"])

    def test_long_running_flags_and_estimates(self):
        expected = {"start_cleaning_task": 1500, "start_clean_task": 900,
                    "score_task": 300, "wait_for_task": 300}
        for name, seconds in expected.items():
            with self.subTest(tool=name):
                spec = mcp_tools.REMOTE_TOOL_SPECS[name]
                self.assertTrue(spec["long_running"])
                self.assertEqual(seconds, spec["estimated_seconds"])
        # 只读工具非长耗时
        self.assertFalse(mcp_tools.REMOTE_TOOL_SPECS["get_samples"]["long_running"])


if __name__ == "__main__":
    unittest.main()
