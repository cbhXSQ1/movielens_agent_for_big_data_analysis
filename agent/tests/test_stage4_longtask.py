# -*- coding: utf-8 -*-
"""阶段 4（ag-longtask）· T4.1 / T4.2 / T4.3：长任务挂起、完成唤醒续写、重启核对。

全部用**假模型**（FakeStreamTransport 承载的脚本化 provider）与**假工具后端**
驱动；执行侧用可注入的 `FakeTaskExecutor` 模拟（真实 Hadoop/MCP 未接入）。

覆盖：
  * T4.1 长任务登记：执行出口识别长耗时工具 → 句柄写进对话上下文 → `task_status`；
         当轮不阻塞、用户可继续（再发一轮照常流式完成）；
         工具表填真实 `long_running` / `estimated_seconds`。
  * T4.2 完成检测与中断注入：终态 → `task_status`(成功) + `result_payload` +
         `task_resumed` → 模型基于结果续写 → `message_delta` / `message_done`；
         续写落在**同一条对话**（节点父链含 role=task）。
  * 收束：同一条流内事件 seq 严格递增、不覆盖；两轮 message_delta 分属不同节点。
  * T4.3 重启核对：新建 Runtime 读回残留长任务，按执行侧 probe 核对 ——
         执行侧跑完的 → 成功；仍在跑的 → 恢复跟进；已消失的 → 已中止并释放。
"""
import os
import sys
import tempfile
import time
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import (MESSAGE_DELTA, MESSAGE_DONE, NODE_ADDED,  # noqa: E402
                           RESULT_PAYLOAD, TASK_RESUMED, TASK_STATUS,
                           FakeTaskExecutor, Runtime)
from agent.tests.stage3_helpers import (FakeToolBackend,  # noqa: E402
                                        make_provider, text_response,
                                        tool_response)
from agent.tests.stage3b_helpers import (event_types,  # noqa: E402
                                         streamed_text_response)


class LongTaskTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage4_longtask_")
        self.db = os.path.join(self.tmp, "rt.sqlite")
        self.art = os.path.join(self.tmp, "art")
        self.rt = Runtime(db_path=self.db, artifact_dir=self.art)
        self.addCleanup(self._safe_close, self.rt)
        self.cid = self.rt.create_conversation(title="长任务测试")

    def _safe_close(self, rt):
        try:
            rt.close()
        except Exception:
            pass

    def start_task(self, cid, hadoop_id="2026-jd01", scope="full",
                   provider_text="任务已提交，可继续对话。"):
        """跑一轮发起长任务，返回 (result, record)。"""
        provider = make_provider([
            tool_response("start_cleaning_task", {"scope": scope}),
            text_response(provider_text),
        ])
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": hadoop_id, "status": "queued"}})
        result = self.rt.run_turn(cid, "帮我清洗数据", provider=provider,
                                  tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        record = self.rt.long_tasks.tasks_of(cid)[-1]
        return result, record


class TestRegistration(LongTaskTestBase):
    def test_long_task_registered_without_blocking(self):
        """T4.1：发起长任务 → 立即登记（句柄进上下文）并推 task_status，当轮不阻塞。"""
        sub = self.rt.open_stream(self.cid)
        result, record = self.start_task(self.cid)

        # 当轮照常结束：模型回答已产出
        self.assertEqual("任务已提交，可继续对话。", result["final"])
        # 句柄字段齐全（接口文档 §3.8）
        self.assertTrue(record["agent_task_id"].startswith("LT-"))
        self.assertEqual("2026-jd01", record["hadoop_task_id"])
        self.assertTrue(record["name"])
        self.assertTrue(record["summary"])
        self.assertGreater(record["estimated_seconds"], 0)
        self.assertEqual("running", record["status"])
        # 挂到对话上下文，可被查询
        ctx = self.rt.get_conversation(self.cid)["context"]
        self.assertIn(record["agent_task_id"], ctx["long_tasks"])
        # 回合结果里也带出长任务视图
        self.assertEqual(record["agent_task_id"],
                         result["long_tasks"][0]["agent_task_id"])

        events = sub.drain()
        kinds = event_types(events)
        self.assertIn(TASK_STATUS, kinds)
        status_ev = [e for e in events if e["event"] == TASK_STATUS][0]
        self.assertEqual(record["agent_task_id"],
                         status_ev["data"]["agent_task_id"])
        self.assertEqual("running", status_ev["data"]["status"])
        self.assertEqual("运行", status_ev["data"]["status_cn"])
        # 本轮流式回答顺利完成（用户可在同一对话继续）
        self.assertIn(MESSAGE_DONE, kinds)

    def test_user_can_continue_while_task_running(self):
        """T4.1（不阻塞证据）：长任务仍在跑，同一对话下一轮照常受理并流式完成。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid)
        sub.drain()
        # 长任务仍是 running
        self.assertEqual("running", self.rt.long_tasks.get(
            self.cid, record["agent_task_id"])["status"])

        provider2 = make_provider([streamed_text_response(["好的，", "继续。"] )])
        r2 = self.rt.run_turn(self.cid, "再问一句", provider=provider2,
                              tool_backend=FakeToolBackend({}), facts={})
        self.assertTrue(r2["ok"], r2)
        kinds = event_types(sub.drain())
        self.assertIn(MESSAGE_DONE, kinds)
        self.assertGreaterEqual(kinds.count(MESSAGE_DELTA), 2)

    def test_tool_table_has_real_long_running_values(self):
        """T4.1：工具表快照填真实 long_running / estimated_seconds（替换占位）。"""
        sub = self.rt.open_stream(self.cid)
        tools = sub.get(timeout=1.0)["data"]["tools"]
        by_name = {t["name"]: t for t in tools}
        for name in ("start_cleaning_task", "start_clean_task", "score_task"):
            self.assertTrue(by_name[name]["long_running"], name)
            self.assertGreater(by_name[name]["estimated_seconds"], 0, name)
        self.assertFalse(by_name["list_tasks"]["long_running"])
        self.assertIsNone(by_name["list_tasks"]["estimated_seconds"])


class TestCompletionResume(LongTaskTestBase):
    def test_completion_resumes_model_continuation(self):
        """T4.2：长任务完成 → task_resumed + 模型续写（基于结果）推到同一条流。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid)
        aid = record["agent_task_id"]
        sub.drain()

        result = {"counts": {"input_rows": 100, "output_rows": 92,
                             "quarantined": 8}, "note": "清洗完成"}
        cont = make_provider([streamed_text_response(["任务已完成，", "清洗后剩 92 行。"])])
        out = self.rt.long_tasks.complete(self.cid, aid, result=result,
                                          provider=cont, facts={})
        self.assertTrue(out["resumed"], out)
        self.assertEqual("succeeded",
                         self.rt.long_tasks.get(self.cid, aid)["status"])

        events = sub.drain()
        kinds = event_types(events)
        # 终态状态、结果载荷、续写信号都在
        statuses = [e["data"]["status"] for e in events
                    if e["event"] == TASK_STATUS]
        self.assertIn("succeeded", statuses)
        self.assertIn(RESULT_PAYLOAD, kinds)
        self.assertIn(TASK_RESUMED, kinds)
        self.assertIn(MESSAGE_DELTA, kinds)
        self.assertIn(MESSAGE_DONE, kinds)
        # task_resumed 在续写增量之前
        self.assertLess(kinds.index(TASK_RESUMED),
                        min(i for i, k in enumerate(kinds) if k == MESSAGE_DELTA))
        resumed = [e for e in events if e["event"] == TASK_RESUMED][0]
        self.assertEqual(aid, resumed["data"]["agent_task_id"])
        self.assertIn("92", resumed["data"]["result_summary"])
        # 续写文本里含工具返回的数字
        joined = "".join(e["data"]["text"] for e in events
                         if e["event"] == MESSAGE_DELTA)
        self.assertIn("92", joined)

    def test_interrupt_injected_into_same_conversation(self):
        """T4.2：中断注入同一条对话——节点父链出现 role=task，模型据历史续写。"""
        _, record = self.start_task(self.cid)
        aid = record["agent_task_id"]
        cont = make_provider([streamed_text_response(["收到，", "继续汇报。"])])
        self.rt.long_tasks.complete(self.cid, aid,
                                    result={"note": "任务完成"},
                                    provider=cont, facts={})
        tree = self.rt.read_tree(self.cid)
        roles = [n["role"] for n in tree["nodes"]]
        self.assertEqual(["user", "assistant", "task", "assistant"], roles)
        # 续写消息进了 LLM 上下文：出现任务完成的中断文本
        messages = self.rt.get_messages(self.cid)
        self.assertTrue(any("后台任务完成" in (m.content or "") for m in messages))

    def test_failed_task_also_injects_interrupt(self):
        """T4.2：失败也是终态——同样注入中断，让模型向用户交代失败。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid, hadoop_id="2026-jd09")
        aid = record["agent_task_id"]
        sub.drain()
        cont = make_provider([text_response("任务失败了，原因是数据源缺失。")])
        self.rt.long_tasks.complete(self.cid, aid, status="failed",
                                    result={"note": "数据源缺失"},
                                    provider=cont, facts={})
        self.assertEqual("failed",
                         self.rt.long_tasks.get(self.cid, aid)["status"])
        kinds = event_types(sub.drain())
        self.assertIn(TASK_RESUMED, kinds)
        self.assertIn(MESSAGE_DONE, kinds)


    def test_poll_detects_execution_side_terminal_and_resumes(self):
        """T4.2 完成检测：周期巡检执行侧，终态才唤醒（未终态不打扰）。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid, hadoop_id="2026-poll")
        aid = record["agent_task_id"]
        sub.drain()

        executor = FakeTaskExecutor()
        executor.start(record)                       # 执行侧开始跑
        self.assertEqual([], self.rt.poll_long_tasks(executor))    # 未终态：不唤醒
        executor.complete(record, result={"note": "巡检发现完成"})
        cont = make_provider([text_response("巡检到任务已完成。")])
        out = self.rt.poll_long_tasks(executor, provider=cont, facts={})
        self.assertEqual(1, len(out))
        self.assertTrue(out[0]["resumed"])
        self.assertEqual("succeeded",
                         self.rt.long_tasks.get(self.cid, aid)["status"])
        kinds = event_types(sub.drain())
        self.assertIn(TASK_STATUS, kinds)
        self.assertIn(TASK_RESUMED, kinds)
        self.assertIn(MESSAGE_DONE, kinds)


