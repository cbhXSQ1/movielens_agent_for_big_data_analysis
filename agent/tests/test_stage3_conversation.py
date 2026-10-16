# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· T3.1 / T3.2：对话持久化、节点血缘、上下文 单测。

覆盖：
  * 对话有编号、可列出；上下文（当前任务编号 / 数据集版本 / 直调口径）可读写；
  * 每回合一个节点（编号、父节点、角色、时间、摘要），整数父节点链条；
  * 从任意节点回溯祖先链；
  * **进程重启**（关闭后新开 Runtime 指向同一 SQLite）后消息与节点都不丢。

仍旧用假模型 + 假后端，不触网、不碰真实 driver。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import ArtifactStore, Runtime, summarize  # noqa: E402
from agent.tests.stage3_helpers import (FakeToolBackend, SAMPLE_TASKS,  # noqa: E402
                                        make_provider, text_response,
                                        tool_response)


class ConversationTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage3_conv_")
        self.db = os.path.join(self.tmp, "rt.sqlite")
        self.art = os.path.join(self.tmp, "art")
        self.rt = Runtime(db_path=self.db, artifact_dir=self.art)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def _backend(self):
        return FakeToolBackend({"list_tasks": SAMPLE_TASKS})

    def _run_two_turns(self, rt, cid):
        p1 = make_provider([tool_response("list_tasks", {}),
                            text_response("最近共 2 个任务。")])
        r1 = rt.run_turn(cid, "之前跑过哪些任务？", provider=p1,
                         tool_backend=self._backend(), facts={})
        assert r1["ok"], r1
        p2 = make_provider([text_response("好的，已了解。")])
        r2 = rt.run_turn(cid, "明白了吗？", provider=p2,
                         tool_backend=self._backend(), facts={})
        assert r2["ok"], r2
        return r1, r2


class TestConversationBasics(ConversationTestBase):
    def test_create_and_list(self):
        cid = self.rt.create_conversation(title="第一次对话")
        self.assertTrue(cid.startswith("c"))
        ids = [c["id"] for c in self.rt.list_conversations()]
        self.assertIn(cid, ids)

    def test_conversation_not_found(self):
        result = self.rt.run_turn("does-not-exist", "你好",
                                  provider=make_provider([text_response("x")]),
                                  tool_backend=self._backend(), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", result["error"]["code"])

    def test_context_task_and_version(self):
        cid = self.rt.create_conversation()
        self.rt.set_context(cid, task_id="2026-x", data_version="v1")
        conv = self.rt.get_conversation(cid)
        self.assertEqual("2026-x", conv["context"]["task_id"])
        self.assertEqual("v1", conv["context"]["data_version"])

    def test_opts_remember_and_recall(self):
        cid = self.rt.create_conversation()
        self.rt.remember_opts(cid, {"exec_mode": "local", "scope": "sample"})
        self.assertEqual({"exec_mode": "local", "scope": "sample"},
                         self.rt.recall_opts(cid))


class TestNodesAndLineage(ConversationTestBase):
    def test_two_turns_form_chain(self):
        cid = self.rt.create_conversation(title="链条")
        self._run_two_turns(self.rt, cid)
        tree = self.rt.read_tree(cid)
        nodes = tree["nodes"]
        self.assertEqual(["user", "assistant", "user", "assistant"],
                         [n["role"] for n in nodes])
        self.assertIsNone(nodes[0]["parent_id"])          # 根
        self.assertEqual(nodes[0]["id"], nodes[1]["parent_id"])
        self.assertEqual(nodes[1]["id"], nodes[2]["parent_id"])
        self.assertEqual(nodes[2]["id"], nodes[3]["parent_id"])
        self.assertEqual(nodes[3]["id"], tree["current_node_id"])

    def test_ancestors_from_leaf(self):
        cid = self.rt.create_conversation(title="回溯")
        self._run_two_turns(self.rt, cid)
        tree = self.rt.read_tree(cid)
        leaf = tree["current_node_id"]
        chain = self.rt.ancestors(leaf)
        self.assertEqual([("user", 1), ("assistant", 2), ("user", 3)],
                         [(n["role"], n["seq"]) for n in chain])

    def test_node_summary_is_readable(self):
        cid = self.rt.create_conversation()
        node = self.rt.store.add_node(cid, None, role="user",
                                      summary=summarize("这是一个很长的摘要" + "啊" * 100),
                                      content="这是一个很长的摘要")
        self.assertTrue(node["summary"].endswith("…"))


class TestPersistenceAcrossRestart(ConversationTestBase):
    def test_nodes_and_messages_survive_restart(self):
        cid = self.rt.create_conversation(title="重启")
        self._run_two_turns(self.rt, cid)
        before_tree = self.rt.read_tree(cid)
        before_msgs = self.rt.get_messages(cid)
        self.rt.close()                                    # 模拟进程结束

        rt2 = Runtime(db_path=self.db, artifact_dir=self.art)
        self.addCleanup(rt2.close)
        after_tree = rt2.read_tree(cid)
        after_msgs = rt2.get_messages(cid)

        self.assertEqual([n["id"] for n in before_tree["nodes"]],
                         [n["id"] for n in after_tree["nodes"]])
        self.assertEqual(len(before_tree["nodes"]), 4)
        self.assertEqual(4, len(after_tree["nodes"]))
        self.assertEqual(before_tree["current_node_id"], after_tree["current_node_id"])
        self.assertEqual(len(before_msgs), len(after_msgs))
        self.assertGreaterEqual(len(after_msgs), 4)
        roles = {type(m).__name__ for m in after_msgs}
        self.assertIn("AIMessage", roles)
        self.assertIn("ToolMessage", roles)

    def test_task_context_recorded_on_start(self):
        cid = self.rt.create_conversation()
        provider = make_provider([
            tool_response("start_cleaning_task", {"tag": "demo"}),
            text_response("任务已提交，编号 2026-cccccccc。"),
        ])
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": "2026-cccccccc", "status": "queued"}})
        result = self.rt.run_turn(cid, "帮我清洗数据", provider=provider,
                                  tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["task_started"])
        self.assertEqual("2026-cccccccc", result["task_id"])
        self.assertEqual("2026-cccccccc",
                         self.rt.get_conversation(cid)["context"]["task_id"])


class TestArtifactStore(unittest.TestCase):
    def test_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="stage3_art_")
        store = ArtifactStore(tmp)
        payload = {"ok": True, "rows": list(range(50))}
        ref = store.save(payload, kind="tool_result")
        self.assertEqual(payload, store.load(ref))
        self.assertEqual(-1, store.size_of("no-such-ref"))


if __name__ == "__main__":
    unittest.main()
