# -*- coding: utf-8 -*-
"""长任务远端探针（agent/runtime/long_task.py::RemoteTaskProbe）单元测试。

全部用**假客户端**：不连网、不起远端服务、不触模型。覆盖：
  * 用 `hadoop_task_id` 调远端 `get_task_status`；
  * 成功终态再取 `get_task_result`；
  * 与 `DriverTaskProbe` 同形状（status / stage / 进度 / 阶段计数 / result）；
  * 缺 `hadoop_task_id` 或信封失败时跳过（返回 None）。

另附「登记只认任务创建类工具」的回归：`wait_for_task`（长耗时但纯等待）不登记，
`start_cleaning_task` 登记。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import Runtime  # noqa: E402
from agent.runtime.long_task import RemoteTaskProbe  # noqa: E402
from agent.runtime.tool_exec import LongTaskBackend  # noqa: E402
from agent.tests.stage3_helpers import FakeToolBackend  # noqa: E402


class FakeClient(object):
    """假远端客户端：按预置状态回 `get_task_status` / `get_task_result`，记录调用。"""

    def __init__(self, status, result=None, status_envelope=None):
        self._status = status
        self._result = result if result is not None else {"scores": {}, "counts": {}}
        self._status_envelope = status_envelope
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "get_task_status":
            if self._status_envelope is not None:
                return self._status_envelope
            return {"ok": True, "task_id": args["task_id"], "status": self._status}
        if name == "get_task_result":
            envelope = {"ok": True, "task_id": args["task_id"]}
            envelope.update(self._result)
            return envelope
        return {"ok": False, "error": {"code": "UNKNOWN"}}


def test_probe_uses_hadoop_task_id():
    client = FakeClient("running")
    probe = RemoteTaskProbe(client)
    out = probe.probe({"hadoop_task_id": "H1", "agent_task_id": "LT-0001"})
    assert out["status"] == "running"
    assert client.calls[0] == ("get_task_status", {"task_id": "H1"})
    assert ("get_task_result", {"task_id": "H1"}) not in client.calls


def test_probe_fetches_result_on_success():
    client = FakeClient("succeeded", result={"counts": {"output_rows": 92}})
    probe = RemoteTaskProbe(client)
    out = probe.probe({"hadoop_task_id": "H1", "agent_task_id": "LT-0001"})
    assert ("get_task_result", {"task_id": "H1"}) in client.calls
    assert out["result"]["counts"]["output_rows"] == 92


def test_probe_mirrors_driver_shape():
    envelope = {"ok": True, "task_id": "H1", "status": "running",
                "stage": "清洗", "progress_percent": 40,
                "stage_index": 2, "stage_total": 9,
                "message": "进行中", "errors": []}
    probe = RemoteTaskProbe(FakeClient("running", status_envelope=envelope))
    out = probe.probe({"hadoop_task_id": "H1"})
    for key in ("status", "stage", "progress_percent", "stage_index",
                "stage_total", "message", "errors"):
        assert key in out, key
    assert out["progress_percent"] == 40
    assert out["stage_index"] == 2 and out["stage_total"] == 9


def test_probe_skips_without_hadoop_id():
    client = FakeClient("running")
    probe = RemoteTaskProbe(client)
    assert probe.probe({"agent_task_id": "LT-0001"}) is None
    assert client.calls == []


def test_probe_returns_none_on_failed_envelope():
    probe = RemoteTaskProbe(FakeClient(
        "running", status_envelope={"ok": False, "error": {"code": "X"}}))
    assert probe.probe({"hadoop_task_id": "H1"}) is None


class TestRegistrationOnlyForTaskCreators(unittest.TestCase):
    """登记修复：只对「任务创建类」工具登记长任务，纯等待不登记。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="remote_probe_")
        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self._close)
        self.cid = self.rt.create_conversation(title="探针登记测试")

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def test_wait_for_task_does_not_register(self):
        inner = FakeToolBackend({"wait_for_task": {
            "ok": True, "task_id": "H1", "status": "succeeded"}})
        backend = LongTaskBackend(inner, self.rt.long_tasks, self.cid)
        ok, env = backend.execute("wait_for_task", {"task_id": "H1"})
        self.assertTrue(ok)
        self.assertNotIn("long_task", env)
        self.assertEqual([], self.rt.long_tasks.tasks_of(self.cid))

    def test_start_cleaning_task_registers(self):
        inner = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": "H1", "status": "queued"}})
        backend = LongTaskBackend(inner, self.rt.long_tasks, self.cid)
        ok, env = backend.execute("start_cleaning_task", {})
        self.assertTrue(env.get("long_task"))
        self.assertEqual(1, len(self.rt.long_tasks.tasks_of(self.cid)))
