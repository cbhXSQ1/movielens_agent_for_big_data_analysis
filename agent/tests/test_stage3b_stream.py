# -*- coding: utf-8 -*-
"""阶段 3b（ag-runtime）· T3.4 / T3.5 / T3.6：每对话一条推送流、异步调度、工具表推送。

全部用**假模型**（FakeStreamTransport 承载的脚本化 provider）驱动，工具执行用假后端。
覆盖：
  * T3.4 事件按序到达（tools_snapshot → 多个 message_delta → message_done）；
  * T3.4 message_delta 带所属节点编号，拼接后等于 message_done 的整段文本；
  * T3.5 两条对话并发跑，各自事件只出现在自己的流里（隔离）；
  * T3.5 后台事件（task_status）与流式输出互不覆盖，共用同一条有序队列；
  * T3.5 异步运行时 run_turn_async（AsyncSqliteSaver）流式产出且可异步消费；
  * T3.6 订阅时收到完整工具表（含来源 / 是否长耗时 / 预计时长），变化时再推。
"""
import asyncio
import os
import sys
import tempfile
import threading
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import (MESSAGE_DELTA, MESSAGE_DONE, NODE_ADDED,  # noqa: E402
                           Runtime, TASK_STATUS, TOOLS_SNAPSHOT)
from agent.tests.stage3_helpers import (FakeToolBackend, SAMPLE_TASKS,  # noqa: E402
                                        error_provider, make_provider)
from agent.tests.stage3b_helpers import (event_types,  # noqa: E402
                                         streamed_text_response)


class StreamTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage3b_stream_")
        self.rt = Runtime(db_path=os.path.join(self.tmp, "rt.sqlite"),
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self.rt.close)
        self.cid = self.rt.create_conversation(title="流测试")

    def backend(self, responses=None):
        data = {"list_tasks": SAMPLE_TASKS}
        if responses:
            data.update(responses)
        return FakeToolBackend(data)


class TestStreamSequence(StreamTestBase):
    def test_events_arrive_in_order(self):
        """tools_snapshot → 多个 message_delta → message_done（真实事件序列）。"""
        sub = self.rt.open_stream(self.cid)
        provider = make_provider([streamed_text_response(["最近", "一切", "正常。"])])
        result = self.rt.run_turn(self.cid, "现在情况如何？", provider=provider,
                                  tool_backend=self.backend(), facts={})
        self.assertTrue(result["ok"], result)

        events = sub.drain()
        kinds = event_types(events)

        self.assertEqual(TOOLS_SNAPSHOT, kinds[0])          # 流建立即推工具表
        deltas = [k for k in kinds if k == MESSAGE_DELTA]
        self.assertGreaterEqual(len(deltas), 2)             # 确实是逐段
        self.assertIn(MESSAGE_DONE, kinds)
        # 所有 message_delta 都在 message_done 之前
        self.assertLess(max(i for i, k in enumerate(kinds) if k == MESSAGE_DELTA),
                        kinds.index(MESSAGE_DONE))
        # 拼接增量 == 完整回答
        joined = "".join(e["data"]["text"] for e in events
                         if e["event"] == MESSAGE_DELTA)
        self.assertEqual(result["final"], joined)
        # node_added 为两个节点各发一条
        self.assertEqual(2, kinds.count(NODE_ADDED))
        # 每条事件都属于本对话
        self.assertTrue(all(e["conversation_id"] == self.cid for e in events))

    def test_delta_carries_assistant_node_id(self):
        """message_delta 带所属节点编号；与 message_done / 结果里的 node_id 一致。"""
        sub = self.rt.open_stream(self.cid)
        provider = make_provider([streamed_text_response(["你", "好"])])
        result = self.rt.run_turn(self.cid, "打个招呼", provider=provider,
                                  tool_backend=self.backend(), facts={})
        events = sub.drain()
        delta_nodes = {e["data"]["node_id"] for e in events
                       if e["event"] == MESSAGE_DELTA}
        done = [e for e in events if e["event"] == MESSAGE_DONE][0]
        self.assertEqual({done["data"]["node_id"]}, delta_nodes)
        self.assertEqual(result["node_id"], done["data"]["node_id"])

    def test_error_event_on_model_failure(self):
        """模型出错：落一个 error 事件（且节点回填为错误说明，不静默）。"""
        sub = self.rt.open_stream(self.cid)
        provider = error_provider()             # 调用即抛结构化 ModelError
        result = self.rt.run_turn(self.cid, "随便问", provider=provider,
                                  tool_backend=self.backend(), facts={})
        kinds = event_types(sub.drain())
        self.assertFalse(result["ok"])
        self.assertIn("error", kinds)


