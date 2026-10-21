# -*- coding: utf-8 -*-
"""opencode 网关适配（进程内）测试：agent/gateway/curl_gateway.py。

覆盖：
  1. is_opencode_gateway：按 host 判定，opencode.ai（含子域）为真，其它为假。
  2. 会话头适配：x-agent-session → x-opencode-session（值相同、去掉原头）；
     缺省时补**进程内稳定**的 UUID（多次调用一致，不随机漂移）。
  3. stream_via_curl：对**本地假 SSE 上游**逐块流式转发、不缓冲（不触网）。
  4. HttpTransport 按 URL 分派：opencode → curl；标准端点 → urllib。
  5. 失败可被 classify_exception 归类（curl 不可用 → 网络不可达）。
"""
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.gateway.curl_gateway import (adapt_session_headers, build_curl_command,
                                        default_session_id, is_opencode_gateway,
                                        stream_via_curl)
from agent.llm import ModelError, NETWORK_UNREACHABLE, classify_exception


class TestIsOpencodeGateway(unittest.TestCase):
    def test_matches_opencode_hosts(self):
        self.assertTrue(is_opencode_gateway("https://opencode.ai/zen/go/v1/chat/completions"))
        self.assertTrue(is_opencode_gateway("https://api.opencode.ai/v1/chat/completions"))

    def test_rejects_standard_endpoints(self):
        self.assertFalse(is_opencode_gateway("https://api.deepseek.com/v1/chat/completions"))
        self.assertFalse(is_opencode_gateway("https://api.openai.com/v1/chat/completions"))
        self.assertFalse(is_opencode_gateway("https://example.com/opencode.ai/x"))

    def test_rejects_garbage(self):
        self.assertFalse(is_opencode_gateway(""))
        self.assertFalse(is_opencode_gateway(None))
        self.assertFalse(is_opencode_gateway("not a url"))


class TestSessionHeaders(unittest.TestCase):
    def test_agent_key_becomes_gateway_header(self):
        out = adapt_session_headers({"Content-Type": "application/json",
                                     "x-agent-session": "conv-42"})
        self.assertEqual("conv-42", out["x-opencode-session"])
        self.assertNotIn("x-agent-session", out)
        self.assertEqual("application/json", out["Content-Type"])

    def test_missing_key_gets_stable_uuid(self):
        first = adapt_session_headers({})["x-opencode-session"]
        second = adapt_session_headers({})["x-opencode-session"]
        self.assertEqual(first, second)
        self.assertEqual(default_session_id(), first)

    def test_existing_gateway_header_is_replaced(self):
        out = adapt_session_headers({"x-agent-session": "k",
                                     "x-opencode-session": "stale"})
        self.assertEqual("k", out["x-opencode-session"])
        self.assertEqual(1, list(out).count("x-opencode-session"))

    def test_build_command_carries_gateway_session_header(self):
        cmd = build_curl_command("https://opencode.ai/zen/go/v1/chat/completions",
                                 {"x-agent-session": "conv-7"}, 5)
        self.assertIn("--data-binary", cmd)
        self.assertIn("@-", cmd)
        self.assertIn("--no-buffer", cmd)
        self.assertIn("x-opencode-session: conv-7", cmd)
        self.assertNotIn("x-agent-session: conv-7", cmd)


