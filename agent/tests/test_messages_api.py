# -*- coding: utf-8 -*-
"""§3.13「消息历史」端点：`GET /api/conversations/{cid}/messages`。

覆盖：
  * 按 `seq` **升序**返回该对话的全部节点消息；
  * `content` 是节点**完整内容**（不是摘要）；
  * 角色齐全（user / assistant / task）；
  * 对话不存在 → `CONVERSATION_NOT_FOUND`。

用假运行时（不触网、不碰真实 driver）起真 HTTP 服务实测。
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

from agent import http_api  # noqa: E402
from agent.runtime import Runtime  # noqa: E402


class MessagesApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="messages_api_")
        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self._close)
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))
        self.srv = http_api.create_server("127.0.0.1", 0, runtime=self.rt)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.srv.server_address[1]

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def _stop_server(self):
        try:
            self.srv.shutdown()
        except Exception:
            pass
        try:
            self.srv.server_close()
        except Exception:
            pass

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            headers = {}
            data = None
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
        finally:
            conn.close()

    def test_messages_ordered_by_seq_with_full_content(self):
        cid = self.rt.create_conversation(title="历史")
        store = self.rt.store
        n1 = store.add_node(cid, None, role="user", summary="摘要一",
                            content="完整内容一：这是用户说的第一句话。")
        n2 = store.add_node(cid, n1["id"], role="assistant", summary="摘要二",
                            content="完整内容二：助手的长回复，远长于摘要。")
        n3 = store.add_node(cid, n2["id"], role="task", summary="摘要三",
                            content="完整内容三：工具执行结果。")

        status, env = self.request("GET", "/api/conversations/%s/messages" % cid)
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual(cid, env["conversation_id"])

        msgs = env["messages"]
        self.assertEqual(3, len(msgs))

        # 按 seq 升序
        self.assertEqual([1, 2, 3], [m["seq"] for m in msgs])

        # 形状齐全
        for m in msgs:
            for key in ("node_id", "parent_id", "role", "content",
                        "created_at", "seq"):
                self.assertIn(key, m)

        # 角色齐全
        self.assertEqual("user", msgs[0]["role"])
        self.assertEqual("assistant", msgs[1]["role"])
        self.assertEqual("task", msgs[2]["role"])

        # parent_id 血缘
        self.assertIsNone(msgs[0]["parent_id"])
        self.assertEqual(n1["id"], msgs[1]["parent_id"])
        self.assertEqual(n2["id"], msgs[2]["parent_id"])

        # content 是**完整内容**（不是摘要）
        self.assertEqual("完整内容一：这是用户说的第一句话。", msgs[0]["content"])
        self.assertEqual("完整内容二：助手的长回复，远长于摘要。", msgs[1]["content"])
        self.assertEqual("完整内容三：工具执行结果。", msgs[2]["content"])
        self.assertNotEqual(msgs[0]["content"], msgs[0].get("summary"))
        self.assertEqual([n1["id"], n2["id"], n3["id"]],
                         [m["node_id"] for m in msgs])

    def test_messages_empty_for_fresh_conversation(self):
        cid = self.rt.create_conversation(title="空")
        status, env = self.request("GET", "/api/conversations/%s/messages" % cid)
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual([], env["messages"])

    def test_unknown_conversation_is_structured_error(self):
        status, env = self.request("GET", "/api/conversations/c9999/messages")
        self.assertEqual(200, status)
        self.assertFalse(env["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", env["error"]["code"])


if __name__ == "__main__":
    unittest.main()
