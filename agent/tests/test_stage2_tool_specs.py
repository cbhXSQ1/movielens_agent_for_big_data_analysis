# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.1：统一工具表 —— 「来源」字段与 schema/实现一致性。

锁定：
  * 每条工具都带「来源」字段，默认 local；
  * schema 的 properties 与 tools.* 实现形参**逐项一致**（不只是子集）；
  * 新增的 source_for / tools_by_source 辅助函数行为正确。
"""
import inspect
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_tools, tools  # noqa: E402
from agent.tool_specs import (TOOL_SPECS, SPEC_TO_FUNCTION, tool_names,  # noqa: E402
                              schema_for, source_for, tools_by_source,
                              SOURCE_LOCAL, SOURCE_MCP, DEFAULT_SOURCE)


class TestToolSource(unittest.TestCase):
    def test_every_tool_declares_source(self):
        for name, spec in TOOL_SPECS.items():
            with self.subTest(tool=name):
                self.assertIn("source", spec)
                self.assertIn(spec["source"], (SOURCE_LOCAL, SOURCE_MCP))

    def test_default_source_is_local(self):
        """未注册的工具按本地（默认 local）处理。"""
        self.assertEqual(SOURCE_LOCAL, DEFAULT_SOURCE)
        self.assertEqual(DEFAULT_SOURCE, source_for("no_such_tool"))

    def test_shared_tools_are_mcp_only_demo_is_local(self):
        """共享能力一律走远端；本机只留快速演示（quick_clean_demo）。"""
        local = [name for name in tool_names() if source_for(name) == SOURCE_LOCAL]
        self.assertEqual(["quick_clean_demo"], local)
        self.assertEqual(SOURCE_MCP, source_for("start_cleaning_task"))

    def test_unregistered_source_defaults_local(self):
        self.assertEqual(DEFAULT_SOURCE, source_for("no_such_tool"))

    def test_tools_by_source(self):
        self.assertEqual(("quick_clean_demo",), tools_by_source(SOURCE_LOCAL))
        self.assertEqual(set(mcp_tools.remote_tool_names()),
                         set(tools_by_source(SOURCE_MCP)))

    def test_source_metadata_is_tool_level(self):
        """「来源」是工具级元数据；与 score_task 的评分来源参数同名但不同层。"""
        for name, spec in TOOL_SPECS.items():
            with self.subTest(tool=name):
                # 工具级 source 是 parameters 的兄弟键，取值为 local/mcp
                self.assertIn(spec["source"], (SOURCE_LOCAL, SOURCE_MCP))
                self.assertIn("parameters", spec)
        # score_task 的参数 source 指的是评分来源，取值是 raw/published/task
        scored = TOOL_SPECS["score_task"]["parameters"]["properties"]["source"]
        self.assertEqual(["raw", "published", "task"], scored["enum"])


class TestSchemaMatchesImplementation(unittest.TestCase):
    def test_properties_exactly_match_signature(self):
        """本机来源工具：schema 参数 == 实现形参（双向）。远端来源不在此层校验。"""
        for name, fn_name in SPEC_TO_FUNCTION.items():
            if source_for(name) != SOURCE_LOCAL:
                continue
            with self.subTest(tool=name):
                fn = getattr(tools, fn_name)
                params = set(inspect.signature(fn).parameters)
                props = set(TOOL_SPECS[name]["parameters"]["properties"])
                self.assertEqual(params, props,
                                 "%s: 实现形参 %s != schema 参数 %s"
                                 % (name, sorted(params), sorted(props)))

    def test_required_subset_and_object_shape(self):
        for name, spec in TOOL_SPECS.items():
            with self.subTest(tool=name):
                p = spec["parameters"]
                self.assertEqual("object", p["type"])
                self.assertTrue(set(p["required"]) <= set(p["properties"]))

    def test_schema_still_reachable(self):
        self.assertIsNotNone(schema_for("start_cleaning_task"))


if __name__ == "__main__":
    unittest.main()
