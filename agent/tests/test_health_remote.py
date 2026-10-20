# -*- coding: utf-8 -*-
"""/health 暴露远端连接状态（Task 13）的真实 HTTP 实测。

用标准库 `http.client` 起真服务（`ThreadingHTTPServer`），注入只暴露 `_tool_backend`
的桩运行时；断言：

  * 配了 `AGENT_MCP_URL` / `AGENT_MCP_TOKEN` 时，`/health` 的 `mcp` 段 `token_set` 为真，
    且**响应里绝不出现口令值**；
  * 两个变量都没配时，`mcp` 段如实报告「不可用」，服务**不崩**；
  * 注入的启动工具表一致性校验结果被带出（`ok/missing/extra/version_ok/peer_version`），
    没有持有者时为 null。

**不连网、不碰 127.0.0.1:8900。**
"""
import http.client
import json
import os
import sys
import threading
import unittest
from unittest import mock

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api  # noqa: E402
from agent.runtime import tool_exec  # noqa: E402

TOKEN = "secret-token-value-should-never-appear"


class _StubRuntime(object):
    """只暴露 /health 需要的 `_tool_backend`（其余端点本测试不涉及）。"""

    def __init__(self, tool_backend=None):
        self._tool_backend = tool_backend


class _FakeVerifyClient(object):
    def __init__(self, result):
        self._result = result

    def verify_tools(self, declared):
        return dict(self._result)


class HealthRemoteTest(unittest.TestCase):
    def setUp(self):
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))
        self._saved_env = {k: os.environ.get(k)
                           for k in ("AGENT_MCP_URL", "AGENT_MCP_TOKEN")}
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _start(self, runtime):
        srv = http_api.create_server("127.0.0.1", 0, runtime=runtime)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop, srv, thread)
        self.port = srv.server_address[1]

    def _stop(self, srv, thread):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def _health_raw(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, raw.decode("utf-8")
        finally:
            conn.close()

    def test_health_reports_mcp_and_hides_token(self):
        os.environ["AGENT_MCP_URL"] = "http://127.0.0.1:8900/mcp"
        os.environ["AGENT_MCP_TOKEN"] = TOKEN
        self._start(_StubRuntime())

        status, raw = self._health_raw()
        self.assertEqual(200, status)
        body = json.loads(raw)
        self.assertTrue(body["ok"])
        self.assertIn("mcp", body)
        self.assertTrue(body["mcp"]["token_set"])
        self.assertTrue(body["mcp"]["usable"])
        self.assertNotIn(TOKEN, raw)                 # 口令绝不外泄

    def test_health_mcp_unusable_without_env(self):
        os.environ.pop("AGENT_MCP_URL", None)
        os.environ.pop("AGENT_MCP_TOKEN", None)
        # 隔离本机配置：真实 config/mcp.settings.json 存在时也不该让本用例变成"可用"。
        with mock.patch("agent.mcp_config._read_file", return_value={}):
            self._start(_StubRuntime())
            status, raw = self._health_raw()

        self.assertEqual(200, status)
        body = json.loads(raw)
        self.assertTrue(body["ok"])                  # 未接入也照常可用，不崩
        self.assertIn("mcp", body)
        self.assertFalse(body["mcp"]["usable"])
        self.assertFalse(body["mcp"]["token_set"])

    def test_health_surfaces_startup_tool_check(self):
        os.environ.pop("AGENT_MCP_URL", None)
        os.environ.pop("AGENT_MCP_TOKEN", None)
        backend = tool_exec.build_tool_backend(_FakeVerifyClient({
            "ok": False, "missing": ["cancel_task"], "extra": [],
            "version_ok": True, "peer_version": "1.0"}))
        tool_exec.wait_for_startup_tool_check(backend, timeout=5)
        with mock.patch("agent.mcp_config._read_file", return_value={}):
            self._start(_StubRuntime(tool_backend=backend))
            _, raw = self._health_raw()
        check = json.loads(raw)["mcp"]["startup_check"]
        self.assertIsNotNone(check)
        self.assertFalse(check["ok"])
        self.assertEqual(["cancel_task"], check["missing"])
        self.assertEqual([], check["extra"])
        self.assertTrue(check["version_ok"])
        self.assertEqual("1.0", check["peer_version"])

    def test_health_startup_check_null_without_holder(self):
        os.environ.pop("AGENT_MCP_URL", None)
        os.environ.pop("AGENT_MCP_TOKEN", None)
        self._start(_StubRuntime())                  # 无后端 → 无持有者

        _, raw = self._health_raw()
        self.assertIsNone(json.loads(raw)["mcp"]["startup_check"])


if __name__ == "__main__":
    unittest.main()
