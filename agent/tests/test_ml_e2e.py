# -*- coding: utf-8 -*-
"""迭代二 ML 端到端本地链路（AG-ML-7）：训练 → 登记长任务 → 终态取结果。

全程**不连网**（不碰 127.0.0.1:8900）：用本文件里的 `FakeRemoteClient`（假 MCP
客户端）顶替真实远端。经运行时工具后端走完整条路径：

    train_classifier（异步受理） → 登记长任务 → get_task_status → 终态
        → get_task_result → result_payload 的 classification 板块

按数据契约 §0/§3.1「模型编号 = 任务编号」，执行侧回传的 `task_id` 即 `model_id`，
只以 `hadoop_task_id` 一个字段入库。断言三件事：
  * 训练工具经同一执行后端登记为长任务，且 `hadoop_task_id == task_id`；
  * 远程探针（`RemoteTaskProbe`）驱动终态，`get_task_result` 取回权威 ML 结果；
  * `result_payload` 的 `blocks` 含 `classification` 板块，取值为远端 `metrics` 原样。

假客户端在 `FakeRemoteClient` 里显式声明（本测试的唯一外部依赖替身）。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_contract  # noqa: E402
from agent.llm import ModelError, NOT_CONFIGURED  # noqa: E402
from agent.runtime import (RESULT_PAYLOAD, TASK_STATUS, RemoteTaskProbe,  # noqa: E402
                           Runtime)

MODEL_ID = "ml-20261018-120000-abcd"

#: 契约 §3.2 分类指标（0–1 小数、四位）；终态结果里 `metrics` 原样落到同名板块。
CLASSIFICATION_METRICS = {
    "f1": 0.8123, "auc": 0.9012, "precision": 0.80, "recall": 0.75,
    "pr_auc": 0.78, "threshold": 0.5,
    "confusion_matrix": {"tp": 10, "fp": 2, "tn": 8, "fn": 3},
    "positive_rate": 0.34,
    "feature_importance": [{"feature": "avg_rating", "importance": 0.31}],
    "split_sizes": {"train": 100, "valid": 20, "test": 30},
    "leakage": {"policy": "time_split", "verified_on": "2001-12-31"},
}
PARAMS = {"algorithm": "random_forest", "max_depth": 8}
ARTIFACTS = [{"id": "%s:model.joblib" % MODEL_ID, "kind": "joblib",
              "role": "model", "path": "model.joblib", "size": 1024}]

#: 终态权威结果信封：训练工具端侧回传的形状（`task_type` / `metrics` / `artifacts`）。
TERMINAL_RESULT = {
    "ok": True, "task_id": MODEL_ID, "status": "succeeded",
    "task_type": "classification", "metrics": CLASSIFICATION_METRICS,
    "params": PARAMS, "artifacts": ARTIFACTS,
}


class FakeRemoteClient(mcp_contract.McpClient):
    """假 MCP 客户端（无网络）：受控返回「训练 / 查状态 / 取结果」三类信封。

    状态可由测试直接改写（`status` / `progress` / `stage`），从而驱动
    「运行中 → 终态」的时序；每次调用都记录在 `calls` 里，供断言双编号与调用次数。
    """

    interface_version = "1.0"

    def __init__(self, task_id=MODEL_ID):
        self.task_id = task_id
        self.status = "running"
        self.progress = 0
        self.stage = "运行"
        self.result = dict(TERMINAL_RESULT)
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        if name == "train_classifier":
            return {"ok": True, "task_id": self.task_id, "status": "queued"}
        if name == "get_task_status":
            return {"ok": True, "task_id": args.get("task_id"),
                    "status": self.status, "stage": self.stage,
                    "progress_percent": self.progress}
        if name == "get_task_result":
            if self.status != "succeeded":
                return {"ok": False, "error": {
                    "code": "TASK_NOT_FINISHED", "message": "任务尚未完成"}}
            return dict(self.result)
        return {"ok": False, "error": {"code": "UNKNOWN", "message": name}}


def _no_model():
    """固定「无可用模型」：长任务终态走确定性收尾（不触模型、不连网）。"""
    raise ModelError(NOT_CONFIGURED, "测试固定无模型，走确定性收尾")


class TestMLEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ml_e2e_")
        self.client = FakeRemoteClient()
        self.rt = Runtime(
            db_path=os.path.join(self.tmp, "rt.sqlite"),
            artifact_dir=os.path.join(self.tmp, "art"),
            mcp_client=self.client,
            task_probe=RemoteTaskProbe(self.client),
            task_poll_interval=3600,
            provider_factory=_no_model)
        self.addCleanup(self.rt.close)
        self.cid = self.rt.create_conversation(title="ML 端到端")

    def test_train_then_terminal_result_yields_classification_block(self):
        sub = self.rt.open_stream(self.cid)

        # 1) 发起训练（经运行时工具后端 → 假 MCP 客户端）：异步受理即登记长任务
        result = self.rt.run_offline(
            self.cid, "train_classifier", {"algorithm": "random_forest"},
            facts={}, enforce_policy=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.client.calls[0][0], "train_classifier")
        self.rt.long_tasks.stop_polling()      # 关掉后台轮询，改用受控 tick

        records = self.rt.long_tasks.tasks_of(self.cid)
        self.assertEqual(1, len(records))
        record = records[0]
        aid = record["agent_task_id"]
        self.assertTrue(aid.startswith("LT-"))
        self.assertEqual("train_classifier", record["tool"])
        # 模型编号 = 任务编号：执行侧 task_id 即以 hadoop_task_id 入库
        self.assertEqual(MODEL_ID, record["hadoop_task_id"])
        self.assertEqual(self.client.task_id, record["hadoop_task_id"])
        self.assertEqual("running", record["status"])

        # 2) 到终态：远程探针查状态 → 取 get_task_result → 进终态并推结果
        self.client.status = "succeeded"
        self.client.progress, self.client.stage = 100, "成功"
        self.rt.long_tasks.poller.tick()
        record = self.rt.long_tasks.get(self.cid, aid)
        self.assertEqual("succeeded", record["status"])
        # 取结果是按 hadoop_task_id（双编号纪律），即模型编号
        self.assertIn(("get_task_result", {"task_id": MODEL_ID}), self.client.calls)

        # 3) 结果板块：classification 板块存在且取值为远端 metrics 原样
        events = sub.drain()
        kinds = [e["event"] for e in events]
        self.assertIn(TASK_STATUS, kinds)
        self.assertIn(RESULT_PAYLOAD, kinds)
        payload = [e for e in events if e["event"] == RESULT_PAYLOAD][-1]["data"]
        self.assertEqual("succeeded", payload["task"]["status"])
        blocks = payload["blocks"]
        self.assertIn("classification", blocks)
        self.assertEqual(CLASSIFICATION_METRICS, blocks["classification"])
        self.assertEqual(PARAMS, blocks["params"])
        self.assertEqual(ARTIFACTS, blocks["artifacts"])


if __name__ == "__main__":
    unittest.main()
