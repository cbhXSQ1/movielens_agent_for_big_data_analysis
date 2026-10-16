# -*- coding: utf-8 -*-
"""阶段 8（ag-mcp）· T8.1 / T8.2 / T8.3：MCP 接缝预留测试。

全部用**假 MCP 客户端 / 假后端**驱动：不连网、不起 driver、不触模型。覆盖：
  * T8.1 执行后端抽象：LocalToolBackend / McpToolBackend / RoutingBackend 同一接口；
  * T8.2 来源字段贯通执行选择 + driver 退出码/传输错误 → 结构化错误码（语义无损）；
  * T8.3 MCP 客户端接口形状（发现 / 调用 / 错误 / 完成通知 / 版本协商）与占位实现；
  * 可插拔：注册假 MCP 后端后，mcp 来源工具走它、local 来源仍走本地，上层不改。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_contract, tool_specs  # noqa: E402
from agent.runtime import (LocalToolBackend, McpToolBackend,  # noqa: E402
                           RoutingBackend, Runtime, ToolBackend,
                           build_tool_backend)


# ---------------------------------------------------------------------------
# 脚手架：假 MCP 客户端 / 记录型本地后端 / 临时 mcp 工具
# ---------------------------------------------------------------------------

class FakeMcpClient(mcp_contract.McpClient):
    """假 MCP 客户端：按名返回预置信封，记录调用，供可插拔验证。"""

    interface_version = "1.0"

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []
        self.subscriptions = []

    def list_tools(self):
        return [{"name": name, "source": "mcp", "description": "假 MCP 工具",
                 "parameters": []} for name in self.responses]

    def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        envelope = self.responses.get(name)
        if envelope is None:
            return {"ok": False, "error": {
                "code": "TASK_NOT_FOUND", "message": "假客户端未登记 %s" % name}}
        return envelope

    def get_task_status(self, task_id):
        return {"ok": True, "task_id": task_id, "status": "running"}

    def subscribe_task_done(self, callback, task_id=None):
        handle = ("sub", task_id)
        self.subscriptions.append((handle, callback))
        return handle

    def unsubscribe_task_done(self, handle):
        for item in list(self.subscriptions):
            if item[0] == handle:
                self.subscriptions.remove(item)
                return True
        return False


class RecordingLocalBackend(ToolBackend):
    """记录型「本地」后端：证明 local 来源工具仍走本地（不触 driver）。"""

    source = tool_specs.SOURCE_LOCAL

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def execute(self, name, args):
        self.calls.append((name, dict(args or {})))
        envelope = self.responses.get(name) or {"ok": True, "echo": name}
        return bool(envelope.get("ok")), envelope


class McpToolMixin(object):
    """在工具表里临时登记一个 source=mcp 的假工具，测试后还原。"""

    MCP_TOOL = "mcp_echo"

    def setUp(self):
        self._saved = tool_specs.TOOL_SPECS.get(self.MCP_TOOL)
        tool_specs.TOOL_SPECS[self.MCP_TOOL] = {
            "name": self.MCP_TOOL,
            "source": tool_specs.SOURCE_MCP,
            "description": "假 MCP 工具（测试用）",
            "parameters": {"type": "object",
                           "properties": {"text": {"type": "string"}},
                           "required": []},
        }
        self.addCleanup(self._restore_tool)

    def _restore_tool(self):
        if self._saved is None:
            tool_specs.TOOL_SPECS.pop(self.MCP_TOOL, None)
        else:
            tool_specs.TOOL_SPECS[self.MCP_TOOL] = self._saved


# ---------------------------------------------------------------------------
# T8.1 执行后端抽象
# ---------------------------------------------------------------------------

class TestBackendAbstraction(unittest.TestCase):
    def test_local_and_mcp_are_tool_backends(self):
        self.assertIsInstance(LocalToolBackend(), ToolBackend)
        self.assertIsInstance(McpToolBackend(), ToolBackend)
        self.assertIsInstance(
            RoutingBackend(LocalToolBackend(), McpToolBackend()), ToolBackend)

    def test_long_task_decorator_is_a_tool_backend(self):
        from agent.runtime import LongTaskBackend
        wrapped = LongTaskBackend(LocalToolBackend(), None, "c0001")
        self.assertIsInstance(wrapped, ToolBackend)

    def test_build_tool_backend_routes_local_and_mcp(self):
        backend = build_tool_backend()
        self.assertIsInstance(backend, RoutingBackend)
        self.assertIsInstance(backend.local_backend, LocalToolBackend)
        self.assertIsInstance(backend.mcp_backend, McpToolBackend)

    def test_all_shipped_tools_are_local_so_default_matches_old_behaviour(self):
        # 现有 11 个工具全为 local：默认路由后端把它们全交给本地后端。
        backend = build_tool_backend()
        for name in tool_specs.tool_names():
            self.assertIs(backend.backend_for(name), backend.local_backend, name)


# ---------------------------------------------------------------------------
# T8.2 来源字段贯通到执行选择（可插拔验证）
# ---------------------------------------------------------------------------

class TestSourceRouting(McpToolMixin, unittest.TestCase):
    def _routing(self):
        self.local = RecordingLocalBackend(responses={"list_schemes": {"ok": True}})
        self.fake = FakeMcpClient(responses={self.MCP_TOOL: {
            "ok": True, "task_id": "hadoop-1", "note": "来自 MCP"}})
        return RoutingBackend(self.local, McpToolBackend(self.fake))

    def test_mcp_sourced_tool_goes_to_mcp_backend(self):
        backend = self._routing()
        ok, envelope = backend.execute(self.MCP_TOOL, {"text": "hi"})
        self.assertTrue(ok)
        self.assertEqual(envelope["task_id"], "hadoop-1")
        self.assertEqual(self.fake.calls, [(self.MCP_TOOL, {"text": "hi"})])
        self.assertEqual(self.local.calls, [])        # 没走本地

    def test_local_sourced_tool_still_goes_to_local_backend(self):
        backend = self._routing()
        ok, envelope = backend.execute("list_schemes", {})
        self.assertTrue(ok)
        self.assertEqual(self.local.calls, [("list_schemes", {})])
        self.assertEqual(self.fake.calls, [])         # 没走 MCP

    def test_source_field_is_the_only_selector(self):
        backend = self._routing()
        self.assertIs(backend.backend_for(self.MCP_TOOL), backend.mcp_backend)
        self.assertIs(backend.backend_for("list_schemes"), backend.local_backend)

    def test_source_resolver_is_injectable(self):
        local = RecordingLocalBackend()
        fake = FakeMcpClient(responses={"anything": {"ok": True}})
        backend = RoutingBackend(local, McpToolBackend(fake),
                                 source_resolver=lambda name: "mcp")
        ok, _ = backend.execute("anything", {})
        self.assertTrue(ok)
        self.assertEqual(fake.calls, [("anything", {})])


# ---------------------------------------------------------------------------
# T8.2 错误码映射（逐条实测）
# ---------------------------------------------------------------------------

class TestErrorCodeMapping(unittest.TestCase):
    def test_exit_code_to_structured_code_table(self):
        expected = {
            0: None,                                  # 成功 → 无错误
            2: "USAGE",                               # 参数非法
            3: "TASK_FAILED",                         # 任务失败
            4: "TASK_NOT_FOUND",                      # 不存在
            5: "TASK_NOT_FINISHED",                   # 未完成
            6: "VERSION_CONFLICT",                    # 版本冲突
        }
        observed = {code: mcp_contract.code_for_exit(code) for code in expected}
        self.assertEqual(observed, expected)          # 列表形式证据
        self.assertIsNone(mcp_contract.code_for_exit(99))   # 未知不臆造

    def test_transport_code_mapping_identity(self):
        for raw, mapped in (
                ("DRIVER_NO_OUTPUT", "DRIVER_NO_OUTPUT"),
                ("DRIVER_BAD_OUTPUT", "DRIVER_BAD_OUTPUT"),
                ("DRIVER_UNREACHABLE", "DRIVER_UNREACHABLE"),
                ("DRIVER_TIMEOUT", "DRIVER_TIMEOUT")):
            self.assertEqual(mcp_contract.code_for_transport(raw), mapped)
        self.assertEqual(mcp_contract.code_for_transport("WEIRD"), "WEIRD")

    def test_new_structured_codes_exist(self):
        self.assertIn("DEPENDENCY_MISSING", mcp_contract.STRUCTURED_ERROR_CODES)
        self.assertIn("TASK_ALREADY_RUNNING", mcp_contract.STRUCTURED_ERROR_CODES)

    def test_normalize_error_maps_when_only_exit_code_present(self):
        out = mcp_contract.normalize_error({"message": "参数错误"}, exit_code=2)
        self.assertEqual(out["code"], "USAGE")
        self.assertEqual(out["message"], "参数错误")     # 说明保留

    def test_normalize_error_keeps_structured_code_verbatim(self):
        for code in ("VERSION_CONFLICT", "TASK_NOT_FINISHED",
                     "DEPENDENCY_MISSING", "TASK_ALREADY_RUNNING"):
            out = mcp_contract.normalize_error({"code": code, "message": "m"})
            self.assertEqual(out["code"], code)

    def test_normalize_error_keeps_driver_native_code(self):
        out = mcp_contract.normalize_error({"code": "CONFIG_INVALID",
                                            "message": "配置非法"}, exit_code=2)
        self.assertEqual(out["code"], "CONFIG_INVALID")   # 不降级成 USAGE

    def test_normalize_error_preserves_message_and_extra_semantics(self):
        err = {"code": "SOMETHING_WEIRD", "message": "细节", "hint": "补救提示",
               "task_id": "t1"}
        out = mcp_contract.normalize_error(err, exit_code=3)
        self.assertEqual(out["code"], "TASK_FAILED")
        self.assertEqual(out["driver_code"], "SOMETHING_WEIRD")   # 原码不丢
        self.assertEqual(out["message"], "细节")
        self.assertEqual(out["hint"], "补救提示")
        self.assertEqual(out["task_id"], "t1")

    def test_normalize_error_unknown_without_hint_is_kept(self):
        out = mcp_contract.normalize_error({"code": "X_UNKNOWN", "message": "m"})
        self.assertEqual(out["code"], "X_UNKNOWN")        # 无语义损失

    def test_normalize_result_success(self):
        ok, env = mcp_contract.normalize_result({"ok": True, "task_id": "t"})
        self.assertTrue(ok)
        self.assertEqual(env["task_id"], "t")

    def test_normalize_result_failure_maps_exit_code(self):
        ok, env = mcp_contract.normalize_result(
            {"ok": False, "error": {"message": "没了"}, "_exit_code": 4})
        self.assertFalse(ok)
        self.assertEqual(env["error"]["code"], "TASK_NOT_FOUND")


# ---------------------------------------------------------------------------
# T8.1 / T8.2 MCP 占位后端
# ---------------------------------------------------------------------------

class TestMcpBackend(unittest.TestCase):
    def test_null_client_returns_structured_unavailable(self):
        ok, env = McpToolBackend().execute("some_tool", {})
        self.assertFalse(ok)
        self.assertEqual(env["error"]["code"], "MCP_UNAVAILABLE")
        self.assertIn("some_tool", env["error"]["message"])

    def test_fake_client_envelope_passes_through(self):
        fake = FakeMcpClient(responses={"t": {"ok": True, "value": 7}})
        ok, env = McpToolBackend(fake).execute("t", {"a": 1})
        self.assertTrue(ok)
        self.assertEqual(env["value"], 7)
        self.assertEqual(fake.calls, [("t", {"a": 1})])

    def test_client_exception_maps_to_unreachable(self):
        class Boom(mcp_contract.McpClient):
            def call_tool(self, name, args):
                raise RuntimeError("网络断了")

        ok, env = McpToolBackend(Boom()).execute("t", {})
        self.assertFalse(ok)
        self.assertEqual(env["error"]["code"], "DRIVER_UNREACHABLE")
        self.assertIn("网络断了", env["error"]["message"])

    def test_backend_normalizes_client_driver_exit_code(self):
        fake = FakeMcpClient(responses={
            "t": {"ok": False, "error": {"message": "任务失败"}, "_exit_code": 3}})
        ok, env = McpToolBackend(fake).execute("t", {})
        self.assertFalse(ok)
        self.assertEqual(env["error"]["code"], "TASK_FAILED")


# ---------------------------------------------------------------------------
# T8.3 MCP 客户端接口预留
# ---------------------------------------------------------------------------

class TestMcpClientInterface(unittest.TestCase):
    def test_interface_methods_present(self):
        for method in ("list_tools", "call_tool", "get_task_status",
                       "subscribe_task_done", "unsubscribe_task_done",
                       "negotiate_version", "close"):
            self.assertTrue(callable(getattr(mcp_contract.McpClient, method)),
                            method)

    def test_interface_version_is_declared(self):
        self.assertTrue(mcp_contract.MCP_INTERFACE_VERSION)
        self.assertEqual(mcp_contract.McpClient.interface_version,
                         mcp_contract.MCP_INTERFACE_VERSION)
        self.assertEqual(mcp_contract.NullMcpClient().interface_version,
                         mcp_contract.MCP_INTERFACE_VERSION)

    def test_null_client_is_offline_and_structured(self):
        client = mcp_contract.NullMcpClient()
        self.assertEqual(client.list_tools(), [])
        env = client.call_tool("t", {})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "MCP_UNAVAILABLE")
        self.assertIsNone(client.subscribe_task_done(lambda *a: None))
        self.assertIsNone(client.close())

    def test_fake_client_completion_notification_seam(self):
        fake = FakeMcpClient()
        events = []
        handle = fake.subscribe_task_done(lambda tid: events.append(tid),
                                          task_id="t1")
        self.assertIsNotNone(handle)
        self.assertEqual(len(fake.subscriptions), 1)
        self.assertTrue(fake.unsubscribe_task_done(handle))

    def test_version_negotiation_additive_compatible(self):
        same = mcp_contract.negotiate("1.4")
        self.assertTrue(same["compatible"])              # 同主版本 → 加性兼容
        diff = mcp_contract.negotiate("2.0")
        self.assertFalse(diff["compatible"])             # 主版本不同 → 不兼容
        self.assertTrue(same["additive_only"])

    def test_negotiate_via_client(self):
        client = FakeMcpClient()
        self.assertTrue(client.negotiate_version("1.9")["compatible"])


# ---------------------------------------------------------------------------
# 可插拔：上层（Runtime）不改，换后端即可切到 MCP
# ---------------------------------------------------------------------------

class TestRuntimeBackendPluggable(McpToolMixin, unittest.TestCase):
    def setUp(self):
        super(TestRuntimeBackendPluggable, self).setUp()
        self.tmp = tempfile.mkdtemp(prefix="stage8_mcp_")
        self.provider_calls = {"n": 0}

        def forbidden_factory():
            self.provider_calls["n"] += 1
            raise AssertionError("离线直调不应触达模型层")

        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"),
                          provider_factory=forbidden_factory)
        self.addCleanup(self.rt.close)
        self.cid = self.rt.create_conversation(title="MCP 接缝")

    def test_runtime_default_backend_is_routing(self):
        self.assertIsInstance(self.rt._tool_backend, RoutingBackend)

    def test_runtime_offline_runs_mcp_tool_through_injected_fake(self):
        """换后端不改上层：run_offline 走同一个 execute 接口，mcp 工具经假后端执行。"""
        fake = FakeMcpClient(responses={self.MCP_TOOL: {
            "ok": True, "task_id": "hadoop-42", "note": "MCP 结果"}})
        local = RecordingLocalBackend()
        backend = RoutingBackend(local, McpToolBackend(fake))
        result = self.rt.run_offline(
            self.cid, self.MCP_TOOL, {"text": "go"},
            tool_backend=backend, enforce_policy=False)
        self.assertTrue(result["ok"])
        self.assertEqual(fake.calls, [(self.MCP_TOOL, {"text": "go"})])
        self.assertEqual(local.calls, [])
        self.assertEqual(self.provider_calls["n"], 0)     # 全程不触模型


if __name__ == "__main__":
    unittest.main()
