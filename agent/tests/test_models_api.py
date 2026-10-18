# -*- coding: utf-8 -*-
"""契约 §3.7「列模型 / 取模型」的 Agent 落地层与 HTTP 端点。

`agent/ml_models.py` 是 `list_ml_models` / `get_ml_model` 的**薄封装 + 产物地址拼装**：
只调远端、只映射字段，不产生任何业务数字。用**假远端客户端**回罐头信封，
断言出口形状、错误码原样保留、无客户端时结构化 `MCP_UNAVAILABLE`。

HTTP 端点用标准库 `http.client` 起真服务（`ThreadingHTTPServer`），注入假运行时；
**不连网、不碰 127.0.0.1:8900**。
"""
import http.client
import json
import os
import sys
import threading
import unittest

import pytest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api, ml_models, resources  # noqa: E402
from agent.mcp_contract import NullMcpClient  # noqa: E402

MODEL_ID = "ml-20261018-103000-a1b2"


class FakeMcpClient(object):
    """假远端客户端：按工具名回罐头信封，并记下每次调用的参数。"""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        handler = self.responses.get(name)
        if handler is None:
            return {"ok": False, "error": {
                "code": "MCP_UNAVAILABLE",
                "message": "本测试没有为 %s 准备罐头信封" % name}}
        return handler(args or {}) if callable(handler) else handler


# ---------------------------------------------------------------------------
# 模块层：出口形状 / 错误码透传 / 无客户端 / 地址拼装
# ---------------------------------------------------------------------------

LIST_MODELS = [
    {"model_id": MODEL_ID, "task_type": "classification",
     "algorithm": "random_forest", "data_version": "dv-20261018",
     "created_at": "2026-10-18T10:30:00", "metrics_summary": {"f1": 0.81, "auc": 0.9}},
]

FULL_MODEL = {
    "model_id": MODEL_ID, "task_type": "classification", "algorithm": "random_forest",
    "data_version": "dv-20261018", "created_at": "2026-10-18T10:30:00",
    "metrics": {"f1": 0.81}, "params": {"n_estimators": 120},
    "time_boundaries": {"train_end": "2026-09-30"},
    "artifacts": [{"id": "%s:model.joblib" % MODEL_ID, "title": "模型本体",
                   "kind": "joblib", "role": "model", "path": "model.joblib",
                   "size": 1024}],
}


def test_list_models_returns_models_shape():
    client = FakeMcpClient({"list_ml_models": {"ok": True, "models": LIST_MODELS}})
    out = ml_models.list_models(client)
    assert out == {"models": LIST_MODELS}
    assert client.calls == [("list_ml_models", {})]


def test_get_model_returns_model_shape():
    client = FakeMcpClient({"get_ml_model": {"ok": True, "model": FULL_MODEL}})
    out = ml_models.get_model(client, MODEL_ID)
    assert out == {"model": FULL_MODEL}
    assert client.calls == [("get_ml_model", {"model_id": MODEL_ID})]


def test_list_models_passes_through_error_code_and_details():
    client = FakeMcpClient({"list_ml_models": {"ok": False, "error": {
        "code": "DRIVER_UNREACHABLE", "message": "连不上", "reason_kind": "network"}}})
    with pytest.raises(resources.ResourceError) as excinfo:
        ml_models.list_models(client)
    assert excinfo.value.code == "DRIVER_UNREACHABLE"
    assert excinfo.value.envelope()["error"]["reason_kind"] == "network"


def test_get_model_preserves_model_not_found():
    client = FakeMcpClient({"get_ml_model": {"ok": False, "error": {
        "code": "MODEL_NOT_FOUND", "message": "模型不存在"}}})
    with pytest.raises(resources.ResourceError) as excinfo:
        ml_models.get_model(client, "ml-missing")
    assert excinfo.value.code == "MODEL_NOT_FOUND"


def test_none_client_reports_mcp_unavailable_without_calling():
    with pytest.raises(resources.ResourceError) as excinfo:
        ml_models.list_models(None)
    assert excinfo.value.code == "MCP_UNAVAILABLE"
    with pytest.raises(resources.ResourceError) as excinfo2:
        ml_models.get_model(None, MODEL_ID)
    assert excinfo2.value.code == "MCP_UNAVAILABLE"


# ---------------------------------------------------------------------------
# HTTP 端点：真起服务 + 假运行时（不连网）
# ---------------------------------------------------------------------------

class StubRuntime(object):
    """只暴露模型端点需要的 `mcp_client`。"""

    def __init__(self, client):
        self.mcp_client = client


class ModelsApiTest(unittest.TestCase):
    def setUp(self):
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))
        self.mcp = FakeMcpClient()
        self.port = self._start(StubRuntime(self.mcp))

    def _start(self, runtime):
        srv = http_api.create_server("127.0.0.1", 0, runtime=runtime)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop, srv, thread)
        return srv.server_address[1]

    def _stop(self, srv, thread):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def get_json(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8"))
        finally:
            conn.close()

    def test_list_models_endpoint_returns_models(self):
        self.mcp.responses["list_ml_models"] = {"ok": True, "models": LIST_MODELS}
        status, payload = self.get_json("/api/models")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["models"], LIST_MODELS)
        self.assertIn(("list_ml_models", {}), self.mcp.calls)

    def test_get_model_endpoint_returns_model(self):
        self.mcp.responses["get_ml_model"] = {"ok": True, "model": FULL_MODEL}
        status, payload = self.get_json("/api/models/%s" % MODEL_ID)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model"], FULL_MODEL)
        self.assertIn(("get_ml_model", {"model_id": MODEL_ID}), self.mcp.calls)

    def test_get_model_error_code_preserved(self):
        self.mcp.responses["get_ml_model"] = {"ok": False, "error": {
            "code": "MODEL_NOT_FOUND", "message": "模型不存在",
            "model_id": "ml-missing"}}
        status, payload = self.get_json("/api/models/ml-missing")
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "MODEL_NOT_FOUND")
        self.assertEqual(payload["error"]["model_id"], "ml-missing")


class ModelsApiUnavailableTest(unittest.TestCase):
    def test_null_client_reports_mcp_unavailable(self):
        saved = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", saved))
        srv = http_api.create_server("127.0.0.1", 0,
                                     runtime=StubRuntime(NullMcpClient()))
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1],
                                              timeout=20)
            try:
                conn.request("GET", "/api/models")
                resp = conn.getresponse()
                payload = json.loads(resp.read().decode("utf-8"))
            finally:
                conn.close()
            self.assertEqual(resp.status, 200)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "MCP_UNAVAILABLE")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
