# -*- coding: utf-8 -*-
"""阶段 7（ag-tree）· T7.1 / T7.2 / T7.3：对话树（分支 / 跳回 / 读树 / 端点）。

覆盖：
  * 分支保留旧支：主对话跑到 N 轮 → 从较早节点分支继续 → 主支后文原样在、
    新支独立、读树能同时看到两支（git 式）；
  * 跳回：从任一节点继续，往后的消息挂到该节点下；
  * 不污染：主支历史在新支活动后未改动，新支的消息不出现在主支；新支上下文
    接得上被选节点的祖先（检查点回放 / 自建表兜底）；
  * 多分支并存；
  * 端点实测：`GET …/tree`、`POST …/branches`、`GET …/nodes/{id}`。

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


def _sig(message):
    """消息的可比较签名（类型 + 文本），用于「主支历史未变」的断言。"""
    return (type(message).__name__, getattr(message, "content", ""))


class TreeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage7_tree_")
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

    def run_two_turns(self, cid):
        """主支跑两轮：user1/assistant1(带一次工具调用)/user2/assistant2。"""
        p1 = make_provider([tool_response("list_tasks", {}),
                            text_response("最近共 2 个任务。")])
        r1 = self.rt.run_turn(cid, "之前跑过哪些任务？", provider=p1,
                              tool_backend=self.backend(), facts={})
        assert r1["ok"], r1
        p2 = make_provider([text_response("好的，已了解。")])
        r2 = self.rt.run_turn(cid, "明白了吗？", provider=p2,
                              tool_backend=self.backend(), facts={})
        assert r2["ok"], r2
        return r1, r2


class TestBranchKeepsOldBranch(TreeTestBase):
    def test_branch_preserves_main_and_adds_independent_branch(self):
        cid = self.rt.create_conversation(title="分支")
        self.run_two_turns(cid)                       # 主支：节点 1..4
        before = self.rt.read_tree(cid)
        main_ids = [n["id"] for n in before["nodes"]]
        main_parents = {n["id"]: n["parent_id"] for n in before["nodes"]}
        self.assertEqual(4, len(main_ids))

        branch = self.rt.branch_from(cid, 2)          # 从 assistant1 另起一支
        self.assertTrue(branch["ok"], branch)
        self.assertEqual(2, branch["current_node_id"])

        p = make_provider([text_response("换一种问法后的回答。")])
        r = self.rt.run_turn(cid, "换个说法再问", provider=p,
                             tool_backend=self.backend(), facts={})
        self.assertTrue(r["ok"], r)

        tree = self.rt.read_tree(cid)
        nodes = {n["id"]: n for n in tree["nodes"]}

        # 旧后文（节点 1..4）原样保留
        self.assertEqual(set(main_ids) | {r["user_node_id"], r["node_id"]},
                         set(nodes))
        for nid in main_ids:
            self.assertEqual(main_parents[nid], nodes[nid]["parent_id"])

        # 新支独立：user5 挂在分支点 2 之下，assistant6 挂在 user5 之下
        self.assertEqual(2, nodes[r["user_node_id"]]["parent_id"])
        self.assertEqual(r["user_node_id"], nodes[r["node_id"]]["parent_id"])
        self.assertEqual(r["node_id"], tree["current_node_id"])

        # 被选节点 2 现在有两个子节点：主支的 3 与新支的 user5
        children = sorted(n["id"] for n in tree["nodes"] if n["parent_id"] == 2)
        self.assertEqual([3, r["user_node_id"]], children)

    def test_jump_back_to_a_node_hangs_children_under_it(self):
        cid = self.rt.create_conversation(title="跳回")
        self.run_two_turns(cid)
        result = self.rt.branch_from(cid, 1)          # 跳回第一个节点
        self.assertTrue(result["ok"], result)
        self.assertEqual(1, self.rt.get_conversation(cid)["current_node_id"])

        p = make_provider([text_response("从根重新出发。")])
        r = self.rt.run_turn(cid, "重来一次", provider=p,
                             tool_backend=self.backend(), facts={})
        self.assertTrue(r["ok"], r)
        nodes = {n["id"]: n for n in self.rt.read_tree(cid)["nodes"]}
        self.assertEqual(1, nodes[r["user_node_id"]]["parent_id"])
        self.assertEqual(r["user_node_id"], nodes[r["node_id"]]["parent_id"])

    def test_multiple_branches_coexist(self):
        cid = self.rt.create_conversation(title="多分支")
        self.run_two_turns(cid)

        b1 = self.rt.branch_from(cid, 2)
        p1 = make_provider([text_response("支一。")])
        r1 = self.rt.run_turn(cid, "支一的问题", provider=p1,
                              tool_backend=self.backend(), facts={})
        self.assertTrue(r1["ok"], r1)

        b2 = self.rt.branch_from(cid, 2)
        p2 = make_provider([text_response("支二。")])
        r2 = self.rt.run_turn(cid, "支二的问题", provider=p2,
                              tool_backend=self.backend(), facts={})
        self.assertTrue(r2["ok"], r2)

        self.assertNotEqual(b1["current_branch"], b2["current_branch"])
        tree = self.rt.read_tree(cid)
        self.assertEqual(2, len(tree["branches"]))
        children = sorted(n["id"] for n in tree["nodes"] if n["parent_id"] == 2)
        self.assertIn(r1["user_node_id"], children)
        self.assertIn(r2["user_node_id"], children)


class TestNoPollution(TreeTestBase):
    def test_main_history_untouched_and_branch_messages_isolated(self):
        cid = self.rt.create_conversation(title="隔离")
        self.run_two_turns(cid)
        main_before = self.rt.get_messages(cid)
        main_sig_before = [_sig(m) for m in main_before]

        branch = self.rt.branch_from(cid, 2)
        p = make_provider([text_response("这是新支里的回答。")])
        r = self.rt.run_turn(cid, "新支里的话", provider=p,
                             tool_backend=self.backend(), facts={})
        self.assertTrue(r["ok"], r)

        # 主支上下文一字未改
        main_after = self.rt.get_messages(cid)
        self.assertEqual(main_sig_before, [_sig(m) for m in main_after])

        # 新支的消息在**自己的线程**里，且接上了被选节点的祖先
        branch_msgs = self.rt.get_thread_messages(branch["current_branch"])
        texts = [getattr(m, "content", "") for m in branch_msgs]
        self.assertIn("之前跑过哪些任务？", texts)       # 祖先内容被带入
        self.assertIn("这是新支里的回答。", texts)       # 本轮回答
        self.assertNotIn("这是新支里的回答。", [t for _, t in main_sig_before])
        self.assertNotIn("新支里的话", [t for _, t in main_sig_before])

    def test_offline_branch_does_not_touch_main_nodes(self):
        cid = self.rt.create_conversation(title="离线分支")
        self.run_two_turns(cid)
        main_ids = [n["id"] for n in self.rt.read_tree(cid)["nodes"]]

        branch = self.rt.branch_from(cid, 2)
        result = self.rt.run_offline(cid, "list_tasks", params={},
                                     tool_backend=self.backend(), facts={})
        self.assertTrue(result["ok"], result)

        tree = self.rt.read_tree(cid)
        nodes = {n["id"]: n for n in tree["nodes"]}
        for nid in main_ids:
            self.assertIn(nid, nodes)
        self.assertEqual(2, nodes[result["user_node_id"]]["parent_id"])
        self.assertEqual(result["node_id"], tree["current_node_id"])
        self.assertEqual(branch["current_branch"],
                         nodes[result["node_id"]]["thread_id"])


class TestSchemaMigration(unittest.TestCase):
    """老库（阶段 3 建的表，无分支列）打开时自动补列，不丢数据。"""

    OLD_SCHEMA = """
    CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        current_node_id INTEGER, context_json TEXT);
    CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL, parent_id INTEGER, role TEXT NOT NULL,
        summary TEXT, content TEXT, content_ref TEXT, created_at REAL NOT NULL,
        seq INTEGER NOT NULL);
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """

    def test_open_old_db_adds_branch_columns(self):
        import sqlite3

        from agent.runtime.store import ConversationStore

        tmp = tempfile.mkdtemp(prefix="stage7_migrate_")
        db = os.path.join(tmp, "old.sqlite")
        conn = sqlite3.connect(db)
        conn.executescript(self.OLD_SCHEMA)
        conn.execute("INSERT INTO conversations(id, title, created_at, updated_at,"
                     " current_node_id, context_json) VALUES('c0001','旧',1,1,2,'{}')")
        conn.commit()
        conn.close()

        store = ConversationStore(db)
        self.addCleanup(store.close)
        conv = store.get_conversation("c0001")
        self.assertEqual("旧", conv["title"])
        self.assertIsNone(conv["current_branch"])
        branch = store.add_branch("c0001", 2, parent_thread="c0001")
        store.update_conversation("c0001", current_branch=branch["thread_id"])
        self.assertEqual(branch["thread_id"],
                         store.get_conversation("c0001")["current_branch"])
        self.assertEqual(1, len(store.branches_of("c0001")))


class TestHttpEndpoints(TreeTestBase):
    def setUp(self):
        TreeTestBase.setUp(self)
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

    def test_tree_branches_and_single_node_endpoints(self):
        cid = self.rt.create_conversation(title="端点")
        self.run_two_turns(cid)

        # 读整棵树
        status, tree = self.request("GET", "/api/conversations/%s/tree" % cid)
        self.assertEqual(200, status)
        self.assertTrue(tree["ok"], tree)
        self.assertEqual(4, len(tree["nodes"]))
        for key in ("node_id", "parent_id", "role", "created_at", "summary"):
            self.assertIn(key, tree["nodes"][0])
        self.assertEqual(tree["nodes"][-1]["node_id"], tree["current_node_id"])
        self.assertEqual(cid, tree["current_branch"])

        # 从节点继续（POST /branches）
        status, branched = self.request(
            "POST", "/api/conversations/%s/branches" % cid,
            {"from_node_id": 2})
        self.assertEqual(200, status)
        self.assertTrue(branched["ok"], branched)
        self.assertEqual(2, branched["from_node_id"])
        self.assertIn("thread_id", branched["branch"])
        self.assertNotEqual(cid, branched["branch"]["thread_id"])

        # 分支后新发一条离线消息，落在新支
        status, msg = self.request(
            "POST", "/api/conversations/%s/messages" % cid,
            {"mode": "offline", "tool": "list_tasks", "params": {}})
        self.assertEqual(200, status)
        self.assertTrue(msg["ok"], msg)

        status, tree2 = self.request("GET", "/api/conversations/%s/tree" % cid)
        nodes = {n["node_id"]: n for n in tree2["nodes"]}
        self.assertEqual(2, nodes[msg["user_node_id"]]["parent_id"])
        self.assertEqual(msg["node_id"], tree2["current_node_id"])
        self.assertEqual(1, len(tree2["branches"]))

        # 读单节点
        status, node = self.request(
            "GET", "/api/conversations/%s/nodes/%d" % (cid, msg["node_id"]))
        self.assertEqual(200, status)
        self.assertTrue(node["ok"], node)
        self.assertEqual("assistant", node["node"]["role"])
        self.assertIn("content", node["node"])
        self.assertEqual(cid, node["node"]["conversation_id"])

    def test_unknown_node_is_structured_error(self):
        cid = self.rt.create_conversation(title="坏节点")
        self.run_two_turns(cid)
        status, env = self.request(
            "POST", "/api/conversations/%s/branches" % cid,
            {"from_node_id": 99999})
        self.assertEqual(200, status)
        self.assertFalse(env["ok"])
        self.assertEqual("NODE_NOT_FOUND", env["error"]["code"])


if __name__ == "__main__":
    unittest.main()
