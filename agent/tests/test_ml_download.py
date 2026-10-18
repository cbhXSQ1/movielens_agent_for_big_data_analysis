# -*- coding: utf-8 -*-
"""模型下载路由的带口令流式取回（`agent/mcp_download.open_model_stream` + http_api 中转）。

全部用 `httpx.MockTransport` 注入：**不连网、不碰 127.0.0.1:8900**。
覆盖：成功透传字节 + 下载头、多级 `relpath` 保留斜杠、结构化错误码原样保留、
`model_id` 形状不合法回 `USAGE`、越界 `relpath` 本机先拒（不发请求）、
口令只进请求头（不外泄）；以及 `http_api` 的 `/api/models/download` 中转处理。
"""
import http.client
import json
import os
import sys
import threading
import unittest

import httpx

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api  # noqa: E402
from agent import mcp_download as dl  # noqa: E402
from agent.mcp_config import McpConfig  # noqa: E402
from agent.mcp_contract import NullMcpClient  # noqa: E402

TOKEN = "t" * 20
MODEL_ID = "ml-20261018-103000-a1b2"


def cfg(transport=None):
    c = McpConfig({"url": "http://hadoop:8765/mcp", "token": TOKEN})
    if transport is not None:
        c.transport = transport                      # 仅测试注入：不连网
    return c


# ---------------------------------------------------------------------------
# 模块层：URL / 字节 / 下载头 / 错误码 / 本机守卫
# ---------------------------------------------------------------------------

def test_open_model_stream_relays_bytes_and_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200, content=b"model-bytes",
            headers={"content-disposition": 'attachment; filename="model.joblib"',
                     "content-length": "11"})

    it, headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "model.joblib",
        transport=httpx.MockTransport(handler))
    assert err is None
    assert b"".join(it) == b"model-bytes"
    assert "model.joblib" in headers.get("Content-Disposition", "")
    assert headers["Content-Length"] == "11"
    assert seen["auth"] == "Bearer " + TOKEN
    assert seen["url"] == (
        "http://hadoop:8765/download/model/%s/model.joblib" % MODEL_ID)


def test_open_model_stream_keeps_multilevel_relpath_slashes():
    def handler(request):
        assert str(request.url) == (
            "http://hadoop:8765/download/model/%s/sub/coords.csv" % MODEL_ID)
        return httpx.Response(200, content=b"1,2,3")

    it, _headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "sub/coords.csv",
        transport=httpx.MockTransport(handler))
    assert err is None
    assert b"".join(it) == b"1,2,3"


def test_open_model_stream_returns_iterator_for_streaming():
    def handler(request):
        return httpx.Response(200, content=b"a" * 100)

    it, _headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "labels.csv", transport=httpx.MockTransport(handler))
    assert err is None
    assert hasattr(it, "__next__")
    assert not isinstance(it, (bytes, bytearray))
    assert b"".join(it) == b"a" * 100


def test_file_not_found_preserved():
    def handler(request):
        return httpx.Response(404, json={
            "ok": False,
            "error": {"code": "FILE_NOT_FOUND", "message": "文件不存在",
                      "reason_kind": "directory"}})

    it, headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "nope.json", transport=httpx.MockTransport(handler))
    assert it is None and headers is None
    assert err["code"] == "FILE_NOT_FOUND"
    assert err["reason_kind"] == "directory"
    assert dl.status_for_code(err["code"]) == 404


def test_forbidden_path_code_preserved_and_status_mapped():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(400, json={
            "ok": False,
            "error": {"code": "FORBIDDEN_PATH", "message": "路径越界",
                      "reason_kind": "sidecar"}})

    _it, _headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "meta.json", transport=httpx.MockTransport(handler))
    assert seen["url"].endswith("/download/model/%s/meta.json" % MODEL_ID)
    assert err["code"] == "FORBIDDEN_PATH"
    assert err["reason_kind"] == "sidecar"
    assert dl.status_for_code(err["code"]) == 400


def test_model_traversal_rejected_locally_without_request():
    def handler(request):
        raise AssertionError("越界路径不应发起任何请求")

    it, headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "../../etc/passwd",
        transport=httpx.MockTransport(handler))
    assert it is None and headers is None
    assert err == {"code": "FORBIDDEN_PATH", "message": "该路径不允许访问"}
    assert dl.status_for_code(err["code"]) == 400


def test_model_other_forbidden_paths_rejected_locally_without_request():
    def handler(request):
        raise AssertionError("非法路径不应发起任何请求")

    for bad in ("/etc/passwd", "a\\b", "a\x00b", "sub/../x", ".."):
        it, headers, err = dl.open_model_stream(
            cfg(), MODEL_ID, bad, transport=httpx.MockTransport(handler))
        assert it is None and headers is None
        assert err["code"] == "FORBIDDEN_PATH", bad