class TestToolsSnapshot(StreamTestBase):
    def test_snapshot_has_interface_doc_fields(self):
        """T3.6：订阅时收到完整工具表，字段符合接口文档 §3.7。"""
        sub = self.rt.open_stream(self.cid)
        snapshot = sub.get(timeout=1.0)
        self.assertEqual(TOOLS_SNAPSHOT, snapshot["event"])
        tools = snapshot["data"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("list_tasks", names)
        self.assertIn("start_cleaning_task", names)
        for tool in tools:
            for key in ("name", "description", "parameters", "source",
                        "long_running", "estimated_seconds"):
                self.assertIn(key, tool)
            self.assertIn(tool["source"], ("local", "mcp"))
        # 参数带类型 / 是否必填 / 默认值
        samples = [t for t in tools if t["name"] == "get_samples"][0]
        params = {p["name"]: p for p in samples["parameters"]}
        self.assertEqual(5, params["limit"]["default"])
        self.assertEqual("cleaned", params["sample_type"]["default"])
        self.assertTrue(params["task_id"]["required"])

    def test_long_running_fields_have_real_estimates(self):
        """长耗时字段：阶段 4 已填真实预计时长（替换阶段 3b 的占位 None）。"""
        sub = self.rt.open_stream(self.cid)
        tools = sub.get(timeout=1.0)["data"]["tools"]
        by_name = {t["name"]: t for t in tools}
        self.assertTrue(by_name["start_cleaning_task"]["long_running"])
        self.assertGreater(by_name["start_cleaning_task"]["estimated_seconds"], 0)
        self.assertGreater(by_name["start_clean_task"]["estimated_seconds"], 0)
        self.assertGreater(by_name["score_task"]["estimated_seconds"], 0)
        self.assertFalse(by_name["list_tasks"]["long_running"])
        self.assertIsNone(by_name["list_tasks"]["estimated_seconds"])

    def test_tools_repushed_on_change(self):
        """工具表变化时再推一次完整工具表。"""
        sub = self.rt.open_stream(self.cid)
        self.assertEqual(1, len(sub.drain()))                 # 建立时那一条
        pushed = self.rt.notify_tools_changed(self.cid)
        self.assertTrue(pushed)
        again = sub.drain()
        self.assertEqual(1, len(again))
        self.assertEqual(TOOLS_SNAPSHOT, again[0]["event"])
        self.assertTrue(again[0]["data"]["tools"])


class TestIsolation(StreamTestBase):
    def test_concurrent_conversations_do_not_cross(self):
        """两条对话并发跑：各自的事件只出现在自己的流里（T3.5）。"""
        cid1 = self.rt.create_conversation(title="甲")
        cid2 = self.rt.create_conversation(title="乙")
        sub1 = self.rt.open_stream(cid1)
        sub2 = self.rt.open_stream(cid2)
        p1 = make_provider([streamed_text_response(["甲甲", "甲的回答"])])
        p2 = make_provider([streamed_text_response(["乙乙", "乙的回答"])])
        barrier = threading.Barrier(2)

        def run(cid, provider, text):
            barrier.wait()                                   # 尽量同时开跑
            res = self.rt.run_turn(cid, text, provider=provider,
                                   tool_backend=self.backend(), facts={})
            assert res["ok"], res

        threads = [threading.Thread(target=run, args=(cid1, p1, "问甲")),
                   threading.Thread(target=run, args=(cid2, p2, "问乙"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        e1, e2 = sub1.drain(), sub2.drain()
        self.assertTrue(all(e["conversation_id"] == cid1 for e in e1))
        self.assertTrue(all(e["conversation_id"] == cid2 for e in e2))
        text1 = "".join(e["data"].get("text", "") for e in e1)
        text2 = "".join(e["data"].get("text", "") for e in e2)
        self.assertIn("甲", text1)
        self.assertNotIn("乙", text1)
        self.assertIn("乙", text2)
        self.assertNotIn("甲", text2)

    def test_background_event_coexists_with_streaming(self):
        """同一对话内后台 task_status 与流式 message_delta 互不覆盖（有序追加）。"""
        sub = self.rt.open_stream(self.cid)
        # 模拟阶段 4 从后台线程推一条长任务状态
        self.rt.streams.emit(self.cid, TASK_STATUS, {
            "task_id": "2026-x", "status": "running", "progress_percent": 10})
        provider = make_provider([streamed_text_response(["已收到", "任务状态"])])
        self.rt.run_turn(self.cid, "任务跑哪了？", provider=provider,
                         tool_backend=self.backend(), facts={})

        events = sub.drain()
        kinds = event_types(events)
        self.assertIn(TASK_STATUS, kinds)
        self.assertIn(MESSAGE_DELTA, kinds)
        self.assertIn(MESSAGE_DONE, kinds)
        # seq 严格递增、无重复（只追加不覆盖）
        seqs = [e["seq"] for e in events]
        self.assertEqual(sorted(seqs), seqs)
        self.assertEqual(len(set(seqs)), len(seqs))
        # 后台事件的内容完好无损
        background = [e for e in events if e["event"] == TASK_STATUS][0]
        self.assertEqual(10, background["data"]["progress_percent"])


class TestAsyncRuntime(StreamTestBase):
    def test_run_turn_async_streams_and_consumes_async(self):
        """run_turn_async（AsyncSqliteSaver）流式产出，可异步消费。"""
        cid = self.rt.create_conversation(title="异步")
        sub = self.rt.open_stream(cid)
        provider = make_provider([streamed_text_response(["异步", "产出", "完成"])])

        async def scenario():
            async def consume():
                out = []
                async for event in sub.aiter():
                    out.append(event)
                return out

            task = asyncio.create_task(consume())
            result = await self.rt.run_turn_async(
                cid, "异步问一句", provider=provider,
                tool_backend=self.backend(), facts={})
            self.rt.streams.close(cid)          # 关流唤醒异步消费者
            return result, await task

        result, events = asyncio.run(scenario())
        self.assertTrue(result["ok"], result)
        kinds = event_types(events)
        self.assertEqual(TOOLS_SNAPSHOT, kinds[0])
        self.assertIn(MESSAGE_DONE, kinds)
        self.assertGreaterEqual(kinds.count(MESSAGE_DELTA), 2)
        joined = "".join(e["data"]["text"] for e in events
                         if e["event"] == MESSAGE_DELTA)
        self.assertEqual(result["final"], joined)

    def test_run_turn_async_two_conversations_isolated(self):
        """异步并发两条对话：事件不串流。"""
        cid1 = self.rt.create_conversation(title="甲")
        cid2 = self.rt.create_conversation(title="乙")
        sub1 = self.rt.open_stream(cid1)
        sub2 = self.rt.open_stream(cid2)
        p1 = make_provider([streamed_text_response(["甲一", "甲二"])])
        p2 = make_provider([streamed_text_response(["乙一", "乙二"])])

        async def scenario():
            r1, r2 = await asyncio.gather(
                self.rt.run_turn_async(cid1, "问甲", provider=p1,
                                       tool_backend=self.backend(), facts={}),
                self.rt.run_turn_async(cid2, "问乙", provider=p2,
                                       tool_backend=self.backend(), facts={}))
            return r1, r2

        r1, r2 = asyncio.run(scenario())
        self.assertTrue(r1["ok"] and r2["ok"])
        e1, e2 = sub1.drain(), sub2.drain()
        self.assertTrue(all(e["conversation_id"] == cid1 for e in e1))
        self.assertTrue(all(e["conversation_id"] == cid2 for e in e2))
        self.assertNotIn("乙", "".join(e["data"].get("text", "") for e in e1))
        self.assertNotIn("甲", "".join(e["data"].get("text", "") for e in e2))


if __name__ == "__main__":
    unittest.main()
