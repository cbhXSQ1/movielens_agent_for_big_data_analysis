# -*- coding: utf-8 -*-
"""贯穿（ag-tests）· TX.1–TX.6 收口补充测试。

本文件只做「验证与收口」，不新增功能：

  * TX.1 假模型脚手架（可控、可重放）—— 此前脚手架被各阶段测试广泛使用，但
    **脚手架自身**的「可注入预定输出 / 可注入故障 / 可重放（同脚本→同事件）」
    没有专门断言，这里补齐；
  * TX.2 多步调用 —— 此前只测了「单工具 + 最终回答」，这里补「一回合内两步
    工具调用 + 流式分片」；
  * TX.4 数字护栏正向断言加固 —— 多来源数字（工具标量 / 数组长度 / 用户原话）
    一起放行、且护栏**不修改**回答；并留一个负向对照证明不是「永远放行」；
  * TX.5 分支隔离持久性 —— 分支活动后主支消息与节点内容一字未改，且**重启后**
    仍不被污染（隔离不是内存假象）。

全部用假模型 / 假后端 / 假执行器驱动，不触网、不碰真实 driver。
"""
import os
import sys
import tempfile
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import llm, llm_config, number_guard  # noqa: E402
from agent.llm import (FakeStreamTransport, UPSTREAM_ERROR, ModelError,  # noqa: E402
                       event_to_dict)
from agent.llm.config import ModelConfig  # noqa: E402
from agent.runtime import Runtime  # noqa: E402
from agent.runtime.long_task import FakeTaskExecutor  # noqa: E402
from agent.tests.stage3_helpers import (FakeToolBackend, SAMPLE_TASKS,  # noqa: E402
                                        ScriptedTransport, make_provider, sse,
                                        text_response, tool_response)
from agent.tests.stage3b_helpers import (event_types,  # noqa: E402
                                         streamed_text_response)

MESSAGES = [{"role": "user", "content": "你好"}]


def _scripted_config():
    base = llm_config.LLMConfig({
        "supported": True, "enabled": True,
        "api_base": "https://api.example.com/v1", "api_key": "sk-test",
        "model": "fake-model",
    })
    return ModelConfig(base, {"provider": "openai"})


def _events(provider):
    """把一次模型流聚成可比较的 dict 列表（供「可重放」断言）。"""
    return [event_to_dict(event) for event in provider.stream_chat(MESSAGES)]


# ===========================================================================
# TX.1 假模型脚手架（可控、可重放）
# ===========================================================================

class TestTx1FakeModelControllable(unittest.TestCase):
    def test_transport_injects_scripted_chunks_in_order(self):
        """可注入预定输出：片段按脚本顺序产出，并记录收到的请求。"""
        transport = FakeStreamTransport(["aa", "bb", "cc"])
        request = {"url": "u", "headers": {}, "body": b"", "timeout": 1.0}
        self.assertEqual(["aa", "bb", "cc"], list(transport.post_stream(request)))
        self.assertEqual([request], transport.requests)

    def test_transport_injects_scripted_error(self):
        """可注入预置故障：迭代即抛，模拟鉴权 / 超时 / 网络错误。"""
        transport = FakeStreamTransport(error=ModelError(UPSTREAM_ERROR, "假故障"))
        with self.assertRaises(ModelError) as ctx:
            list(transport.post_stream({}))
        self.assertEqual(UPSTREAM_ERROR, ctx.exception.code)

    def test_scripted_transport_plays_in_order_then_repeats_last(self):
        """多轮可控：按脚本顺序推进；脚本用尽后重复最后一个（便于上限类用例）。"""
        scripted = ScriptedTransport([["one"], ["two"]])
        self.assertEqual(["one"], list(scripted.post_stream({})))
        self.assertEqual(["two"], list(scripted.post_stream({})))
        self.assertEqual(["two"], list(scripted.post_stream({})))

    def test_output_is_replayable(self):
        """可重放：同一份脚本两次装配，产出**逐事件相同**（确定性）。"""
        script = [sse({"choices": [{"index": 0, "delta": {"content": "你好"}}]}),
                  sse({"choices": [{"index": 0, "delta": {"tool_calls": [
                      {"index": 0, "id": "c1", "function": {
                          "name": "list_tasks", "arguments": "{}"}}]}}]}),
                  sse({"choices": [{"index": 0, "delta": {},
                                    "finish_reason": "tool_calls"}]}),
                  "data: [DONE]\n\n"]
        first = _events(llm.build_provider(_scripted_config(),
                                           FakeStreamTransport(list(script))))
        second = _events(llm.build_provider(_scripted_config(),
                                            FakeStreamTransport(list(script))))
        self.assertEqual(first, second)
        self.assertTrue(any(e["type"] == "tool_call" for e in first))

    def test_fake_tool_backend_returns_and_records(self):
        """工具侧可控：返回预置信封并记录调用；未登记工具给结构化 USAGE。"""
        backend = FakeToolBackend({"list_tasks": {"ok": True, "total": 2}})
        ok, envelope = backend.execute("list_tasks", {})
        self.assertTrue(ok)
        self.assertEqual(2, envelope["total"])
        self.assertEqual([("list_tasks", {})], backend.calls)
        bad_ok, bad = backend.execute("no_such_tool", {})
        self.assertFalse(bad_ok)
        self.assertEqual("USAGE", bad["error"]["code"])

    def test_fake_task_executor_is_controllable(self):
        """长任务侧可控：start / complete / vanish 决定 probe 的真实状态。"""
        executor = FakeTaskExecutor()
        record = {"hadoop_task_id": "2026-h1", "agent_task_id": "LT-0001"}
        self.assertIsNone(executor.probe(record))              # 未开始
        executor.start(record)
        self.assertEqual("running", executor.probe(record)["status"])
        executor.complete(record, result={"note": "完成"})
        seen = executor.probe(record)
        self.assertEqual("succeeded", seen["status"])
        self.assertEqual("完成", seen["result"]["note"])
        executor.vanish(record)
        self.assertIsNone(executor.probe(record))              # 已消失


