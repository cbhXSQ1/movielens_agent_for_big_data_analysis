# -*- coding: utf-8 -*-
"""M-测试：hadoop/tools/opencode_proxy.py 的会话 ID 转接头（get_or_create_session）。

背景（docs/agent/gateway-probe.md V2）：opencode-go 网关按 `x-opencode-session`
做会话路由；代理负责把 agent 的会话键（`x-agent-session`）映射成**稳定**的网关
会话 UUID。随机 UUID 只代表请求被接受，不代表多轮延续 —— 本测试锁定映射语义：
同键同会话 / 异键隔离 / TTL 过期换新 / 容量上限淘汰。
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, os.path.join(REPO_ROOT, "hadoop"))

from tools.opencode_proxy import get_or_create_session, parse_proxy_args  # noqa: E402 (namespace 包)


class TestProxyArgs(unittest.TestCase):
    def test_defaults_for_opencode_go(self):
        """默认：session_header 开（upstream 是 opencode-go 时必需）。"""
        conf = parse_proxy_args(["--api-key", "k"])
        self.assertTrue(conf["session_header"])
        self.assertEqual(8901, conf["port"])
        self.assertIn("opencode.ai", conf["upstream"])

    def test_session_header_off_for_standard_endpoints(self):
        """标准 OpenAI 兼容 upstream（DeepSeek 官方 / icspa）可关掉专属头。"""
        conf = parse_proxy_args(["--upstream", "https://api.deepseek.com/v1",
                                 "--session-header", "off"])
        self.assertFalse(conf["session_header"])

    def test_unknown_arg_rejected(self):
        self.assertIsNone(parse_proxy_args(["--bogus"]))


class TestSessionMapping(unittest.TestCase):
    def test_same_key_keeps_same_session(self):
        """多轮延续：同一个 agent 会话键必须永远映射到同一个网关会话。"""
        m = {}
        first = get_or_create_session(m, "chat-1")
        second = get_or_create_session(m, "chat-1")
        self.assertEqual(first, second)
        self.assertEqual(1, len(m))

    def test_different_keys_isolated(self):
        """并发隔离：不同会话键得到不同网关会话。"""
        m = {}
        a = get_or_create_session(m, "chat-a")
        b = get_or_create_session(m, "chat-b")
        self.assertNotEqual(a, b)

    def test_ttl_expiry_rotates_session(self):
        """过期后同键换新会话（旧会话作废）。"""
        m = {}
        first = get_or_create_session(m, "k", ttl=0.001)
        import time
        time.sleep(0.01)
        second = get_or_create_session(m, "k", ttl=0.001)
        self.assertNotEqual(first, second)

    def test_capacity_evicts_oldest(self):
        """容量上限：超限淘汰最久未用键，映射表不超限（LRU）。"""
        m = {}
        id1 = get_or_create_session(m, "k1", max_sessions=2)
        get_or_create_session(m, "k2", max_sessions=2)
        id3 = get_or_create_session(m, "k3", max_sessions=2)   # 超限 → 淘汰最老的 k1
        self.assertEqual(2, len(m))
        # k1 已被淘汰 → 再取是全新会话，且这次淘汰当时最老的 k2
        self.assertNotEqual(id1, get_or_create_session(m, "k1", max_sessions=2))
        self.assertLessEqual(2, len(m))
        # k3 从未被淘汰 → 会话保持稳定
        self.assertEqual(id3, get_or_create_session(m, "k3", max_sessions=2))

    def test_refresh_keeps_session_alive(self):
        """滚动续期：过期前再次使用，会话不换。"""
        m = {}
        first = get_or_create_session(m, "k", ttl=3600)
        second = get_or_create_session(m, "k", ttl=3600)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()