# -*- coding: utf-8 -*-
"""§3.11「资源与文件接口（直通，不经 LLM）」的真实 HTTP 实测。

用标准库 `http.client` 起真服务（`ThreadingHTTPServer`），用临时原始区 / 任务目录
隔离环境，覆盖契约八条端点与安全约束：

    列原始数据 / 上传 (.dat/.zip) / 列已发布 / 取方案内容 / 上传方案(校验) /
    列任务文件 / 预览 / 下载；越界 → FORBIDDEN_PATH；类型不符 → UNSUPPORTED_FILE；
    缺失 → FILE_NOT_FOUND。

这些端点**不经大模型、不产业务数字**：本测试只断言文件搬运与目录列举事实。
"""
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile

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


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class ResourceServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage11_resources_")
        self.raw_dir = os.path.join(self.tmp, "raw")
        self.var_dir = os.path.join(self.tmp, "var")
        self.tasks_dir = os.path.join(self.var_dir, "tasks")
        os.makedirs(self.tasks_dir, exist_ok=True)
        self._set_env("AGENT_DATA_RAW_DIR", self.raw_dir)
        self._set_env("ML_VAR_DIR", self.var_dir)
        srv = http_api.create_server("127.0.0.1", 0)
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

class RawDataTest(ResourceServerBase):
    def test_list_raw_starts_empty(self):
        status, payload, _ = self.get_json("/api/data/raw")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["datasets"], [])
        self.assertEqual(payload["dir"], self.raw_dir)

    def test_upload_dat_lands_under_name(self):
        status, payload, _ = self.post_multipart(
            "/api/data/raw/upload",
            {"name": "ml-test"},
            [("files", "ratings.dat", b"1::2::3::4\r\n")])
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["uploaded"]["name"], "ml-test")
        names = [d["name"] for d in payload["datasets"]]
        self.assertIn("ml-test", names)
        on_disk = os.path.join(self.raw_dir, "ml-test", "ratings.dat")
        self.assertTrue(os.path.isfile(on_disk))

    def test_upload_zip_is_extracted(self):
        archive = zip_bytes({"movies.dat": b"1::Toy\r\n",
                             "users.dat": b"1::F\r\n"})
        status, payload, _ = self.post_multipart(
            "/api/data/raw/upload",
            {"name": "zipped"},
            [("files", "ml.zip", archive)])
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.raw_dir, "zipped", "movies.dat")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.raw_dir, "zipped", "users.dat")))

    def test_upload_default_name_from_filename(self):
        _, payload, _ = self.post_multipart(
            "/api/data/raw/upload", {},
            [("files", "ratings.dat", b"x")])
        self.assertEqual(payload["uploaded"]["name"], "ratings")

    def test_unsupported_extension_rejected(self):
        status, payload, _ = self.post_multipart(
            "/api/data/raw/upload", {},
            [("files", "notes.txt", b"nope")])
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "UNSUPPORTED_FILE")

    def test_zip_slip_rejected(self):
        archive = zip_bytes({"../escape.dat": b"x"})
        status, payload, _ = self.post_multipart(
            "/api/data/raw/upload", {"name": "evil"},
            [("files", "evil.zip", archive)])
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FORBIDDEN_PATH")


# ---------------------------------------------------------------------------
# §3.11.2 清洗后数据（已发布）
# ---------------------------------------------------------------------------

class PublishedDataTest(ResourceServerBase):
    def _publish(self, tid, version, tables):
        vdir = os.path.join(self.tasks_dir, tid, "cleaned", version)
        os.makedirs(vdir, exist_ok=True)
        for name, data in tables.items():
            with open(os.path.join(vdir, name), "wb") as fh:
                fh.write(data)
        return vdir

    def test_empty_returns_empty_array(self):
        _, payload, _ = self.get_json("/api/data/published")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["datasets"], [])

    def test_lists_published_versions(self):
        self._publish("20260101-000000-aaaaaa", "ml1m-clean-v1",
                      {"ratings.dat": b"1", "movies.dat": b"2"})
        _, payload, _ = self.get_json("/api/data/published")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["datasets"]), 1)
        ds = payload["datasets"][0]
        self.assertEqual(ds["data_version"], "ml1m-clean-v1")
        self.assertEqual(ds["source_task_id"], "20260101-000000-aaaaaa")
        table_names = sorted(t["name"] for t in ds["tables"])
        self.assertEqual(table_names, ["movies", "ratings"])


# ---------------------------------------------------------------------------
# §3.11.3 方案文件
# ---------------------------------------------------------------------------

class SchemeTest(ResourceServerBase):
    def test_content_returns_json(self):
        status, payload, _ = self.get_json(
            "/api/schemes/content?name=scoring_scheme.v1.json")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "scoring_scheme.v1.json")
        self.assertEqual(payload["path"], "config/scoring_scheme.v1.json")
        self.assertEqual(payload["content"]["config_type"], "scoring_scheme")

    def test_content_missing_file(self):
        _, payload, _ = self.get_json(
            "/api/schemes/content?name=does_not_exist.json")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FILE_NOT_FOUND")

    def test_content_traversal_forbidden(self):
        _, payload, _ = self.get_json(
            "/api/schemes/content?name=../../../../etc/passwd")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FORBIDDEN_PATH")

    def test_upload_valid_scheme_runs_validation(self):
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

class TaskFilesTest(ResourceServerBase):
    def setUp(self):
        super().setUp()
        self.tid = "20260101-120000-bbbbbb"
        base = os.path.join(self.tasks_dir, self.tid)
        os.makedirs(os.path.join(base, "sub"), exist_ok=True)
        with open(os.path.join(base, "result.json"), "w", encoding="utf-8") as fh:
            fh.write('{"ok": true}')
        with open(os.path.join(base, "report.md"), "wb") as fh:
            fh.write(b"# report\n")              # 二进制写：避免 Windows 换行转换
        with open(os.path.join(base, "ratings.dat"), "wb") as fh:
            fh.write(b"1\r\n")

    def test_list_files_with_kinds(self):
        _, payload, _ = self.get_json("/api/tasks/%s/files" % self.tid)
        self.assertTrue(payload["ok"])
        kinds = {f["path"]: f["kind"] for f in payload["files"]}
        self.assertEqual(kinds["result.json"], "json")
        self.assertEqual(kinds["report.md"], "md")
        self.assertEqual(kinds["ratings.dat"], "dat")

    def test_preview_json(self):
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=result.json" % self.tid)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["previewable"])
        self.assertEqual(payload["content"], '{"ok": true}')

    def test_preview_binary_not_previewable(self):
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=ratings.dat" % self.tid)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["previewable"])

    def test_download_attachment_header(self):
        status, headers, raw = self.request(
            "GET", "/api/files/download?task_id=%s&path=report.md" % self.tid)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn("report.md", headers.get("Content-Disposition", ""))
        self.assertEqual(raw.decode("utf-8"), "# report\n")

    def test_missing_file(self):
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=nope.json" % self.tid)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FILE_NOT_FOUND")

    def test_traversal_forbidden(self):
        _, payload, _ = self.get_json(
            "/api/files/preview?task_id=%s&path=../../../../etc/passwd" % self.tid)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FORBIDDEN_PATH")

    def test_unknown_task(self):
        _, payload, _ = self.get_json(
            "/api/tasks/20260101-999999-nope/files")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "FILE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
