# -*- coding: utf-8 -*-
"""远端两段只读下载路由的带口令流式取回（`agent/mcp_download.py`）单元测试。

全部用 `httpx.MockTransport` 注入：**不连网、不碰 127.0.0.1:8900**。
覆盖：成功透传字节 + 下载头、结构化错误码原样保留、口令只进请求头（不外泄）、
未接入时结构化 `MCP_UNAVAILABLE`；以及 `http_api` 的中转处理（假客户端 + 假传输）。
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


def cfg(transport=None):
    c = McpConfig({"url": "http://hadoop:8765/mcp", "token": TOKEN})
    if transport is not None:
        c.transport = transport                      # 仅测试注入：不连网
    return c


# ---------------------------------------------------------------------------
# 模块层：URL / 字节 / 下载头 / 错误码
# ---------------------------------------------------------------------------

def test_open_task_stream_relays_bytes_and_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200, content=b"hello",
            headers={"content-disposition": 'attachment; filename="a.dat"',
                     "content-length": "5"})

    it, headers, err = dl.open_task_stream(
        cfg(), "H1", "cleaned/x/ratings.dat",
        transport=httpx.MockTransport(handler))
    assert err is None
    assert b"".join(it) == b"hello"
    assert "filename" in headers.get("Content-Disposition", "")
    assert headers["Content-Length"] == "5"
    assert seen["auth"] == "Bearer " + TOKEN
    assert seen["url"] == "http://hadoop:8765/download/task/H1/cleaned/x/ratings.dat"


def test_open_returns_iterator_for_streaming():
    """成功时给的是**迭代器**（流式转发），不是已经读进内存的 bytes。"""
    def handler(request):
        return httpx.Response(
            200, content=b"a" * 100,
            headers={"content-disposition": 'attachment; filename="x"'})

    it, _headers, err = dl.open_task_stream(cfg(), "H1", "x",
                                            transport=httpx.MockTransport(handler))
    assert err is None
    assert hasattr(it, "__next__")
    assert not isinstance(it, (bytes, bytearray))
    assert b"".join(it) == b"a" * 100


def test_open_task_stream_surfaces_structured_error():
    def handler(request):
        return httpx.Response(404, json={
            "ok": False,
            "error": {"code": "FILE_NOT_FOUND", "message": "文件不存在",
                      "path": "nope"}})

    it, headers, err = dl.open_task_stream(cfg(), "H1", "nope",
                                           transport=httpx.MockTransport(handler))
    assert it is None and headers is None
    assert err["code"] == "FILE_NOT_FOUND"
    assert err["message"] == "文件不存在"
    assert err["path"] == "nope"                     # 远端细节原样带出


def test_forbidden_path_code_preserved_and_status_mapped():
    def handler(request):
        return httpx.Response(400, json={
            "ok": False,
            "error": {"code": "FORBIDDEN_PATH", "message": "路径越界",
                      "reason_kind": "residue"}})

    _it, _headers, err = dl.open_task_stream(
        cfg(), "H1", "../../etc/passwd",
        transport=httpx.MockTransport(handler))
    assert err["code"] == "FORBIDDEN_PATH"           # 不降级为泛化错误
    assert err["reason_kind"] == "residue"
    assert dl.status_for_code(err["code"]) == 400


def test_unauthorized_maps_to_401():
    def handler(request):
        return httpx.Response(401, json={
            "ok": False, "error": {"code": "UNAUTHORIZED", "message": "口令不对"}})

    _it, _headers, err = dl.open_task_stream(cfg(), "H1", "x",
                                             transport=httpx.MockTransport(handler))
    assert err["code"] == "UNAUTHORIZED"
    assert dl.status_for_code(err["code"]) == 401


def test_open_published_stream_url_and_bytes():
    def handler(request):
        assert str(request.url) == (
            "http://hadoop:8765/download/published/ml1m-clean-v1/ratings")
        return httpx.Response(
            200, content=b"data",
            headers={"content-disposition": 'attachment; filename="ratings.dat"'})

    it, headers, err = dl.open_published_stream(
        cfg(), "ml1m-clean-v1", "ratings", transport=httpx.MockTransport(handler))
    assert err is None
    assert b"".join(it) == b"data"
    assert "ratings.dat" in headers.get("Content-Disposition", "")


def test_unusable_config_reports_unavailable_without_network():
    def handler(request):                            # 永不调用
        raise AssertionError("未接入时不应发起任何请求")

    it, headers, err = dl.open_task_stream(
        McpConfig({}), "H1", "x", transport=httpx.MockTransport(handler))
    assert it is None and headers is None
    assert err["code"] == "MCP_UNAVAILABLE"


def test_token_never_leaks_into_error():
    def handler(request):
        return httpx.Response(500, content=b"boom")

    _it, _headers, err = dl.open_task_stream(
        cfg(), "H1", "x", transport=httpx.MockTransport(handler))
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


class RelayHandlerTest(RelayServerBase):
    def _runtime(self):
        def handler(request):
            assert request.headers["Authorization"] == "Bearer " + TOKEN
            if "forbidden" in str(request.url):
                return httpx.Response(403, json={
                    "ok": False,
                    "error": {"code": "FORBIDDEN_HOST", "message": "Host 不在允许列表"}})
            return httpx.Response(
                200, content=b"clean-ratings",
                headers={"content-disposition": 'attachment; filename="ratings.dat"',
                         "content-length": "13"})

        self._config = cfg(httpx.MockTransport(handler))
        return StubRuntime(FakeClient(self._config))

    def test_download_relays_bytes_and_headers(self):
        status, headers, raw = self.request(
            "/api/files/download?task_id=H1&path=cleaned/x/ratings.dat")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"clean-ratings")
        self.assertIn("ratings.dat", headers.get("Content-Disposition", ""))
        self.assertEqual(headers.get("Content-Length"), "13")

    def test_download_error_returns_envelope_and_status(self):
        status, _headers, raw = self.request(
            "/api/files/download?task_id=H1&path=forbidden")
        self.assertEqual(status, 403)
        body = json.loads(raw.decode("utf-8"))
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "FORBIDDEN_HOST")

    def test_published_download_relays(self):
        status, headers, raw = self.request(
            "/api/data/published/download?data_version=ml1m-clean-v1&table=ratings")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"clean-ratings")
        self.assertIn("ratings.dat", headers.get("Content-Disposition", ""))


class UnconfiguredRelayTest(RelayServerBase):
    def _runtime(self):
        return StubRuntime(NullMcpClient())

    def test_null_client_reports_mcp_unavailable(self):
        status, _headers, raw = self.request("/api/files/download?task_id=H1&path=x")
        self.assertEqual(status, 503)
        body = json.loads(raw.decode("utf-8"))
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], "MCP_UNAVAILABLE")

    def test_published_unavailable(self):
        status, _headers, raw = self.request(
            "/api/data/published/download?data_version=v1&table=ratings")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(raw.decode("utf-8"))["error"]["code"],
                         "MCP_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
