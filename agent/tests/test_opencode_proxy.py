# -*- coding: utf-8 -*-
"""opencode 适配器测试（agent/gateway/opencode_proxy.py）。

三部分：
  1. 参数解析：只剩 --port / --api-key（通用代理功能已删）。
  2. 会话键映射：同键同会话 / 异键隔离 / TTL 过期 / 容量淘汰（网关侧多轮延续的前提）。
  3. 流式转发：本地假上游分块**带延时**吐 SSE，验证适配器**逐块**转发、不缓冲。
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

from agent.gateway.opencode_proxy import (Handler, get_or_create_session,
                                          parse_args)


class TestArgs(unittest.TestCase):
    def test_defaults(self):
        conf = parse_args(["--api-key", "k"])
        self.assertEqual(8901, conf["port"])
        self.assertEqual("k", conf["api_key"])

    def test_port_override(self):
        conf = parse_args(["--port", "9100", "--api-key", "k"])
        self.assertEqual(9100, conf["port"])

    def test_unknown_arg_rejected(self):
        """通用代理时代的参数（--upstream/--session-header/--model-id）已不再接受。"""
        self.assertIsNone(parse_args(["--upstream", "x"]))
        self.assertIsNone(parse_args(["--session-header", "off"]))
        self.assertIsNone(parse_args(["--bogus"]))

    def test_bad_port_rejected(self):
        self.assertIsNone(parse_args(["--port", "abc"]))


class TestSessionMapping(unittest.TestCase):
    def test_same_key_keeps_same_session(self):
        m = {}
        self.assertEqual(get_or_create_session(m, "chat-1"),
                         get_or_create_session(m, "chat-1"))
        self.assertEqual(1, len(m))

    def test_different_keys_isolated(self):
        m = {}
        self.assertNotEqual(get_or_create_session(m, "chat-a"),
                            get_or_create_session(m, "chat-b"))

    def test_ttl_expiry_rotates_session(self):
        m = {}
        first = get_or_create_session(m, "k", ttl=0.001)
        time.sleep(0.01)
        self.assertNotEqual(first, get_or_create_session(m, "k", ttl=0.001))

    def test_capacity_evicts_oldest(self):
        m = {}
        id1 = get_or_create_session(m, "k1", max_sessions=2)
        get_or_create_session(m, "k2", max_sessions=2)
        id3 = get_or_create_session(m, "k3", max_sessions=2)   # 超限 → 淘汰最老 k1
        self.assertEqual(2, len(m))
        self.assertNotEqual(id1, get_or_create_session(m, "k1", max_sessions=2))
        self.assertEqual(id3, get_or_create_session(m, "k3", max_sessions=2))

    def test_refresh_keeps_session_alive(self):
        m = {}
        self.assertEqual(get_or_create_session(m, "k", ttl=3600),
                         get_or_create_session(m, "k", ttl=3600))


class _FakeUpstream(BaseHTTPRequestHandler):
    """假上游：分 3 帧吐 SSE，每帧后睡 0.3s —— 用来证明适配器没有攒批。"""

    protocol_version = "HTTP/1.1"
    gap = 0.3

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for i in range(3):
            frame = ("data: %s\n\n" % json.dumps({"i": i})).encode("utf-8")
            self.wfile.write(frame)
            self.wfile.flush()
            time.sleep(self.gap)
        self.close_connection = True

    def log_message(self, *args):
        pass


class TestStreamingRelay(unittest.TestCase):
    def setUp(self):
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
        threading.Thread(target=self.up.serve_forever, daemon=True).start()

        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.proxy_port = self.proxy.server_address[1]
        self.proxy.api_key = "test-key"
        # 把上游指到本地假上游（CLI 不暴露该开关；仅测试注入服务器属性）
        self.proxy.upstream = "http://127.0.0.1:%d" % self.up.server_address[1]
        self.proxy.sessions = {}
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()

    def tearDown(self):
        for srv in (self.proxy, self.up):
            srv.shutdown()
            srv.server_close()

    def test_relays_incrementally(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % self.proxy_port,
            data=b'{"stream": true, "messages": []}',
            headers={"Content-Type": "application/json"}, method="POST")
        stamps = []
        with urllib.request.urlopen(req, timeout=15) as resp:
            self.assertIn("event-stream", resp.headers.get("Content-Type", ""))
            for line in resp:
                if line.startswith(b"data:"):
                    stamps.append(time.time())
        self.assertEqual(3, len(stamps))
        # 三帧之间有两次 ~0.3s 的睡眠：整体跨度必须明显大于一次性到达
        self.assertGreater(stamps[-1] - stamps[0], 0.4)

    def test_unknown_path_error(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/nope" % self.proxy_port, data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("应返回 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(404, exc.code)


if __name__ == "__main__":
    unittest.main()