class _FakeSseUpstream(BaseHTTPRequestHandler):
    """假上游：分 3 帧吐 SSE，每帧后睡 0.3s —— 用来证明转发没有攒批。"""

    protocol_version = "HTTP/1.1"
    gap = 0.3
    frames = 3

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.server.seen_headers = {k.lower(): v for k, v in self.headers.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for i in range(self.frames):
            frame = ("data: %s\n\n" % json.dumps({"i": i})).encode("utf-8")
            self.wfile.write(frame)
            self.wfile.flush()
            time.sleep(self.gap)
        self.close_connection = True

    def log_message(self, *args):
        pass


class _FakeErrorUpstream(BaseHTTPRequestHandler):
    """假上游：返回**非 SSE** 的 JSON 错误体（模拟网关拒绝）。"""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        body = json.dumps({"error": {"message": "upstream refused"}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, *args):
        pass


class TestStreamViaCurl(unittest.TestCase):
    def setUp(self):
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSseUpstream)
        self.url = "http://127.0.0.1:%d/v1/chat/completions" % self.up.server_address[1]
        threading.Thread(target=self.up.serve_forever, daemon=True).start()

    def tearDown(self):
        self.up.shutdown()
        self.up.server_close()

    def test_streams_incrementally(self):
        stamps = []
        for chunk in stream_via_curl(self.url, {"Content-Type": "application/json"},
                                     b'{"stream": true, "messages": []}', 15):
            if "data:" in chunk:
                stamps.append(time.time())
        self.assertEqual(3, len(stamps))
        # 三帧之间两次 ~0.3s 睡眠：整体跨度必须明显大于一次性到达
        self.assertGreater(stamps[-1] - stamps[0], 0.4)

    def test_gateway_session_header_reaches_upstream(self):
        for _ in stream_via_curl(self.url, {"x-agent-session": "conv-9",
                                            "Content-Type": "application/json"},
                                 b"{}", 15):
            pass
        got = getattr(self.up, "seen_headers", {})
        self.assertEqual("conv-9", got.get("x-opencode-session"))
        self.assertNotIn("x-agent-session", got)

    def test_default_session_header_is_stable_uuid(self):
        for _ in stream_via_curl(self.url, {}, b"{}", 15):
            pass
        got = getattr(self.up, "seen_headers", {})
        self.assertEqual(default_session_id(), got.get("x-opencode-session"))


class TestStreamNonSseBody(unittest.TestCase):
    def test_non_sse_error_body_is_wrapped(self):
        up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeErrorUpstream)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d/v1/chat/completions" % up.server_address[1]
            chunks = list(stream_via_curl(url, {}, b"{}", 15))
            self.assertTrue(any("upstream refused" in c for c in chunks))
            self.assertTrue(all(c.lstrip().startswith("data:") for c in chunks if c.strip()))
        finally:
            up.shutdown()
            up.server_close()


class TestLaunchFailureClassified(unittest.TestCase):
    def test_missing_curl_is_network_unreachable(self):
        gen = stream_via_curl("https://opencode.ai/zen/go/v1/chat/completions",
                              {}, b"{}", 5, curl_bin="definitely-not-a-real-curl")
        with self.assertRaises(OSError) as ctx:
            list(gen)
        err = classify_exception(ctx.exception)
        self.assertIsInstance(err, ModelError)
        self.assertEqual(NETWORK_UNREACHABLE, err.code)


class TestHttpTransportDispatch(unittest.TestCase):
    def test_opencode_url_uses_curl(self):
        import agent.llm.transport as transport
        used = []

        def fake_curl(url, headers=None, body=None, timeout=None, curl_bin=None):
            used.append(url)
            yield "data: [DONE]\n\n"

        orig = transport.stream_via_curl
        transport.stream_via_curl = fake_curl
        try:
            out = list(transport.HttpTransport().post_stream({
                "url": "https://opencode.ai/zen/go/v1/chat/completions",
                "headers": {}, "body": b"{}", "timeout": 5}))
        finally:
            transport.stream_via_curl = orig
        self.assertEqual(["https://opencode.ai/zen/go/v1/chat/completions"], used)
        self.assertIn("data: [DONE]\n\n", out)

    def test_standard_url_uses_urllib(self):
        import agent.llm.transport as transport
        seen = {}

        class _Resp(object):
            def __iter__(self):
                return iter([b"data: [DONE]\n\n"])

            def close(self):
                seen["closed"] = True

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            return _Resp()

        def fake_curl(*args, **kwargs):
            raise AssertionError("标准端点不应走 curl")
            yield  # pragma: no cover

        orig_open = urllib.request.urlopen
        orig_curl = transport.stream_via_curl
        urllib.request.urlopen = fake_urlopen
        transport.stream_via_curl = fake_curl
        try:
            out = list(transport.HttpTransport().post_stream({
                "url": "https://api.deepseek.com/v1/chat/completions",
                "headers": {}, "body": b"{}", "timeout": 5}))
        finally:
            urllib.request.urlopen = orig_open
            transport.stream_via_curl = orig_curl
        self.assertEqual("https://api.deepseek.com/v1/chat/completions", seen["url"])
        self.assertEqual(["data: [DONE]\n\n"], out)


if __name__ == "__main__":
    unittest.main()
