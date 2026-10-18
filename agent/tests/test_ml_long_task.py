# -*- coding: utf-8 -*-
"""迭代二 ML 训练工具的长任务登记（AG-ML-3）。

训练类工具（`train_classifier` / `cluster_users` / `reduce_dimensions`）与
`start_cleaning_task` 同路：经 `LongTaskBackend` 异步受理后登记为长任务。按数据
契约 §0/§3.1「模型编号 = 任务编号」，执行侧回传的 `task_id` 即 `model_id`，只以
`hadoop_task_id` 一个字段入库，不另加 id 字段。

全部用**假工具后端 + 本地 Runtime**：不连网、不起远端服务、不触模型。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import Runtime  # noqa: E402
from agent.runtime.long_task import creates_task  # noqa: E402
from agent.runtime.tool_exec import LongTaskBackend  # noqa: E402
from agent.tests.stage3_helpers import FakeToolBackend  # noqa: E402

TRAINING_TOOLS = ("train_classifier", "cluster_users", "reduce_dimensions")

#: 非训练工具（读类）：即便属于迭代二 ML 能力，也不得登记长任务。
READ_TOOLS = ("get_ml_model", "list_ml_models", "predict_high_rating", "list_tasks")

MODEL_ID = "ml-20261018-120000-abcd"


class TestTrainingToolsCreateTask(unittest.TestCase):
    def test_training_tools_are_task_creators(self):
        for name in TRAINING_TOOLS:
            with self.subTest(tool=name):
                self.assertTrue(creates_task(name))

    def test_read_tools_are_not_task_creators(self):
        for name in READ_TOOLS:
            with self.subTest(tool=name):
                self.assertFalse(creates_task(name))


class TestTrainingToolRegistersLongTask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ml_long_task_")
        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def test_training_registers_and_hadoop_id_is_envelope_task_id(self):
        for name in TRAINING_TOOLS:
            with self.subTest(tool=name):
                cid = self.rt.create_conversation(title="训练登记")
                inner = FakeToolBackend({name: {
                    "ok": True, "task_id": MODEL_ID, "status": "queued"}})
                backend = LongTaskBackend(inner, self.rt.long_tasks, cid)
                ok, env = backend.execute(name, {"algorithm": "naive_bayes"})
                self.assertTrue(ok)
                self.assertTrue(env.get("long_task"))
                tasks = self.rt.long_tasks.tasks_of(cid)
                self.assertEqual(1, len(tasks))
                record = tasks[0]
                self.assertEqual(name, record.get("tool"))
                # 模型编号 = 任务编号：执行侧的 task_id 即以 hadoop_task_id 入库。
                self.assertEqual(env.get("task_id"), record.get("hadoop_task_id"))
                self.assertEqual(MODEL_ID, record.get("hadoop_task_id"))

    def test_read_tool_does_not_register(self):
        for name in READ_TOOLS:
            with self.subTest(tool=name):
                cid = self.rt.create_conversation(title="读工具不登记")
                inner = FakeToolBackend({name: {
                    "ok": True, "task_id": MODEL_ID, "status": "succeeded"}})
                backend = LongTaskBackend(inner, self.rt.long_tasks, cid)
                ok, env = backend.execute(name, {})
                self.assertTrue(ok)
                self.assertNotIn("long_task", env)
                self.assertEqual([], self.rt.long_tasks.tasks_of(cid))


if __name__ == "__main__":
    unittest.main()