# ===========================================================================
# TX.2 多步调用与流式分片（加固：一回合两步工具 + 分片）
# ===========================================================================

class RuntimeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tx_verify_")
        self.db = os.path.join(self.tmp, "rt.sqlite")
        self.rt = Runtime(db_path=self.db,
                          artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.rt.close()
        except Exception:
            pass

    def backend(self, extra=None):
        data = {"list_tasks": SAMPLE_TASKS}
        if extra:
            data.update(extra)
        return FakeToolBackend(data)


class TestTx2MultiStepAndFragments(RuntimeTestBase):
    def test_two_tools_in_one_turn_then_fragmented_final(self):
        """一回合内顺序调用两个工具，再以**多分片**收尾；事件逐段到达。"""
        cid = self.rt.create_conversation(title="多步")
        sub = self.rt.open_stream(cid)
        provider = make_provider([
            tool_response("list_tasks", {}),
            tool_response("get_task_status", {"task_id": "2026-aaaaaaaa"}),
            streamed_text_response(["共 2 个任务，", "第一个已成功。"]),
        ])
        backend = self.backend({
            "get_task_status": {"ok": True, "task_id": "2026-aaaaaaaa",
                                "status": "succeeded"}})
        result = self.rt.run_turn(cid, "列出任务并看第一个的状态",
                                  provider=provider, tool_backend=backend,
                                  facts={})
        self.assertTrue(result["ok"], result)

        # 多步：两次工具调用，顺序与参数可核
        self.assertEqual(["list_tasks", "get_task_status"],
                         [step["tool"] for step in result["steps"]])
        self.assertEqual([("list_tasks", {}),
                          ("get_task_status", {"task_id": "2026-aaaaaaaa"})],
                         backend.calls)
        self.assertEqual("共 2 个任务，第一个已成功。", result["final"])

        # 分片：message_delta 逐段到达，拼接等于最终回答
        events = sub.drain()
        kinds = event_types(events)
        self.assertGreaterEqual(kinds.count("message_delta"), 2)
        joined = "".join(e["data"]["text"] for e in events
                         if e["event"] == "message_delta")
        self.assertEqual(result["final"], joined)


# ===========================================================================
# TX.4 数字护栏（加固：正向断言 + 不改回答 + 负向对照）
# ===========================================================================

class TestTx4GuardPositivePath(unittest.TestCase):
    def test_multi_source_numbers_all_pass(self):
        """正向：工具标量 / 数组长度 / 用户原话的数字同屏出现，全部放行。"""
        steps = [{"tool": "get_task_result", "args": {}, "ok": True, "envelope": {
            "ok": True,
            "scores": {"after": {"Accurate": 0.91}},
            "counts": {"quarantine": {"total": 7}},
            "quarantine_samples": [{"line_no": 1}, {"line_no": 2}],
        }}]
        auth = number_guard.build_authorized("给我 5 条", steps)
        final = "准确率 0.91，隔离 7 行，样例 2 条（你要的 5 条）。"
        self.assertEqual([], number_guard.find_unauthorized(final, auth))
        self.assertEqual(set(),
                         number_guard.unauthorized_numbers(final, "给我 5 条", steps))

    def test_negative_control_not_always_pass(self):
        """负向对照：换一个无来源的数字必须被拦（证明护栏不是「永远放行」）。"""
        steps = [{"tool": "list_schemes", "args": {}, "ok": True,
                  "envelope": {"ok": True, "total": 2}}]
        auth = number_guard.build_authorized("有哪些方案？", steps)
        records = number_guard.find_unauthorized("方案有 2 个，编号 888888 待定。", auth)
        self.assertIn("888888", [r["number"] for r in records])
        self.assertNotIn("2", [r["number"] for r in records])


class TestTx4GuardAtRuntime(RuntimeTestBase):
    def test_allowed_answer_is_not_rewritten(self):
        """正向闸门：数字均有来源 → 放行且回答**一字未改**（护栏只校验不改写）。"""
        cid = self.rt.create_conversation(title="护栏正向")
        provider = make_provider([
            tool_response("list_tasks", {}),
            text_response("共 2 个任务。"),
        ])
        result = self.rt.run_turn(cid, "有几个任务？", provider=provider,
                                  tool_backend=self.backend(), facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual("共 2 个任务。", result["final"])
        self.assertIsNone(result["error"])


# ===========================================================================
# TX.5 树血缘与分支隔离（加固：主支未污染 + 重启后仍隔离）
# ===========================================================================

def _sig(message):
    return (type(message).__name__, getattr(message, "content", ""))


class TestTx5BranchIsolationDurable(RuntimeTestBase):
    def _two_turns(self, cid):
        p1 = make_provider([tool_response("list_tasks", {}),
                            text_response("最近共 2 个任务。")])
        r1 = self.rt.run_turn(cid, "之前跑过哪些任务？", provider=p1,
                              tool_backend=self.backend(), facts={})
        self.assertTrue(r1["ok"], r1)
        p2 = make_provider([text_response("好的，已了解。")])
        r2 = self.rt.run_turn(cid, "明白了吗？", provider=p2,
                              tool_backend=self.backend(), facts={})
        self.assertTrue(r2["ok"], r2)

    def test_main_branch_untouched_after_branch_and_restart(self):
        cid = self.rt.create_conversation(title="隔离持久")
        self._two_turns(cid)

        main_msgs_before = [_sig(m) for m in self.rt.get_messages(cid)]
        main_content_before = {n["id"]: n["content"]
                               for n in self.rt.store.nodes_of(cid)}
        self.assertEqual(4, len(main_content_before))

        # 从节点 2 另起一支，并在新支里说一句话
        branch = self.rt.branch_from(cid, 2)
        self.assertTrue(branch["ok"], branch)
        p = make_provider([text_response("这是新支里的回答。")])
        r = self.rt.run_turn(cid, "新支里的话", provider=p,
                             tool_backend=self.backend(), facts={})
        self.assertTrue(r["ok"], r)

        # 1) 主支消息一字未改；主支节点内容一字未改（新节点只是新增）
        self.assertEqual(main_msgs_before,
                         [_sig(m) for m in self.rt.get_messages(cid)])
        after_content = {n["id"]: n["content"]
                         for n in self.rt.store.nodes_of(cid)}
        for nid, content in main_content_before.items():
            self.assertEqual(content, after_content[nid])

        # 2) 新支的消息在自己的线程；主支里看不到新支的话
        branch_msgs = self.rt.get_thread_messages(branch["current_branch"])
        branch_texts = [getattr(m, "content", "") for m in branch_msgs]
        self.assertIn("这是新支里的回答。", branch_texts)
        self.assertIn("之前跑过哪些任务？", branch_texts)          # 接上祖先上下文
        self.assertNotIn("这是新支里的回答。", [t for _, t in main_msgs_before])

        # 3) 重启后仍隔离（隔离不是内存假象）
        self.rt.close()
        rt2 = Runtime(db_path=self.db, artifact_dir=os.path.join(self.tmp, "art"))
        self.addCleanup(rt2.close)
        self.assertEqual(main_msgs_before,
                         [_sig(m) for m in rt2.get_messages(cid)])
        rt2_main_texts = [getattr(m, "content", "") for m in rt2.get_messages(cid)]
        self.assertNotIn("新支里的话", rt2_main_texts)
        self.assertNotIn("这是新支里的回答。", rt2_main_texts)
        rt2_branch_texts = [getattr(m, "content", "")
                            for m in rt2.get_thread_messages(branch["current_branch"])]
        self.assertIn("这是新支里的回答。", rt2_branch_texts)


if __name__ == "__main__":
    unittest.main()
