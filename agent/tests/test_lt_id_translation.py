# -*- coding: utf-8 -*-
"""双编号翻译：对话里引用 LT 编号 → 执行侧编号（清单⑩）。

用户在对话里说「查一下 LT-0003 的进度」「取消 LT-0003」「用 LT-0005 那个模型
预测」时，工具参数里带的是 Agent 自生成的 `LT-xxxx`；而执行侧（driver / MCP）
只认 `hadoop_task_id` / `model_id`。`LongTaskBackend.execute` 在转发前把
`task_id` / `model_id` / `from_task` 里的 LT 编号换成执行侧编号：

  * 登记表里有且有执行侧编号 → 替换后转发；
  * 登记表里有但执行侧没回传编号 → 结构化错误（查无可查，如实说）；
  * 登记表里没有 → 结构化错误（本对话内没这个长任务）；
  * 非 LT 编号（执行侧编号）原样透传，不影响既有调用。

全部用假工具后端 + 本地 Runtime：不连网、不起远端服务。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import Runtime  # noqa: E402
from agent.runtime.tool_exec import LongTaskBackend  # noqa: E402
from agent.tests.stage3_helpers import FakeToolBackend  # noqa: E402

HADOOP_ID = "20260925-184303-379a4b"


class TestLtIdTranslation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lt_id_translation_")
        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def _register(self, cid, envelope):
        """经执行出口登记一个长任务，返回 (LT 编号, 执行侧编号)。"""
        inner = FakeToolBackend({"start_cleaning_task": envelope})
        backend = LongTaskBackend(inner, self.rt.long_tasks, cid)
        ok, env = backend.execute("start_cleaning_task", {"tag": "登记用"})
        self.assertTrue(ok)
        return env["agent_task_id"], env["task_handle"]["hadoop_task_id"]

    def _run(self, cid, name, args, responses=None):
        inner = FakeToolBackend(responses or {name: {"ok": True, "echo": args}})
        backend = LongTaskBackend(inner, self.rt.long_tasks, cid)
        ok, env = backend.execute(name, dict(args))
        return ok, env, inner

    def test_lt_task_id_translated_to_hadoop_id(self):
        cid = self.rt.create_conversation(title="编号翻译")
        lt_id, hadoop_id = self._register(
            cid, {"ok": True, "task_id": HADOOP_ID, "status": "running"})
        self.assertEqual(HADOOP_ID, hadoop_id)

        ok, _env, inner = self._run(cid, "get_task_status", {"task_id": lt_id})
        self.assertTrue(ok)
        self.assertEqual(("get_task_status", {"task_id": HADOOP_ID}), inner.calls[-1])

    def test_model_id_and_from_task_also_translated(self):
        cid = self.rt.create_conversation(title="编号翻译")
        lt_id, hadoop_id = self._register(
            cid, {"ok": True, "task_id": HADOOP_ID, "status": "succeeded"})

        ok, _env, inner = self._run(
            cid, "predict_high_rating",
            {"model_id": lt_id, "user_id": "1", "movie_id": "1"})
        self.assertTrue(ok)
        self.assertEqual(HADOOP_ID, inner.calls[-1][1]["model_id"])

        ok, _env, inner = self._run(
            cid, "score_task", {"source": "task", "from_task": lt_id})
        self.assertTrue(ok)
        self.assertEqual(HADOOP_ID, inner.calls[-1][1]["from_task"])

    def test_unknown_lt_id_rejected_structured(self):
        cid = self.rt.create_conversation(title="编号翻译")
        ok, env, inner = self._run(cid, "get_task_status", {"task_id": "LT-9999"})
        self.assertFalse(ok)
        self.assertEqual("TASK_NOT_FOUND", env["error"]["code"])
        self.assertIn("LT-9999", env["error"]["message"])
        # 没搞懂的编号绝不原样丢给执行侧
        self.assertEqual([], inner.calls)

    def test_lt_without_hadoop_id_rejected_structured(self):
        cid = self.rt.create_conversation(title="编号翻译")
        lt_id, hadoop_id = self._register(cid, {"ok": True, "status": "running"})
        self.assertIsNone(hadoop_id)

        ok, env, inner = self._run(cid, "get_task_status", {"task_id": lt_id})
        self.assertFalse(ok)
        self.assertEqual("TASK_NOT_FOUND", env["error"]["code"])
        self.assertIn("尚无执行侧编号", env["error"]["message"])
        self.assertEqual([], inner.calls)

    def test_non_lt_task_id_passes_through(self):
        cid = self.rt.create_conversation(title="编号翻译")
        self._register(cid, {"ok": True, "task_id": HADOOP_ID, "status": "running"})

        ok, _env, inner = self._run(
            cid, "get_task_status", {"task_id": "20261020-010101-ffffffff"})
        self.assertTrue(ok)
        self.assertEqual(
            "20261020-010101-ffffffff", inner.calls[-1][1]["task_id"])

    def test_args_without_id_params_untouched(self):
        cid = self.rt.create_conversation(title="编号翻译")
        ok, _env, inner = self._run(cid, "list_tasks", {})
        self.assertTrue(ok)
        self.assertEqual(("list_tasks", {}), inner.calls[-1])

    def test_lt_id_from_other_conversation_not_reusable(self):
        """长任务登记按对话隔离：别的对话里的 LT 编号在这里查不到。"""
        cid_a = self.rt.create_conversation(title="对话A")
        cid_b = self.rt.create_conversation(title="对话B")
        lt_id, _hadoop_id = self._register(
            cid_a, {"ok": True, "task_id": HADOOP_ID, "status": "running"})

        ok, env, inner = self._run(cid_b, "get_task_status", {"task_id": lt_id})
        self.assertFalse(ok)
        self.assertEqual("TASK_NOT_FOUND", env["error"]["code"])
        self.assertEqual([], inner.calls)


if __name__ == "__main__":
    unittest.main()
