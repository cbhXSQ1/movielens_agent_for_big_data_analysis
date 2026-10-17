# -*- coding: utf-8 -*-
"""远端工具表单测：22 项远端能力改判 source=mcp，本机仅留快速演示。

来源权威：`hadoop/mcpserver/tools.py` 的 `TOOLS`（能力自描述）。本表是 Agent
侧的权威形状；参数名/默认值/枚举/取值范围必须与远端一致。
"""
import json
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_http_client as mc  # noqa: E402
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


class _CapsSession:
    """假会话：`describe_capabilities` 返回预置信封（不连网）。"""

    def __init__(self, envelope):
        self._envelope = envelope
        self.calls = []

    async def initialize(self):
        return None

    async def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))

        class _C:
            def __init__(self, text):
                self.text = text

        return type("R", (), {"content": [_C(json.dumps(self._envelope))]})()


def _caps_envelope(names, version="1.0"):
    """仿远端 `describe_capabilities` 信封：`tools` 为公开条目列表。"""
    return {"ok": True, "interface_version": version,
            "tools": [{"name": name} for name in names],
            "declared_count": len(list(names))}


def _client_with(envelope, url="http://127.0.0.1:8900/mcp", token="t" * 20):
    cfg = mc.McpConfig({"url": url, "token": token})
    return mc.McpHttpClient(cfg, session_factory=lambda: _CapsSession(envelope))


class TestVerifyTools(unittest.TestCase):
    """启动时「工具表 ↔ 能力自描述」一致性校验（Task 4）。"""

    def test_exact_match_is_ok(self):
        names = mcp_tools.remote_tool_names()
        out = _client_with(_caps_envelope(names)).verify_tools(names)
        self.assertTrue(out["ok"])
        self.assertEqual([], out["missing"])
        self.assertEqual([], out["extra"])
        self.assertTrue(out["version_ok"])
        self.assertEqual("1.0", out["peer_version"])

    def test_declared_but_absent_on_server_is_missing(self):
        server = list(mcp_tools.remote_tool_names())
        declared = set(server) | {"declared_only_tool"}    # 多声明了一个远端没有的
        out = _client_with(_caps_envelope(server)).verify_tools(declared)
        self.assertFalse(out["ok"])
        self.assertIn("declared_only_tool", out["missing"])
        self.assertEqual([], out["extra"])

    def test_on_server_but_not_declared_is_extra(self):
        server = list(mcp_tools.remote_tool_names())
        declared = set(server) - {"cancel_task"}          # 少声明了一个
        out = _client_with(_caps_envelope(server)).verify_tools(declared)
        self.assertFalse(out["ok"])
        self.assertIn("cancel_task", out["extra"])
        self.assertEqual([], out["missing"])

    def test_same_major_version_is_ok_even_if_minor_differs(self):
        names = mcp_tools.remote_tool_names()
        out = _client_with(_caps_envelope(names, version="1.7")).verify_tools(names)
        self.assertTrue(out["version_ok"])
        self.assertTrue(out["ok"])
        self.assertEqual("1.7", out["peer_version"])

    def test_major_version_mismatch_is_not_ok(self):
        names = mcp_tools.remote_tool_names()
        out = _client_with(_caps_envelope(names, version="2.0")).verify_tools(names)
        self.assertFalse(out["version_ok"])
        self.assertFalse(out["ok"])
        self.assertEqual("2.0", out["peer_version"])

    def test_server_failure_envelope_explains_and_does_not_raise(self):
        client = _client_with({"ok": False, "interface_version": "1.0",
                               "error": {"code": "INTERNAL_ERROR",
                                         "message": "远端内部错误"}})
        out = client.verify_tools(mcp_tools.remote_tool_names())
        self.assertFalse(out["ok"])
        self.assertIn("远端内部错误", out["reason"])

    def test_unusable_config_is_not_ok_without_networking(self):
        client = mc.McpHttpClient(mc.McpConfig({}))
        out = client.verify_tools(mcp_tools.remote_tool_names())
        self.assertFalse(out["ok"])
        self.assertIn("reason", out)
        self.assertEqual([], out["missing"])
        self.assertEqual([], out["extra"])


class TestStartupToolCheck(unittest.TestCase):
    """启动装配点的非阻断校验（Task 4）：结果按后端实例持有（非模块级单槽）。"""

    def test_build_backend_records_verification(self):
        from agent.runtime import tool_exec

        class _Client:
            def verify_tools(self, declared):
                return {"ok": True, "missing": [], "extra": [],
                        "version_ok": True, "peer_version": "1.0"}

        backend = tool_exec.build_tool_backend(_Client())
        out = tool_exec.wait_for_startup_tool_check(backend, timeout=5)
        self.assertTrue(out["ok"])
        self.assertTrue(tool_exec.startup_tool_check(backend)["ok"])

    def test_build_backend_is_non_blocking_on_failure(self):
        from agent.runtime import tool_exec

        class _Boom:
            def verify_tools(self, declared):
                raise RuntimeError("server absent")

        backend = tool_exec.build_tool_backend(_Boom())   # 装配照常返回，不抛
        self.assertIsNotNone(backend)
        out = tool_exec.wait_for_startup_tool_check(backend, timeout=5)
        self.assertFalse(out["ok"])
        self.assertIn("reason", out)

    def test_startup_check_is_owned_per_backend(self):
        from agent.runtime import tool_exec

        class _Client:
            def __init__(self, ok):
                self._ok = ok

            def verify_tools(self, declared):
                return {"ok": self._ok, "missing": [], "extra": [],
                        "version_ok": True, "peer_version": "1.0"}

        first = tool_exec.build_tool_backend(_Client(True))
        second = tool_exec.build_tool_backend(_Client(False))
        self.assertTrue(
            tool_exec.wait_for_startup_tool_check(first, timeout=5)["ok"])
        self.assertFalse(
            tool_exec.wait_for_startup_tool_check(second, timeout=5)["ok"])
        # 后装配的客户端不覆盖先装配的结果
        self.assertTrue(tool_exec.startup_tool_check(first)["ok"])
        self.assertFalse(tool_exec.startup_tool_check(second)["ok"])

    def test_no_client_has_no_holder(self):
        from agent.runtime import tool_exec

        backend = tool_exec.build_tool_backend()
        self.assertIsNone(tool_exec.startup_tool_check(backend))
        self.assertIsNone(
            tool_exec.wait_for_startup_tool_check(backend, timeout=0))

    def test_no_module_level_mutable_slot(self):
        from agent.runtime import tool_exec

        self.assertFalse(hasattr(tool_exec, "_STARTUP_TOOL_CHECK"))
        self.assertFalse(hasattr(tool_exec, "_STARTUP_TOOL_CHECK_THREAD"))

    def test_verify_declared_tools_survives_remote_names_error(self):
        from agent.runtime import tool_exec

        class _Client:
            def verify_tools(self, declared):
                return {"ok": True}

        original = mcp_tools.remote_tool_names
        mcp_tools.remote_tool_names = lambda: (_ for _ in ()).throw(
            RuntimeError("远端工具表不可用"))
        try:
            out = tool_exec.verify_declared_tools(_Client())
        finally:
            mcp_tools.remote_tool_names = original
        self.assertFalse(out["ok"])
        self.assertIn("reason", out)


if __name__ == "__main__":
    unittest.main()
