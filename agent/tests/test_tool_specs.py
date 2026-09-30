# -*- coding: utf-8 -*-
"""第 5 节 Agent v2 阶段 1：工具表单测 —— schema 形状、参数边界与现有实现一致。

用 inspect 锁定：TOOL_SPECS 的每个工具，其 parameters.properties ⊆ 对应
tools 函数的形参名；枚举与 tools.py 常量同源；必填 ⊆ 属性；task 类工具
强制 task_id。
"""
import inspect
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import tools  # noqa: E402
from agent.tool_specs import TOOL_SPECS, SPEC_TO_FUNCTION, schema_for, tool_names  # noqa: E402


class TestSpecShape(unittest.TestCase):
    def test_all_tools_registered(self):
        self.assertEqual(tuple(tools.TOOL_NAMES), tool_names())
        self.assertEqual(11, len(tools.TOOL_NAMES))

    def test_schema_shape(self):
        """每条 schema：name/description/parameters(type=object, properties, required)。"""
        for name, spec in TOOL_SPECS.items():
            with self.subTest(tool=name):
                self.assertEqual(name, spec["name"])
                self.assertTrue(spec["description"])
                p = spec["parameters"]
                self.assertEqual("object", p["type"])
                self.assertIsInstance(p["properties"], dict)
                self.assertIsInstance(p["required"], list)
                self.assertTrue(set(p["required"]) <= set(p["properties"]))

    def test_properties_within_implementation_signature(self):
        """参数边界与 tools.py 实参对齐：schema 里出现的每个参数都能落到实现函数。"""
        for name, fn_name in SPEC_TO_FUNCTION.items():
            with self.subTest(tool=name):
                fn = getattr(tools, fn_name)
                params = set(inspect.signature(fn).parameters)
                props = set(TOOL_SPECS[name]["parameters"]["properties"])
                self.assertTrue(props <= params,
                                "%s: schema 参数 %s 超出实现形参 %s"
                                % (name, sorted(props - params), sorted(params)))

    def test_enums_aligned_with_tools_constants(self):
        start = TOOL_SPECS["start_cleaning_task"]["parameters"]["properties"]
        self.assertEqual(list(tools.SCOPES), start["scope"]["enum"])
        samples = TOOL_SPECS["get_samples"]["parameters"]["properties"]
        self.assertEqual(list(tools.SAMPLE_TYPES), samples["sample_type"]["enum"])
        self.assertEqual(list(tools.SAMPLE_TABLES), samples["table"]["enum"])
        report = TOOL_SPECS["get_report"]["parameters"]["properties"]
        self.assertEqual(["md", "json"], report["fmt"]["enum"])

    def test_task_tools_require_task_id(self):
        for name in ("get_task_status", "get_task_result", "get_samples", "get_report"):
            with self.subTest(tool=name):
                self.assertIn("task_id", TOOL_SPECS[name]["parameters"]["required"])

    def test_samples_n_bounds(self):
        n = TOOL_SPECS["get_samples"]["parameters"]["properties"]["n"]
        self.assertEqual(1, n["minimum"])
        self.assertEqual(200, n["maximum"])

    def test_v11_tools_promoted(self):
        """v1.1 精细任务工具已转正：工具表在册、schema 可拿、参数形状合法。"""
        from agent.tool_specs import PENDING_V11
        self.assertEqual((), PENDING_V11)
        for name in ("start_clean_task", "score_task"):
            with self.subTest(tool=name):
                spec = schema_for(name)
                self.assertIsNotNone(spec)
                self.assertEqual(name, spec["name"])
        sc = schema_for("score_task")
        self.assertIn("source", sc["parameters"]["required"])
        self.assertEqual(["raw", "published", "task"],
                         sc["parameters"]["properties"]["source"]["enum"])

    def test_schema_for_unknown_returns_none(self):
        self.assertIsNone(schema_for("no_such_tool"))


if __name__ == "__main__":
    unittest.main()