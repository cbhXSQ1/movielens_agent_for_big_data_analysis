# -*- coding: utf-8 -*-
"""端到端本地链路：经运行时工具后端 + 假 MCP 客户端走完整条长任务路径。

全程**不连网**（不碰 127.0.0.1:8900）：用本文件里的 `FakeRemoteClient`（假 MCP
客户端）顶替真实远端。覆盖一条完整链路：

    发起清洗（长任务） → get_task_status（进度流动） → 终态 → get_task_result

断言四件事：
  * 长任务被登记（`agent_task_id` + `hadoop_task_id` 写进对话上下文）；
  * 运行期进度经查状态流动（progress_percent / stage 同步进记录并推 task_status）；
  * 到终态后取回权威结果并推 `result_payload`；
  * 终态收集到产物（经远端 `get_task_result` 的 `artifacts[]`）并推 `artifact_added`。

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
from agent.runtime import (ARTIFACT_ADDED, MESSAGE_DONE, RESULT_PAYLOAD,  # noqa: E402
                           TASK_STATUS, RemoteTaskProbe, Runtime)


class FakeRemoteClient(mcp_contract.McpClient):
    """假 MCP 客户端（无网络）：受控返回「发起 / 查状态 / 取结果」三类信封。

    状态可由测试直接改写（`status` / `progress` / `stage`），从而驱动
    「运行中 → 终态」的时序；每次调用都记录在 `calls` 里，供断言双编号与调用次数。
    """

    interface_version = "1.0"

    def __init__(self, task_id="H-9001"):
        self.task_id = task_id
        self.status = "running"
        self.progress = 0
        self.stage = "运行"
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        if name == "start_cleaning_task":
            return {"ok": True, "task_id": self.task_id, "status": "queued"}
        if name == "get_task_status":
            return {"ok": True, "task_id": args.get("task_id"),
                    "status": self.status, "stage": self.stage,
                    "progress_percent": self.progress}
        if name == "get_task_result":
            if self.status != "succeeded":
                return {"ok": False, "error": {
                    "code": "TASK_NOT_FINISHED", "message": "任务尚未完成"}}
            return {
                "ok": True, "task_id": args.get("task_id"),
                "counts": {"input_rows": 1150241, "output_rows": 1000209,
                           "quarantined": 100830},
                "scores": {"after": {"composite": 99.95}},
                "note": "清洗与评分完成",
                "artifacts": [
                    {"path": "cleaned/movies/movies.dat", "kind": "data"},
                    {"path": "report.json", "kind": "report"},
                ],
            }
        if name == "list_schemes":
            return {"ok": True, "schemes": []}
        return {"ok": False, "error": {"code": "UNKNOWN", "message": name}}


def _no_model():
    """固定「无可用模型」：长任务终态走确定性收尾（不触模型、不连网）。"""
    raise ModelError(NOT_CONFIGURED, "测试固定无模型，走确定性收尾")


class TestRemoteLongTaskEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="e2e_remote_")
        self.client = FakeRemoteClient()
        self.rt = Runtime(
            db_path=os.path.join(self.tmp, "rt.sqlite"),
            artifact_dir=os.path.join(self.tmp, "art"),
            mcp_client=self.client,
            task_probe=RemoteTaskProbe(self.client),
            task_poll_interval=3600,
            provider_factory=_no_model)
        self.addCleanup(self.rt.close)
        self.cid = self.rt.create_conversation(title="远端长任务端到端")

    def test_full_path_start_status_terminal_result(self):
        sub = self.rt.open_stream(self.cid)

        # 1) 发起清洗（长任务）：经运行时工具后端 → 假 MCP 客户端
        result = self.rt.run_offline(self.cid, "start_cleaning_task", {},
                                     facts={}, enforce_policy=False)
        self.assertTrue(result["ok"], result)
        self.rt.long_tasks.stop_polling()      # 关掉后台轮询，改用受控 tick

        self.assertEqual(self.client.calls[0][0], "start_cleaning_task")
        records = self.rt.long_tasks.tasks_of(self.cid)
        self.assertEqual(1, len(records))
        record = records[0]
        aid = record["agent_task_id"]
        self.assertTrue(aid.startswith("LT-"))
        self.assertEqual("H-9001", record["hadoop_task_id"])
        self.assertEqual("running", record["status"])

        # 2) 进度流动：查状态 → 进度/阶段同步进记录
        self.client.progress, self.client.stage = 40, "清洗"
        self.rt.long_tasks.poller.tick()
        record = self.rt.long_tasks.get(self.cid, aid)
        self.assertEqual("running", record["status"])
        self.assertEqual(40, record["progress_percent"])
        self.assertEqual("清洗", record["stage"])
        self.assertIn(("get_task_status", {"task_id": "H-9001"}), self.client.calls)

        # 3) 到终态：查状态 → 收 get_task_result → 进终态并推结果
        self.client.status = "succeeded"
        self.client.progress, self.client.stage = 100, "成功"
        self.rt.long_tasks.poller.tick()
        record = self.rt.long_tasks.get(self.cid, aid)
        self.assertEqual("succeeded", record["status"])
        self.assertEqual(100, record["progress_percent"])
        self.assertIsInstance(record["result"], dict)
        # 取结果是按 hadoop_task_id（双编号纪律）
        self.assertIn(("get_task_result", {"task_id": "H-9001"}), self.client.calls)

        # 4) 结果/产物真的产出
        events = sub.drain()
        kinds = [e["event"] for e in events]
        self.assertIn(TASK_STATUS, kinds)
        self.assertIn(RESULT_PAYLOAD, kinds)
        self.assertIn(ARTIFACT_ADDED, kinds)
        self.assertIn(MESSAGE_DONE, kinds)

        payload = [e for e in events if e["event"] == RESULT_PAYLOAD][-1]["data"]
        self.assertEqual("succeeded", payload["task"]["status"])
        self.assertIn("data_volume", payload["blocks"])

        paths = {a["path"] for a in self.rt.visible_artifacts(self.cid)}
        self.assertIn("cleaned/movies/movies.dat", paths)
        self.assertIn("report.json", paths)


if __name__ == "__main__":
    unittest.main()
