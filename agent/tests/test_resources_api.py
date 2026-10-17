# -*- coding: utf-8 -*-
"""§3.11「资源与文件接口（直通，不经 LLM）」的真实 HTTP 实测。

用标准库 `http.client` 起真服务（`ThreadingHTTPServer`），资源类端点（原始数据 /
已发布 / 方案 / 产物清单 / 预览）改由**假远端客户端**提供罐头信封，断言映射后的
出口形状与前端契约一致、失败原样转述。**不连网、不碰 127.0.0.1:8900。**

方案上传仍是本机落盘（远端不提供上传）；下载已改为经远端只读路由带口令流式中转
（见 `test_mcp_download.py`）。

这些端点**不经大模型、不产业务数字**：本测试只断言字段映射与错误透传事实。
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api, resources  # noqa: E402

MULTIPART_BOUNDARY = "----stage11TestBoundary9876543210"


def multipart(fields, files):
    """造一个 multipart/form-data 请求体；返回 (bytes, content_type)。
    files 为 [(字段名, 文件名, bytes), …]。
    """
    body = b""
    for key, value in fields.items():
        body += ("--%s\r\n" % MULTIPART_BOUNDARY).encode()
        body += ('Content-Disposition: form-data; name="%s"\r\n\r\n'
                 % key).encode()
        body += value.encode("utf-8") + b"\r\n"
    for field, filename, data in files:
        body += ("--%s\r\n" % MULTIPART_BOUNDARY).encode()
        body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                 % (field, filename)).encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
    body += ("--%s--\r\n" % MULTIPART_BOUNDARY).encode()
    return body, "multipart/form-data; boundary=%s" % MULTIPART_BOUNDARY


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


class StubRuntime(object):
    """只暴露资源端点需要的 `mcp_client`（其余端点本测试不涉及）。"""

    def __init__(self, client):
        self.mcp_client = client


class ResourceServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage11_resources_")
        self.var_dir = os.path.join(self.tmp, "var")
        self.tasks_dir = os.path.join(self.var_dir, "tasks")
        os.makedirs(self.tasks_dir, exist_ok=True)
        self._set_env("ML_VAR_DIR", self.var_dir)
        self.mcp = FakeMcpClient()
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))
        srv = http_api.create_server("127.0.0.1", 0,
                                     runtime=StubRuntime(self.mcp))
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop, srv, thread)
        self.port = srv.server_address[1]

    def _set_env(self, key, value):
        old = os.environ.get(key)

        def restore():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        os.environ[key] = value
        self.addCleanup(restore)

    def _stop(self, srv, thread):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def request(self, method, path, body=None, content_type=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            headers = {}
            if content_type:
                headers["Content-Type"] = content_type
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, dict(resp.getheaders()), raw
        finally:
            conn.close()

    def get_json(self, path):
        status, headers, raw = self.request("GET", path)
        return status, json.loads(raw.decode("utf-8")), headers

    def post_multipart(self, path, fields=None, files=None):
        body, ctype = multipart(fields or {}, files or [])
        status, headers, raw = self.request("POST", path, body=body,
                                            content_type=ctype)
        return status, json.loads(raw.decode("utf-8")), headers


# ---------------------------------------------------------------------------
# §3.11.1 原始数据
# ---------------------------------------------------------------------------

RAW_ENVELOPE = {
    "ok": True, "interface_version": "1.0",
    "datasets": [
        {"name": "ml-1m", "path": "ml-1m",
         "files": [{"name": "ratings.dat", "size": 10, "updated_at": 1234.5,
                    "kind": "dat"}],
         "file_count": 1, "size": 10, "updated_at": 1234.5},
    ],
    "count": 1, "file_count": 1, "empty": False,
}


class RawDataTest(ResourceServerBase):
    def test_list_raw_maps_remote_shape(self):
        self.mcp.responses["list_raw_datasets"] = RAW_ENVELOPE
        status, payload, _ = self.get_json("/api/data/raw")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dir"], "")
        self.assertEqual(payload["datasets"], [
            {"name": "ml-1m", "path": "ml-1m",
             "files": [{"name": "ratings.dat", "size": 10, "updated_at": 1234.5}],
             "size": 10, "updated_at": 1234.5}])
        self.assertEqual(self.mcp.calls[-1][0], "list_raw_datasets")

    def test_list_raw_empty(self):
        self.mcp.responses["list_raw_datasets"] = {
            "ok": True, "datasets": [], "count": 0}
        _, payload, _ = self.get_json("/api/data/raw")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["datasets"], [])

    def test_list_raw_error_passthrough(self):
        self.mcp.responses["list_raw_datasets"] = {
            "ok": False, "error": {"code": "DEPENDENCY_MISSING",
                                   "message": "这台机器上没有配置原始数据区"}}
        _, payload, _ = self.get_json("/api/data/raw")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "DEPENDENCY_MISSING")
        self.assertEqual(payload["error"]["message"], "这台机器上没有配置原始数据区")

    def test_raw_upload_route_removed(self):
        """原始数据上传已下线：该 POST 路由不再是上传端点（落到未知路径）。"""
        status, _, _ = self.request(
            "POST", "/api/data/raw/upload", body=b"{}",
            content_type="application/json")
        self.assertEqual(status, 404)


# ---------------------------------------------------------------------------
# §3.11.2 清洗后数据（已发布）
# ---------------------------------------------------------------------------

PUBLISHED_ENVELOPE = {
    "ok": True, "published_dir": "/data/published", "exists": True,
    "versions": [
        {"data_version": "ml1m-clean-v1", "path": "/data/published/ml1m-clean-v1",
         "tables": [{"name": "ratings", "file": "ratings.dat", "size": 5}],
         "published_at": 1700000000.0, "source_task_id": "H-1",
         "other_files": [{"name": "x", "size": 1}]},
    ],
    "count": 1,
}


class PublishedDataTest(ResourceServerBase):
    def test_list_published_maps_remote_shape(self):
        self.mcp.responses["list_published_versions"] = PUBLISHED_ENVELOPE
        _, payload, _ = self.get_json("/api/data/published")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["datasets"], [
            {"data_version": "ml1m-clean-v1",
             "source_task_id": "H-1",
             "path": "/data/published/ml1m-clean-v1",
             "tables": [{"name": "ratings", "file": "ratings.dat", "size": 5}],
             "published_at": 1700000000.0}])

    def test_list_published_empty(self):
        self.mcp.responses["list_published_versions"] = {
            "ok": True, "exists": False, "versions": [], "count": 0}
        _, payload, _ = self.get_json("/api/data/published")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["datasets"], [])

    def test_list_published_error_passthrough(self):
        self.mcp.responses["list_published_versions"] = {
            "ok": False, "error": {"code": "DRIVER_UNREACHABLE",
                                   "message": "读取发布区失败：连不上集群"}}
        _, payload, _ = self.get_json("/api/data/published")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "DRIVER_UNREACHABLE")


# ---------------------------------------------------------------------------
# §3.11.3 方案文件
# ---------------------------------------------------------------------------

class SchemeTest(ResourceServerBase):
    def test_list_schemes_remote(self):
        self.mcp.responses["list_schemes"] = {
            "ok": True,
            "schemes": [{"name": "cleaning_rules.v1.json", "kind": "rules"}]}
        _, payload, _ = self.get_json("/api/schemes")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schemes"],
                         [{"name": "cleaning_rules.v1.json", "kind": "rules"}])

    def test_list_schemes_error_passthrough(self):
        self.mcp.responses["list_schemes"] = {
            "ok": False, "error": {"code": "DRIVER_UNREACHABLE", "message": "连不上"}}
        _, payload, _ = self.get_json("/api/schemes")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "DRIVER_UNREACHABLE")

    def test_content_remote(self):
        def env(args):
            return {"ok": True, "name": args["name"],
                    "path": "config/" + args["name"], "size": 12,
                    "content": {"config_type": "scoring_scheme"}}
        self.mcp.responses["get_scheme_content"] = env
        status, payload, _ = self.get_json(
            "/api/schemes/content?name=scoring_scheme.v1.json")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "scoring_scheme.v1.json")
        self.assertEqual(payload["path"], "config/scoring_scheme.v1.json")
        self.assertEqual(payload["content"], {"config_type": "scoring_scheme"})
        self.assertEqual(self.mcp.calls[-1],
                         ("get_scheme_content", {"name": "scoring_scheme.v1.json"}))

    def test_content_missing_file_passthrough(self):
        self.mcp.responses["get_scheme_content"] = {
            "ok": False, "error": {"code": "FILE_NOT_FOUND", "message": "文件不存在"}}
        _, payload, _ = self.get_json(
            "/api/schemes/content?name=does_not_exist.json")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FILE_NOT_FOUND")

    def test_content_traversal_passthrough(self):
        self.mcp.responses["get_scheme_content"] = {
            "ok": False, "error": {"code": "FORBIDDEN_PATH", "message": "不允许访问"}}
        _, payload, _ = self.get_json(
            "/api/schemes/content?name=../../../../etc/passwd")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FORBIDDEN_PATH")

    def test_upload_valid_scheme_runs_validation(self):
        """方案上传仍是本机落盘（远端不提供上传），校验仍复用现有工具层。"""
        with open(os.path.join(REPO_ROOT, "config",
                               "cleaning_rules.v1.json"), "rb") as fh:
            rules = fh.read()
        name = "test_upload_resources.v1.json"
        saved = os.path.join(REPO_ROOT, "config", name)
        self.addCleanup(lambda: os.path.exists(saved) and os.remove(saved))

        status, payload, _ = self.post_multipart(
            "/api/schemes/upload", {"name": name},
            [("file", name, rules)])
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["saved"]["path"], "config/" + name)
        self.assertTrue(payload["validation"]["ok"])
        self.assertTrue(os.path.isfile(saved))

    def test_upload_invalid_json_reports_validation_failure(self):
        name = "test_upload_broken.v1.json"
        saved = os.path.join(REPO_ROOT, "config", name)
        self.addCleanup(lambda: os.path.exists(saved) and os.remove(saved))
        status, payload, _ = self.post_multipart(
            "/api/schemes/upload", {"name": name},
            [("file", name, b"{not valid json")])
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("saved", payload)                    # 仍落盘，便于修正
        self.assertFalse(payload["validation"]["ok"])

    def test_upload_non_json_rejected(self):
        _, payload, _ = self.post_multipart(
            "/api/schemes/upload", {"name": "evil.txt"},
            [("file", "evil.txt", b"nope")])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "UNSUPPORTED_FILE")


# ---------------------------------------------------------------------------
# §3.11.4 产出文件
# ---------------------------------------------------------------------------

TASK_ID = "20260101-120000-bbbbbb"


def _task_files_env(args):
    return {"ok": True, "task_id": args["task_id"], "count": 2, "files": [
        {"path": "result.json", "size": 12, "kind": "json"},
        {"path": "report.md", "size": 9, "kind": "md"}]}


def _preview_env(args):
    if args["path"] == "ratings.dat":
        return {"ok": True, "task_id": args["task_id"], "path": args["path"],
                "kind": "dat", "size": 5, "previewable": False}
    return {"ok": True, "task_id": args["task_id"], "path": args["path"],
            "kind": "json", "size": 12, "previewable": True,
            "content": '{"ok": true}'}


class TaskFilesTest(ResourceServerBase):
    def setUp(self):
        super().setUp()
        base = os.path.join(self.tasks_dir, TASK_ID)
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "report.md"), "wb") as fh:
            fh.write(b"# report\n")              # 二进制写：避免 Windows 换行转换

    def test_list_files_maps_remote_shape(self):
        self.mcp.responses["list_task_files"] = _task_files_env
        _, payload, _ = self.get_json("/api/tasks/%s/files" % TASK_ID)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_id"], TASK_ID)
        self.assertEqual(payload["files"], [
            {"path": "result.json", "size": 12, "kind": "json"},
            {"path": "report.md", "size": 9, "kind": "md"}])
        self.assertEqual(self.mcp.calls[-1],
                         ("list_task_files", {"task_id": TASK_ID}))

    def test_list_files_error_passthrough(self):
        self.mcp.responses["list_task_files"] = {
            "ok": False, "error": {"code": "TASK_NOT_FOUND", "message": "任务不存在"}}
        _, payload, _ = self.get_json("/api/tasks/20260101-999999-nope/files")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "TASK_NOT_FOUND")

    def test_preview_json(self):
        self.mcp.responses["read_task_file"] = _preview_env
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=result.json" % TASK_ID)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["previewable"])
        self.assertEqual(payload["content"], '{"ok": true}')
        self.assertEqual(payload["kind"], "json")
        self.assertEqual(self.mcp.calls[-1],
                         ("read_task_file", {"task_id": TASK_ID,
                                             "path": "result.json"}))

    def test_preview_binary_not_previewable(self):
        self.mcp.responses["read_task_file"] = _preview_env
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=ratings.dat" % TASK_ID)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["previewable"])
        self.assertNotIn("content", payload)

    def test_preview_missing_file_passthrough(self):
        self.mcp.responses["read_task_file"] = {
            "ok": False, "error": {"code": "FILE_NOT_FOUND", "message": "文件不存在"}}
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=nope.json" % TASK_ID)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FILE_NOT_FOUND")

    def test_preview_traversal_passthrough(self):
        self.mcp.responses["read_task_file"] = {
            "ok": False, "error": {"code": "FORBIDDEN_PATH",
                                   "message": "该路径不允许访问",
                                   "reason_kind": "residue"}}
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=../../../../etc/passwd" % TASK_ID)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FORBIDDEN_PATH")
        # 远端细节原样带出，便于对端归类
        self.assertEqual(payload["error"]["reason_kind"], "residue")

    def test_download_relays_remote_bytes(self):
        """产物下载经远端只读路由，Agent 带口令流式中转（不再读本机文件）。"""
        import httpx
        from agent.mcp_config import McpConfig

        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer " + "t" * 20)
            return httpx.Response(
                200, content=b"# report\n",
                headers={"content-disposition": 'attachment; filename="report.md"',
                         "content-length": "9"})

        config = McpConfig({"url": "http://hadoop:8765/mcp", "token": "t" * 20})
        config.transport = httpx.MockTransport(handler)   # 仅测试：不连网
        self.mcp.config = config

        status, headers, raw = self.request(
            "GET", "/api/files/download?task_id=%s&path=report.md" % TASK_ID)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn("report.md", headers.get("Content-Disposition", ""))
        self.assertEqual(raw.decode("utf-8"), "# report\n")


if __name__ == "__main__":
    unittest.main()