def test_model_id_with_slash_is_usage_without_request():
    def handler(request):
        raise AssertionError("模型编号不合法不应发起任何请求")

    it, headers, err = dl.open_model_stream(
        cfg(), "ml/1", "model.joblib", transport=httpx.MockTransport(handler))
    assert it is None and headers is None
    assert err["code"] == "USAGE"
    assert dl.status_for_code(err["code"]) == 400


def test_model_id_illegal_shapes_are_usage_without_request():
    def handler(request):
        raise AssertionError("模型编号不合法不应发起任何请求")

    for bad in ("", "a b", "ml\\1", "a\x00b", "..", "."):
        it, headers, err = dl.open_model_stream(
            cfg(), bad, "model.joblib", transport=httpx.MockTransport(handler))
        assert it is None and headers is None
        assert err["code"] == "USAGE", bad


def test_model_id_matching_contract_shape_is_allowed():
    """契约形状 `ml-YYYYMMDD-HHMMSS-xxxx`（字母数字与 `.-_`）放行。"""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"ok")

    _it, _headers, err = dl.open_model_stream(
        cfg(httpx.MockTransport(handler)), MODEL_ID, "meta.json")
    assert err is None
    assert "/download/model/%s/meta.json" % MODEL_ID in seen["url"]


def test_unusable_config_reports_unavailable_without_network():
    def handler(request):                            # 永不调用
        raise AssertionError("未接入时不应发起任何请求")

    it, headers, err = dl.open_model_stream(
        McpConfig({}), MODEL_ID, "model.joblib",
        transport=httpx.MockTransport(handler))
    assert it is None and headers is None
    assert err["code"] == "MCP_UNAVAILABLE"


def test_token_never_leaks_into_error():
    def handler(request):
        return httpx.Response(500, content=b"boom")

    _it, _headers, err = dl.open_model_stream(
        cfg(), MODEL_ID, "model.joblib", transport=httpx.MockTransport(handler))
    assert TOKEN not in json.dumps(err, ensure_ascii=False)
    assert "Bearer" not in json.dumps(err, ensure_ascii=False)


# ---------------------------------------------------------------------------
# http_api 中转处理：假客户端 + 假传输（真起服务，但不连远端）
# ---------------------------------------------------------------------------

class FakeClient(object):
    def __init__(self, config):
        self.config = config


class StubRuntime(object):
    def __init__(self, client):
        self.mcp_client = client


class RelayServerBase(unittest.TestCase):
    def setUp(self):
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))
        srv = http_api.create_server("127.0.0.1", 0, runtime=self._runtime())
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop, srv, thread)
        self.port = srv.server_address[1]

    def _runtime(self):                              # pragma: no cover - 子类覆盖
        raise NotImplementedError

    def _stop(self, srv, thread):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def request(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, dict(resp.getheaders()), raw
        finally:
            conn.close()


class ModelRelayHandlerTest(RelayServerBase):
    def _runtime(self):
        def handler(request):
            assert request.headers["Authorization"] == "Bearer " + TOKEN
            if "missing" in str(request.url):
                return httpx.Response(404, json={
                    "ok": False,
                    "error": {"code": "FILE_NOT_FOUND", "message": "文件不存在"}})
            return httpx.Response(
                200, content=b"model-bytes",
                headers={"content-disposition": 'attachment; filename="model.joblib"',
                         "content-length": "11"})

        self._config = cfg(httpx.MockTransport(handler))
        return StubRuntime(FakeClient(self._config))

    def test_relays_bytes_and_headers(self):
        status, headers, raw = self.request(
            "/api/models/download?model_id=%s&path=model.joblib" % MODEL_ID)
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"model-bytes")
        self.assertIn("model.joblib", headers.get("Content-Disposition", ""))
        self.assertEqual(headers.get("Content-Length"), "11")

    def test_error_returns_envelope_and_status(self):
        status, _headers, raw = self.request(
            "/api/models/download?model_id=%s&path=missing.json" % MODEL_ID)
        self.assertEqual(status, 404)
        body = json.loads(raw.decode("utf-8"))
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "FILE_NOT_FOUND")

    def test_traversal_path_rejected_locally(self):
        status, _headers, raw = self.request(
            "/api/models/download?model_id=%s&path=../../etc/passwd" % MODEL_ID)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw.decode("utf-8"))["error"]["code"],
                         "FORBIDDEN_PATH")


class ModelRelayUnconfiguredTest(RelayServerBase):
    def _runtime(self):
        return StubRuntime(NullMcpClient())

    def test_null_client_reports_mcp_unavailable(self):
        status, _headers, raw = self.request(
            "/api/models/download?model_id=%s&path=model.joblib" % MODEL_ID)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw.decode("utf-8"))["error"]["code"],
                         "MCP_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
