# -*- coding: utf-8 -*-
"""删除对话（后端加性能力）：存储层 / 运行时 / HTTP 端点。

覆盖：
  * `ConversationStore.delete_conversation` 清掉该对话在 conversations / nodes /
    branches 表里的**全部**行；重复删除返回 False（没删到）；
  * 运行时删除后 `get_conversation` 为 None、`nodes_of` / `branches_of` 为空、
    `read_tree` 为 None；
  * `DELETE /api/conversations/{cid}` 成功 → `{"ok": true, "conversation_id": …}`，
    不存在（含重复删除）→ `CONVERSATION_NOT_FOUND`；删除后树 / 消息端点如实报不存在。

仍旧用假模型 + 假后端，不触网、不碰真实 driver。
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
from agent.tests.stage3_helpers import (FakeToolBackend, SAMPLE_TASKS,  # noqa: E402
                                        make_provider, text_response,
                                        tool_response)


class DeleteTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="conv_delete_")
        self.db = os.path.join(self.tmp, "rt.sqlite")
        self.art = os.path.join(self.tmp, "art")
        self.rt = Runtime(db_path=self.db, artifact_dir=self.art)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def backend(self):
        return FakeToolBackend({"list_tasks": SAMPLE_TASKS})

    def run_conversation(self, cid):
        """跑两轮 + 从节点 2 分一条支，制造节点与分支记录。"""
        p1 = make_provider([tool_response("list_tasks", {}),
                            text_response("最近共 2 个任务。")])
        r1 = self.rt.run_turn(cid, "之前跑过哪些任务？", provider=p1,
                              tool_backend=self.backend(), facts={})
        assert r1["ok"], r1
        p2 = make_provider([text_response("好的，已了解。")])
        r2 = self.rt.run_turn(cid, "明白了吗？", provider=p2,
                              tool_backend=self.backend(), facts={})
        assert r2["ok"], r2
        branch = self.rt.branch_from(cid, r1["node_id"])
        assert branch["ok"], branch
        return r1, r2, branch


class TestDeleteAtRuntimeLevel(DeleteTestBase):
    def test_delete_clears_conversation_nodes_and_branches(self):
        cid = self.rt.create_conversation(title="要删的对话")
        self.run_conversation(cid)

        self.assertTrue(self.rt.get_conversation(cid))
        self.assertEqual(4, len(self.rt.store.nodes_of(cid)))
        self.assertEqual(1, len(self.rt.store.branches_of(cid)))

        self.assertTrue(self.rt.delete_conversation(cid))

        self.assertIsNone(self.rt.get_conversation(cid))
        self.assertEqual([], self.rt.store.nodes_of(cid))
        self.assertEqual([], self.rt.store.branches_of(cid))
        self.assertIsNone(self.rt.read_tree(cid))

    def test_repeat_delete_reports_not_deleted(self):
        cid = self.rt.create_conversation(title="重复删除")
        self.run_conversation(cid)
        self.assertTrue(self.rt.delete_conversation(cid))
        self.assertFalse(self.rt.delete_conversation(cid))

    def test_delete_missing_conversation_returns_false(self):
        self.assertFalse(self.rt.delete_conversation("c9999"))

    def test_delete_does_not_touch_other_conversations(self):
        keep = self.rt.create_conversation(title="保留")
        gone = self.rt.create_conversation(title="删除")
        self.run_conversation(keep)
        self.run_conversation(gone)

        self.assertTrue(self.rt.delete_conversation(gone))
        self.assertIsNotNone(self.rt.get_conversation(keep))
        self.assertEqual(4, len(self.rt.store.nodes_of(keep)))
        self.assertEqual(1, len(self.rt.store.branches_of(keep)))


class TestDeleteHttpEndpoint(DeleteTestBase):
    def setUp(self):
        DeleteTestBase.setUp(self)
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))
        self.srv = http_api.create_server("127.0.0.1", 0, runtime=self.rt)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.srv.server_address[1]

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

    def test_delete_endpoint_success_then_not_found(self):
        cid = self.rt.create_conversation(title="端点删除")
        self.run_conversation(cid)

        status, env = self.request("DELETE", "/api/conversations/%s" % cid)
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual(cid, env["conversation_id"])

        # 删除后：树 / 消息端点如实报不存在，列表里也不再有它
        status, tree = self.request("GET", "/api/conversations/%s/tree" % cid)
        self.assertFalse(tree["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", tree["error"]["code"])

        status, msgs = self.request("GET", "/api/conversations/%s/messages" % cid)
        self.assertFalse(msgs["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", msgs["error"]["code"])

        status, listing = self.request("GET", "/api/conversations")
        self.assertNotIn(cid, [c["conversation_id"] for c in listing["conversations"]])

    def test_repeat_delete_is_structured_not_found(self):
        cid = self.rt.create_conversation(title="重复删")
        status, env = self.request("DELETE", "/api/conversations/%s" % cid)
        self.assertTrue(env["ok"], env)
        status, env2 = self.request("DELETE", "/api/conversations/%s" % cid)
        self.assertEqual(200, status)
        self.assertFalse(env2["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", env2["error"]["code"])

    def test_delete_unknown_is_structured_not_found(self):
        status, env = self.request("DELETE", "/api/conversations/c9999")
        self.assertEqual(200, status)
        self.assertFalse(env["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", env["error"]["code"])


if __name__ == "__main__":
    unittest.main()
