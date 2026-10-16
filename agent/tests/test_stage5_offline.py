# -*- coding: utf-8 -*-
"""阶段 5（ag-offline）· T5.1 / T5.2：离线直调通道「工具名 + 可选参数，不经模型」。

全部用**假工具后端**（FakeToolBackend / 记录后端）驱动；模型层用一个「一被调用
即失败并计数」的 provider_factory 守着，证明离线直调**从不触模型**。

覆盖：
  * T5.1 补默认值：只给部分参数（甚至空参数）→ 用工具表默认值补齐 → 执行；
  * T5.1 不经模型：假 provider 计数为 0，而工具确实被执行；
  * T5.1 校验：非法工具名 / 非法参数（类型 / 枚举 / 范围 / 未知 / 缺必填）结构化拒绝；
  * T5.1 输出同形：回合信封与事件（node_added / message_delta / message_done /
         result_payload / error）与 LLM 模式同一套；
  * T5.2 共用：LLM 与离线两条路走到**同一个执行后端**与**同一张工具表**；
  * T5.2 长任务：离线直调长耗时工具同样登记长任务并向该对话流推 task_status。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import tool_specs  # noqa: E402
from agent.runtime import (ERROR, MESSAGE_DELTA, MESSAGE_DONE,  # noqa: E402
                           NODE_ADDED, RESULT_PAYLOAD, TASK_STATUS,
                           TOOLS_SNAPSHOT, Runtime, tool_table)
from agent.tests.stage3_helpers import (FakeToolBackend, SAMPLE_TASKS,  # noqa: E402
                                        make_provider, text_response,
                                        tool_response)
from agent.tests.stage3b_helpers import event_types  # noqa: E402


class OfflineTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage5_offline_")
        self.db = os.path.join(self.tmp, "rt.sqlite")
        self.art = os.path.join(self.tmp, "art")
        self.provider_calls = {"n": 0}

        def forbidden_factory():                 # 离线直调绝不该构造模型
            self.provider_calls["n"] += 1
            raise AssertionError("离线直调不应触达模型层")

        self.rt = Runtime(db_path=self.db, artifact_dir=self.art,
                          provider_factory=forbidden_factory)
        self.addCleanup(self._safe_close, self.rt)
        self.cid = self.rt.create_conversation(title="离线直调")

    def _safe_close(self, rt):
        try:
            rt.close()
        except Exception:
            pass


class TestDefaultsAndEnvelope(OfflineTestBase):
    def test_partial_params_filled_from_tool_table_and_executed(self):
        """T5.1：只给 task_id → 工具表默认值补齐 sample_type/table/n → 执行。"""
        sub = self.rt.open_stream(self.cid)
        backend = FakeToolBackend({"get_samples": {
            "ok": True, "samples": [], "total_available": 0,
            "type": "cleaned", "table": "movies"}})
        result = self.rt.run_offline(self.cid, "get_samples",
                                     {"task_id": "T-1"}, tool_backend=backend,
                                     facts={})

        self.assertTrue(result["ok"], result)
        self.assertEqual("offline", result["engine"])
        self.assertEqual("offline", result["mode"])
        # 默认值补齐后，交给后端的是「完整参数」
        self.assertEqual(
            [("get_samples", {"task_id": "T-1", "sample_type": "cleaned",
                              "table": "movies", "n": 5})], backend.calls)
        self.assertEqual({"sample_type": "cleaned", "table": "movies", "n": 5},
                         result["offline"]["defaults_filled"])
        # 模型层未被调用
        self.assertEqual(0, self.provider_calls["n"])
        self.assertFalse(result["llm"]["used"])

        # 事件形状与 LLM 模式同一套
        kinds = event_types(sub.drain())
        self.assertEqual(TOOLS_SNAPSHOT, kinds[0])
        self.assertEqual(2, kinds.count(NODE_ADDED))     # user + assistant
        self.assertIn(MESSAGE_DELTA, kinds)
        self.assertIn(MESSAGE_DONE, kinds)
        self.assertIn(RESULT_PAYLOAD, kinds)
        self.assertEqual(1, kinds.count(MESSAGE_DONE))

    def test_empty_params_filled_entirely_by_defaults(self):
        """T5.1：空参数调用 → 全部走工具表默认值（quiet 等布尔默认也补齐）。"""
        backend = FakeToolBackend({"quick_clean_demo": {
            "ok": True, "scores": {}, "counts": {}}})
        result = self.rt.run_offline(self.cid, "quick_clean_demo", None,
                                     tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual({"quiet": True}, result["offline"]["defaults_filled"])
        self.assertEqual([("quick_clean_demo", {"quiet": True})], backend.calls)


class TestNoModel(OfflineTestBase):
    def test_direct_call_does_not_touch_model_layer(self):
        """T5.1：离线直调不经模型——假 provider 计数为 0，而工具确实被执行。"""
        backend = FakeToolBackend({"list_tasks": SAMPLE_TASKS})
        result = self.rt.run_offline(self.cid, "list_tasks", {},
                                     tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual(0, self.provider_calls["n"])    # 模型层没被调用
        self.assertTrue(backend.calls)                    # 工具确实被执行


class TestValidation(OfflineTestBase):
    def test_unknown_tool_rejected_structurally(self):
        sub = self.rt.open_stream(self.cid)
        result = self.rt.run_offline(self.cid, "no_such_tool", {},
                                     tool_backend=FakeToolBackend({}), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("UNKNOWN_TOOL", result["error"]["code"])
        self.assertEqual("offline", result["engine"])
        kinds = event_types(sub.drain())
        self.assertIn(ERROR, kinds)                       # 结构化错误事件
        self.assertEqual(0, kinds.count(NODE_ADDED))      # 未落业务节点

    def test_invalid_type_rejected(self):
        result = self.rt.run_offline(
            self.cid, "get_samples", {"task_id": "T", "n": "five"},
            tool_backend=FakeToolBackend({}), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("USAGE", result["error"]["code"])
        self.assertIn("整数", result["error"]["message"])

    def test_invalid_enum_rejected(self):
        result = self.rt.run_offline(
            self.cid, "get_samples", {"task_id": "T", "sample_type": "bogus"},
            tool_backend=FakeToolBackend({}), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("USAGE", result["error"]["code"])
        self.assertIn("取值非法", result["error"]["message"])

    def test_out_of_range_rejected(self):
        result = self.rt.run_offline(
            self.cid, "get_samples", {"task_id": "T", "n": 999},
            tool_backend=FakeToolBackend({}), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("USAGE", result["error"]["code"])
        self.assertIn("上限", result["error"]["message"])

    def test_unknown_argument_rejected(self):
        result = self.rt.run_offline(
            self.cid, "get_samples", {"task_id": "T", "bogus": 1},
            tool_backend=FakeToolBackend({}), facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("USAGE", result["error"]["code"])
        self.assertIn("未知参数", result["error"]["message"])

    def test_missing_required_rejected(self):
        result = self.rt.run_offline(
            self.cid, "get_samples", {}, tool_backend=FakeToolBackend({}),
            facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("USAGE", result["error"]["code"])
        self.assertIn("缺少必填参数", result["error"]["message"])

    def test_policy_rejection_is_structured(self):
        """与 LLM 循环共用同一张策略表：原始数据明确不可读 → 结构化拒绝。"""
        result = self.rt.run_offline(
            self.cid, "start_cleaning_task", {},
            tool_backend=FakeToolBackend({}),
            facts={"raw_readable": False, "raw_dir": "/data/raw"})
        self.assertFalse(result["ok"])
        self.assertEqual("POLICY_REJECTED", result["error"]["code"])

    def test_tool_failure_reported_faithfully(self):
        """工具执行失败：占位节点回填错误说明 + error 事件，如实转述不编造。"""
        sub = self.rt.open_stream(self.cid)
        backend = FakeToolBackend({"get_task_result": {
            "ok": False,
            "error": {"code": "TASK_NOT_FINISHED", "message": "任务未完成"}}})
        result = self.rt.run_offline(self.cid, "get_task_result",
                                     {"task_id": "T"}, tool_backend=backend,
                                     facts={})
        self.assertFalse(result["ok"])
        self.assertEqual("TASK_NOT_FINISHED", result["error"]["code"])
        kinds = event_types(sub.drain())
        self.assertIn(ERROR, kinds)
        self.assertIn(NODE_ADDED, kinds)
        node = self.rt.store.get_node(result["node_id"])
        self.assertIn("任务未完成", node["content"])


class RecordingBackend(object):
    """记录每次执行的（名，参数），便于证明两条路走的是同一个后端。"""

    def __init__(self):
        self.calls = []

    def execute(self, name, args):
        self.calls.append((name, args))
        return True, {"ok": True, "tasks": []}


class TestSharedWithLlm(OfflineTestBase):
    def test_both_modes_share_same_backend_instance(self):
        """T5.2：LLM 主控与离线直调走到**同一个执行后端**（runtime._tool_backend）。"""
        backend = RecordingBackend()
        self.rt._tool_backend = backend
        # LLM 路径：模型规划 → 图里 tools 节点 → 同一后端
        provider = make_provider([tool_response("list_tasks", {}),
                                  text_response("最近没有任务。")])
        llm = self.rt.run_turn(self.cid, "有哪些任务？", provider=provider, facts={})
        self.assertTrue(llm["ok"], llm)
        # 离线路径：不经模型，直接同一后端
        off = self.rt.run_offline(self.cid, "list_tasks", {}, facts={})
        self.assertTrue(off["ok"], off)

        self.assertEqual([("list_tasks", {}), ("list_tasks", {})], backend.calls)
        self.assertEqual("llm", llm["engine"])
        self.assertEqual("offline", off["engine"])

    def test_same_tool_table_source(self):
        """T5.2：两条路共用同一张工具表（tool_specs / stream.tool_table 同源）。"""
        self.assertEqual(set(tool_specs.tool_names()),
                         {row["name"] for row in tool_table()})
        # 离线校验用的 schema 就是 LLM 工具表的 schema（参数名逐项一致）
        spec = tool_specs.schema_for("get_samples")
        row = [r for r in tool_table() if r["name"] == "get_samples"][0]
        self.assertEqual(set(spec["parameters"]["properties"]),
                         {p["name"] for p in row["parameters"]})
        self.assertEqual(["task_id"], list(spec["parameters"]["required"]))


class TestOfflineLongTask(OfflineTestBase):
    def test_offline_long_task_registers_and_pushes_status(self):
        """T5.2：离线直调长耗时工具 → 登记长任务 + 向该对话流推 task_status。"""
        sub = self.rt.open_stream(self.cid)
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": "2026-off", "status": "queued"}})
        result = self.rt.run_offline(self.cid, "start_cleaning_task",
                                     {"scope": "full"}, tool_backend=backend,
                                     facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual("2026-off", result["task_id"])
        self.assertTrue(result["task_started"])
        # 长任务已登记（句柄写进对话上下文）
        self.assertEqual(1, len(result["long_tasks"]))
        record = result["long_tasks"][0]
        self.assertTrue(record["agent_task_id"].startswith("LT-"))
        self.assertEqual("2026-off", record["hadoop_task_id"])
        self.assertEqual("running", record["status"])
        ctx = self.rt.get_conversation(self.cid)["context"]
        self.assertIn(record["agent_task_id"], ctx["long_tasks"])

        # 同一条对话流收到了 task_status（与 LLM 模式同一出口）
        events = sub.drain()
        kinds = event_types(events)
        self.assertIn(TASK_STATUS, kinds)
        status_event = [e for e in events if e["event"] == TASK_STATUS][0]
        self.assertEqual(record["agent_task_id"],
                         status_event["data"]["agent_task_id"])
        self.assertEqual("running", status_event["data"]["status"])
        self.assertIn(MESSAGE_DONE, kinds)
        self.assertIn(RESULT_PAYLOAD, kinds)


if __name__ == "__main__":
    unittest.main()