class TestAsyncLongTask(LongTaskTestBase):
    def test_run_turn_async_registers_long_task(self):
        """T4.1 异步路径：run_turn_async 发起长任务同样登记并推 task_status。"""
        import asyncio

        sub = self.rt.open_stream(self.cid)
        provider = make_provider([
            tool_response("start_cleaning_task", {"scope": "full"}),
            text_response("已提交。"),
        ])
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": "2026-async", "status": "queued"}})
        result = asyncio.run(self.rt.run_turn_async(
            self.cid, "异步发起", provider=provider,
            tool_backend=backend, facts={}))
        self.assertTrue(result["ok"], result)
        tasks = self.rt.long_tasks.tasks_of(self.cid)
        self.assertEqual(1, len(tasks))
        self.assertEqual("2026-async", tasks[0]["hadoop_task_id"])
        self.assertEqual("running", tasks[0]["status"])
        self.assertIn(TASK_STATUS, event_types(sub.drain()))


class TestOrdering(LongTaskTestBase):
    def test_stream_events_ordered_and_not_overwritten(self):
        """收束：流式输出与完成续写共存于同一条流，seq 严格递增、互不覆盖。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid,
                                    provider_text="已发起，稍后汇报。")
        first = sub.drain()                                  # snapshot + 第一轮
        cont = make_provider([streamed_text_response(["完成：", "输出 92 行。"])])
        self.rt.long_tasks.complete(
            self.cid, record["agent_task_id"],
            result={"counts": {"output_rows": 92}, "note": "完成"},
            provider=cont, facts={})
        events = first + sub.drain()                         # 同一条流的两段，合起来看
        seqs = [e["seq"] for e in events]
        self.assertEqual(sorted(seqs), seqs)                 # 递增
        self.assertEqual(len(set(seqs)), len(seqs))          # 无重复（不覆盖）
        # 两轮 message_done 分属不同节点
        dones = [e for e in events if e["event"] == MESSAGE_DONE]
        self.assertEqual(2, len(dones))
        self.assertNotEqual(dones[0]["data"]["node_id"],
                            dones[1]["data"]["node_id"])
        # 每个节点的增量拼接等于它自己的 message_done 文本
        by_node = {}
        for event in events:
            if event["event"] == MESSAGE_DELTA:
                by_node.setdefault(event["data"]["node_id"], []).append(
                    event["data"]["text"])
        for done in dones:
            node_id = done["data"]["node_id"]
            if node_id in by_node:
                self.assertEqual(done["data"]["text"], "".join(by_node[node_id]))
        # 节点事件：第一轮 2 个（user/assistant）+ 续写 2 个（task/assistant）
        self.assertEqual(4, len([e for e in events
                                 if e["event"] == NODE_ADDED]))


class TestReconcileOnRestart(LongTaskTestBase):
    def test_reconcile_against_execution_side(self):
        """T4.3：重启后按执行侧核对残留长任务，进明确终态并释放占用。"""
        cid_a = self.rt.create_conversation(title="跑完的")
        cid_b = self.rt.create_conversation(title="还在跑")
        cid_c = self.rt.create_conversation(title="已消失")
        _, rec_a = self.start_task(cid_a, hadoop_id="2026-aa")
        _, rec_b = self.start_task(cid_b, hadoop_id="2026-bb")
        _, rec_c = self.start_task(cid_c, hadoop_id="2026-cc")

        # 执行侧（假）：A 跑成功、C 已消失、B 仍在运行
        executor = FakeTaskExecutor()
        executor.start(rec_a)
        executor.start(rec_b)
        executor.start(rec_c)
        executor.complete(rec_a, result={"note": "完成"})
        executor.vanish(rec_c)

        # 模拟进程结束 → 新 Runtime 指向同一 SQLite（本地记录仍在，但不自证）
        self.rt.close()
        rt2 = Runtime(db_path=self.db, artifact_dir=self.art)
        self.addCleanup(self._safe_close, rt2)
        sub_a = rt2.open_stream(cid_a)
        sub_b = rt2.open_stream(cid_b)
        sub_c = rt2.open_stream(cid_c)

        report = rt2.reconcile_long_tasks(executor)
        self.assertEqual(3, len(report))

        a = rt2.long_tasks.get(cid_a, rec_a["agent_task_id"])
        b = rt2.long_tasks.get(cid_b, rec_b["agent_task_id"])
        c = rt2.long_tasks.get(cid_c, rec_c["agent_task_id"])
        # 执行侧跑完的 → 成功且释放
        self.assertEqual("succeeded", a["status"])
        self.assertTrue(a["released"])
        # 仍在跑的 → 恢复跟进（保持 running）
        self.assertEqual("running", b["status"])
        self.assertFalse(b["released"])
        self.assertEqual([rec_b["agent_task_id"]],
                         [t["agent_task_id"] for t in rt2.long_tasks.active_tasks(cid_b)])
        # 已不存在的 → 进终态（已中止）并释放
        self.assertEqual("aborted", c["status"])
        self.assertTrue(c["released"])
        self.assertEqual([], rt2.long_tasks.active_tasks(cid_c))

        # 事件如实推出：A 成功、B 运行、C 已中止
        statuses_a = [e["data"]["status"] for e in sub_a.drain()
                      if e["event"] == TASK_STATUS]
        self.assertIn("succeeded", statuses_a)
        statuses_b = [e["data"]["status"] for e in sub_b.drain()
                      if e["event"] == TASK_STATUS]
        self.assertIn("running", statuses_b)
        statuses_c = [e["data"]["status"] for e in sub_c.drain()
                      if e["event"] == TASK_STATUS]
        self.assertIn("aborted", statuses_c)


def wait_until(predicate, timeout=3.0, interval=0.01):
    """轮询等待条件成立（后台线程测试用；超时返回最终判定）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class FakeProbe(object):
    """可注入假探针（与 `FakeTaskExecutor.probe` 同形状）：返回预置状态。"""

    def __init__(self, state=None):
        self.state = dict(state or {})
        self.calls = 0

    def set(self, state):
        self.state = dict(state or {})

    def probe(self, record):
        self.calls += 1
        return dict(self.state) if self.state else None


