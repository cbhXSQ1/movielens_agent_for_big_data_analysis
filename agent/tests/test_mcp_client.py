# -*- coding: utf-8 -*-
"""真实 MCP 客户端（agent/mcp_http_client.py）单元测试。

全部走注入的 `session_factory` 接缝：不连网、不起服务、不触模型。
覆盖：列工具映射、调工具信封解析、非信封兜底、配置不可用、连接异常翻译、
口令绝不外泄、以及基类接口（版本协商 / 关闭）。
"""
import json
import os
import sys

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_contract  # noqa: E402
from agent import mcp_http_client as mc  # noqa: E402


def cfg(url="http://127.0.0.1:8900/mcp", token="t" * 20):
    return mc.McpConfig({"url": url, "token": token})


class FakeSession:
    """假会话：不连网，按预置结果返回，并记录调用。"""

    def __init__(self, tools=None, call_result=None, call_text=None):
        self._tools = tools or []
        self._call_result = call_result
        self._call_text = call_text
        self.calls = []

    async def initialize(self):
        return None

    async def list_tools(self):
        class _T:
            def __init__(self, name, schema):
                self.name = name
                self.title = name
                self.input_schema = schema

        return type("R", (), {"tools": [_T(n, s) for n, s in self._tools]})()

    async def call_tool(self, name, args):
        class _C:
            def __init__(self, text):
                self.text = text

        self.calls.append((name, dict(args or {})))
        text = (self._call_text if self._call_text is not None
                else json.dumps(self._call_result))
        return type("R", (), {"content": [_C(text)]})()


# ---------------------------------------------------------------------------
# 列工具
# ---------------------------------------------------------------------------

def test_list_tools_maps_to_rows():
    client = mc.McpHttpClient(cfg(), session_factory=lambda: FakeSession(
        tools=[("list_schemes", {"type": "object", "properties": {}, "required": []})]))
    rows = client.list_tools()
    assert rows[0]["name"] == "list_schemes"
    assert rows[0]["source"] == "mcp"
    assert rows[0]["parameters"]["type"] == "object"


def test_list_tools_unusable_returns_empty():
    client = mc.McpHttpClient(mc.McpConfig({}))
    assert client.list_tools() == []


# ---------------------------------------------------------------------------
# 调工具：信封解析
# ---------------------------------------------------------------------------

def test_call_tool_returns_envelope():
    client = mc.McpHttpClient(cfg(), session_factory=lambda: FakeSession(
        call_result={"ok": True, "interface_version": "1.0", "task_id": "T1"}))
    env = client.call_tool("get_task_status", {"task_id": "T1"})
    assert env["ok"] is True and env["task_id"] == "T1"


def test_call_tool_passes_name_and_args():
    session = FakeSession(call_result={"ok": True})
    client = mc.McpHttpClient(cfg(), session_factory=lambda: session)
    client.call_tool("get_samples", {"task_id": "H1", "limit": 3})
    assert session.calls == [("get_samples", {"task_id": "H1", "limit": 3})]


def test_call_tool_non_json_is_bad_output():
    client = mc.McpHttpClient(cfg(), session_factory=lambda: FakeSession(call_text="not json"))
    env = client.call_tool("x", {})
    assert env["ok"] is False
    assert env["error"]["code"] == mcp_contract.CODE_DRIVER_BAD_OUTPUT


def test_call_tool_non_object_is_bad_output():
    client = mc.McpHttpClient(cfg(), session_factory=lambda: FakeSession(call_text="[1, 2, 3]"))
    env = client.call_tool("x", {})
    assert env["ok"] is False
    assert env["error"]["code"] == mcp_contract.CODE_DRIVER_BAD_OUTPUT


# ---------------------------------------------------------------------------
# 配置不可用：一律结构化 MCP_UNAVAILABLE，绝不连网
# ---------------------------------------------------------------------------

def test_unusable_config_reports_unavailable():
    client = mc.McpHttpClient(mc.McpConfig({}))
    env = client.call_tool("list_schemes", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "MCP_UNAVAILABLE"


def test_unusable_config_status_describe_health_all_unavailable():
    client = mc.McpHttpClient(mc.McpConfig({}))
    for env in (client.get_task_status("T1"), client.describe_capabilities(),
                client.health()):
        assert env["ok"] is False
        assert env["error"]["code"] == "MCP_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 连接 / 协议异常：翻成可读说明，且绝不带口令
# ---------------------------------------------------------------------------

def test_connection_refused_is_explained_and_token_hidden():
    def boom():
        raise ConnectionRefusedError("connection refused")

    client = mc.McpHttpClient(cfg(token="super-secret-token"), session_factory=boom)
    env = client.call_tool("x", {})
    assert env["ok"] is False
    assert env["error"]["code"] == mcp_contract.CODE_DRIVER_UNREACHABLE
    assert "连不上" in env["error"]["message"]
    assert "super-secret-token" not in env["error"]["message"]


def test_http_401_is_explained_as_credential_problem():
    def boom():
        raise RuntimeError("unexpected status: 401 Unauthorized")

    client = mc.McpHttpClient(cfg(token="super-secret-token"), session_factory=boom)
    env = client.call_tool("x", {})
    assert env["error"]["code"] == mcp_contract.CODE_DRIVER_UNREACHABLE
    assert "凭据" in env["error"]["message"]
    assert "super-secret-token" not in env["error"]["message"]


def test_generic_error_is_explained():
    def boom():
        raise RuntimeError("some other protocol failure")

    client = mc.McpHttpClient(cfg(), session_factory=boom)
    env = client.call_tool("x", {})
    assert env["error"]["code"] == mcp_contract.CODE_DRIVER_UNREACHABLE
    assert "调用失败" in env["error"]["message"]


# ---------------------------------------------------------------------------
# 便捷方法
# ---------------------------------------------------------------------------

def test_get_task_status_uses_task_id():
    client = mc.McpHttpClient(cfg(), session_factory=lambda: FakeSession(
        call_result={"ok": True, "task_id": "H1", "status": "running"}))
    env = client.get_task_status("H1")
    assert env["task_id"] == "H1" and env["status"] == "running"


def test_describe_capabilities_calls_tool():
    client = mc.McpHttpClient(cfg(), session_factory=lambda: FakeSession(
        call_result={"ok": True, "tools": []}))
    assert client.describe_capabilities()["ok"] is True


# ---------------------------------------------------------------------------
# 基类契约：接口版本来自配置，版本协商 / 关闭沿用基类
# ---------------------------------------------------------------------------

def test_interface_version_from_config():
    c = mc.McpConfig({"url": "http://x/mcp", "token": "t" * 20,
                      "interface_version": "1.0"})
    assert mc.McpHttpClient(c).interface_version == "1.0"


def test_negotiate_version_and_close_inherited():
    client = mc.McpHttpClient(cfg())
    assert client.negotiate_version("1.9")["compatible"] is True
    assert client.close() is None