class TestProgressSync(LongTaskTestBase):
    """问题 2：长任务进度同步（运行中刷新进度、终态反映到任务视图）。"""

    def test_running_task_progress_synced_and_emitted(self):
        """活跃任务的 status/stage/progress（含 stage_index/total）被同步并推 task_status。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid, hadoop_id="2026-sync")
        aid = record["agent_task_id"]
        sub.drain()

        probe = FakeProbe({"status": "running", "stage": "清洗中",
                           "progress_percent": 42,
                           "stage_index": 3, "stage_total": 7})
        finished = self.rt.poll_long_tasks(probe)
        self.assertEqual([], finished)
        rec = self.rt.long_tasks.get(self.cid, aid)
        self.assertEqual(42, rec["progress_percent"])
        self.assertEqual("清洗中", rec["stage"])
        self.assertEqual(3, rec["stage_index"])
        self.assertEqual(7, rec["stage_total"])

        events = [e for e in sub.drain() if e["event"] == TASK_STATUS]
        self.assertTrue(events)
        self.assertEqual(42, events[-1]["data"]["progress_percent"])
        self.assertEqual("清洗中", events[-1]["data"]["stage"])
        self.assertEqual(3, events[-1]["data"]["stage_index"])

        # 无变化 → 不再重复推（不刷屏）
        self.rt.poll_long_tasks(probe)
        self.assertEqual(
            [], [e for e in sub.drain() if e["event"] == TASK_STATUS])

    def test_terminal_poll_finalizes_and_resumes(self):
        """执行侧终态 → 走既有完成路径（finalize + result_payload + 续写）。"""
        sub = self.rt.open_stream(self.cid)
        _, record = self.start_task(self.cid, hadoop_id="2026-term")
        aid = record["agent_task_id"]
        sub.drain()

        probe = FakeProbe({"status": "succeeded", "progress_percent": 100,
                           "stage": "发布", "result": {"note": "完成"}})
        cont = make_provider([text_response("任务已完成。")])
        finished = self.rt.poll_long_tasks(probe, provider=cont, facts={})
        self.assertEqual(1, len(finished))
        self.assertTrue(finished[0]["resumed"])
        self.assertEqual("succeeded",
                         self.rt.long_tasks.get(self.cid, aid)["status"])
        self.assertEqual(100,
                         self.rt.long_tasks.get(self.cid, aid)["progress_percent"])
        kinds = event_types(sub.drain())
        self.assertIn(RESULT_PAYLOAD, kinds)
        self.assertIn(TASK_RESUMED, kinds)
        self.assertIn(MESSAGE_DONE, kinds)

    def test_failed_status_reflected(self):
        """失败也是终态：任务视图反映 failed（不再停在「运行 0%」）。"""
        _, record = self.start_task(self.cid, hadoop_id="2026-fail")
        aid = record["agent_task_id"]
        probe = FakeProbe({"status": "failed", "progress_percent": 100,
                           "stage": "失败"})
        cont = make_provider([text_response("任务失败。")])
        finished = self.rt.poll_long_tasks(probe, provider=cont, facts={})
        self.assertEqual(1, len(finished))
        self.assertEqual("failed",
                         self.rt.long_tasks.get(self.cid, aid)["status"])


class TestBackgroundPoller(LongTaskTestBase):
    """问题 2：后台轮询的启动 / 周期 / 停止条件。"""

    def test_no_probe_means_no_background_polling(self):
        """不注入探针时不起后台轮询（既有测试与离线路径不受影响）。"""
        self.start_task(self.cid, hadoop_id="2026-noprobe")
        self.assertFalse(self.rt.long_tasks.poller.running)

    def test_poller_not_started_without_active_tasks(self):
        """无活跃任务时不起线程（停止条件）。"""
        self.rt.long_tasks.configure_polling(
            FakeProbe({"status": "running"}), interval=0.01)
        self.assertFalse(self.rt.long_tasks.poller.running)
        self.assertFalse(self.rt.long_tasks.poller.ensure_running())

    def test_poller_syncs_then_stops_on_terminal(self):
        """登记后自动起轮询；进度被同步；终态收束后线程自动退出。"""
        probe = FakeProbe({"status": "running", "stage": "清洗中",
                           "progress_percent": 10})
        cont = make_provider([text_response("已完成。")])
        self.rt.long_tasks.configure_polling(
            probe, interval=0.01, provider=cont, facts={})

        _, record = self.start_task(self.cid, hadoop_id="2026-bg")
        aid = record["agent_task_id"]
        # 登记后轮询在跑，进度被同步
        self.assertTrue(wait_until(
            lambda: (self.rt.long_tasks.get(self.cid, aid) or {})
            .get("progress_percent") == 10))
        self.assertTrue(self.rt.long_tasks.poller.running)

        # 执行侧到终态 → 轮询收束并自动停止
        probe.set({"status": "succeeded", "progress_percent": 100,
                   "result": {"note": "完成"}})
        self.assertTrue(wait_until(
            lambda: (self.rt.long_tasks.get(self.cid, aid) or {})
            .get("status") == "succeeded"))
        self.assertTrue(wait_until(
            lambda: not self.rt.long_tasks.poller.running),
            "无活跃任务后轮询应停止")


class TestDriverTaskProbe(unittest.TestCase):
    """默认真实探针：把 driver 的 status/result 信封映射成探针状态（不触网）。"""

    def test_maps_status_and_fetches_result_on_success(self):
        from agent import tools
        from agent.runtime import long_task

        old_status, old_result = tools.get_task_status, tools.get_task_result
        calls = {"status": [], "result": []}
        try:
            def fake_status(tid):
                calls["status"].append(tid)
                return {"ok": True, "task_id": tid, "status": "succeeded",
                        "stage": "发布", "progress_percent": 100,
                        "stage_index": 9, "stage_total": 9}

            def fake_result(tid):
                calls["result"].append(tid)
                return {"ok": True, "task_id": tid,
                        "counts": {"output_rows": 92}}

            tools.get_task_status = fake_status
            tools.get_task_result = fake_result
            seen = long_task.DriverTaskProbe().probe({"hadoop_task_id": "T-1"})
        finally:
            tools.get_task_status = old_status
            tools.get_task_result = old_result

        self.assertEqual("succeeded", seen["status"])
        self.assertEqual(100, seen["progress_percent"])
        self.assertEqual(9, seen["stage_index"])
        self.assertEqual(9, seen["stage_total"])
        self.assertEqual(92, seen["result"]["counts"]["output_rows"])
        self.assertEqual(["T-1"], calls["status"])
        self.assertEqual(["T-1"], calls["result"])

    def test_no_hadoop_id_or_failed_status_returns_none(self):
        from agent import tools
        from agent.runtime import long_task

        old_status = tools.get_task_status
        try:
            tools.get_task_status = lambda tid: {"ok": False,
                                                 "error": {"code": "X"}}
            probe = long_task.DriverTaskProbe()
            self.assertIsNone(probe.probe({"hadoop_task_id": "T"}))
            self.assertIsNone(probe.probe({}))
        finally:
            tools.get_task_status = old_status


if __name__ == "__main__":
    unittest.main()