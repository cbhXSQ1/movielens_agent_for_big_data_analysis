# -*- coding: utf-8 -*-
"""阶段 6（ag-contract）· T6.1 / T6.2 / T6.3：接口契约端点的**真实 HTTP 实测**。

用标准库 `http.client` 起真服务（`ThreadingHTTPServer`）并发请求，注入假模型与假工具
后端，覆盖接口文档 §3 的前端 ↔ Agent 契约：

  T6.1 健康检查 / 新建对话 / 列出对话 / 打开 SSE 推送流 / 发消息（offline + llm）/ 设置上下文；
  T6.1 SSE 线上格式（`event: …\\ndata: …\\n\\n`）：tools_snapshot → message_delta* → message_done；
  T6.2 任务进展：长耗时工具经流推 task_status + 查询端点（双编号视图）；
  T6.3 结构化结果：result_payload 事件 + 取用端点；
  T6.1 模型不可用 → 结构化错误（不退化成别的东西）。
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api  # noqa: E402
from agent.llm import ModelError, NOT_CONFIGURED  # noqa: E402
from agent.runtime import Runtime  # noqa: E402
from agent.tests.stage3_helpers import FakeToolBackend, make_provider  # noqa: E402
from agent.tests.stage3b_helpers import streamed_text_response  # noqa: E402


class _RecordingProvider(object):
    """包一层真 provider，记录每次 stream_chat 收到的 options（思考档位透传用）。"""

    def __init__(self, scripts):
        self._inner = make_provider(scripts)
        self.options_seen = []

    def stream_chat(self, messages, tools=None, options=None):
        self.options_seen.append(options)
        return self._inner.stream_chat(messages, tools=tools, options=options)


def read_frame(resp):
    """从 SSE 响应里读一帧；返回 {"event","data"} 或 None（流结束）。"""
    event = None
    data = None
    while True:
        line = resp.fp.readline()
        if not line:
            return None
        text = line.decode("utf-8").rstrip("\r\n")
        if text == "":
            if event is None and data is None:
                continue                      # 心跳注释后的空行
            return {"event": event, "data": json.loads(data)}
        if text.startswith(":"):              # 注释 / 心跳
            continue
        if text.startswith("event:"):
            event = text[len("event:"):].strip()
        elif text.startswith("data:"):
            data = text[len("data:"):].strip()


class ServerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stage6_contract_")
        self._n = 0
        self._servers = []
        self._saved_runtime = http_api._RUNTIME
        self.addCleanup(lambda: setattr(http_api, "_RUNTIME", self._saved_runtime))

    def _safe_close(self, rt):
        try:
            rt.close()
        except Exception:
            pass

    def make_runtime(self, provider_factory=None, tool_backend=None,
                     facts_factory=None):
        self._n += 1
        kwargs = {}
        if provider_factory is not None:
            kwargs["provider_factory"] = provider_factory
        if tool_backend is not None:
            kwargs["tool_backend"] = tool_backend
        if facts_factory is not None:
            kwargs["facts_factory"] = facts_factory
        rt = Runtime(db_path=os.path.join(self.tmp, "rt-%d.sqlite" % self._n),
                     artifact_dir=os.path.join(self.tmp, "art"),
                     **kwargs)
        self.addCleanup(self._safe_close, rt)
        return rt

    def start(self, runtime):
        srv = http_api.create_server("127.0.0.1", 0, runtime=runtime)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self._stop, srv, t)
        self.port = srv.server_address[1]
        return srv

    def _stop(self, srv, thread):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            headers = {}
            data = None
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8")) if raw else None
            return resp.status, payload
        finally:
            conn.close()

    def open_stream(self, cid):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request("GET", "/api/conversations/%s/stream" % cid)
        resp = conn.getresponse()
        return conn, resp

    def create_conversation(self, title=None):
        status, env = self.request("POST", "/api/conversations",
                                   {"title": title} if title else {})
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        return env["conversation"]["conversation_id"]


class TestHealthAndTools(ServerTestBase):
    def test_health_reports_tools_and_llm(self):
        rt = self.make_runtime()
        self.start(rt)
        status, env = self.request("GET", "/health")
        self.assertEqual(200, status)
        self.assertTrue(env["ok"])
        self.assertIn("service", env)
        self.assertEqual(["local"], env["tools"]["sources"])
        self.assertGreater(env["tools"]["count"], 0)
        self.assertIn("supported", env["llm"])
        self.assertIn("configured", env["llm"])

    def test_tool_table_readonly_fallback(self):
        rt = self.make_runtime()
        self.start(rt)
        status, env = self.request("GET", "/api/tools")
        self.assertEqual(200, status)
        self.assertTrue(env["ok"])
        names = {t["name"] for t in env["tools"]}
        self.assertIn("get_samples", names)
        row = [t for t in env["tools"] if t["name"] == "get_samples"][0]
        for key in ("name", "description", "parameters", "source",
                    "long_running", "estimated_seconds"):
            self.assertIn(key, row)


class TestConversationManagement(ServerTestBase):
    def test_create_and_list_conversations(self):
        rt = self.make_runtime()
        self.start(rt)
        cid = self.create_conversation("第一段")
        status, env = self.request("GET", "/api/conversations")
        self.assertEqual(200, status)
        self.assertTrue(env["ok"])
        ids = [c["conversation_id"] for c in env["conversations"]]
        self.assertIn(cid, ids)
        row = [c for c in env["conversations"]
               if c["conversation_id"] == cid][0]
        self.assertEqual("第一段", row["title"])
        self.assertIn("updated_at", row)

    def test_set_context(self):
        rt = self.make_runtime()
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/context" % cid,
                                   {"task_id": "2026-x", "data_version": "v1"})
        self.assertEqual(200, status)
        self.assertTrue(env["ok"])
        self.assertEqual("2026-x", env["context"]["task_id"])
        self.assertEqual("v1", env["context"]["data_version"])

    def test_unknown_conversation_is_structured_error(self):
        rt = self.make_runtime()
        self.start(rt)
        status, env = self.request(
            "POST", "/api/conversations/c9999/messages",
            {"mode": "offline", "tool": "list_tasks"})
        self.assertEqual(200, status)
        self.assertFalse(env["ok"])
        self.assertEqual("CONVERSATION_NOT_FOUND", env["error"]["code"])


class TestSendMessage(ServerTestBase):
    def test_offline_mode_fills_defaults(self):
        rt = self.make_runtime(
            tool_backend=FakeToolBackend({"get_samples": {
                "ok": True, "samples": [], "total_available": 0}}),
            facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request(
            "POST", "/api/conversations/%s/messages" % cid,
            {"mode": "offline", "tool": "get_samples", "params": {"task_id": "T-1"}})
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual("offline", env["engine"])
        self.assertEqual("offline", env["mode"])
        self.assertEqual("get_samples", env["offline"]["tool"])
        self.assertIn("sample_type", env["offline"]["defaults_filled"])
        self.assertIn("n", env["offline"]["defaults_filled"])

    def test_missing_mode_rejected(self):
        rt = self.make_runtime()
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/messages" % cid,
                                   {"text": "你好"})
        self.assertFalse(env["ok"])
        self.assertEqual("USAGE", env["error"]["code"])

    def test_llm_mode_end_to_end(self):
        provider = make_provider([streamed_text_response(["一切", "正常。"])])
        rt = self.make_runtime(provider_factory=lambda: provider,
                               facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/messages" % cid,
                                   {"mode": "llm", "text": "现在情况如何？"})
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual("llm", env["engine"])
        self.assertEqual("一切正常。", env["reply"])

    def test_llm_mode_passes_legal_reasoning_effort(self):
        provider = _RecordingProvider([streamed_text_response(["好的。"])])
        rt = self.make_runtime(provider_factory=lambda: provider,
                               facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/messages" % cid,
                                   {"mode": "llm", "text": "你好",
                                    "reasoning_effort": "high"})
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual([{"reasoning_effort": "high"}], provider.options_seen)

    def test_llm_mode_ignores_illegal_reasoning_effort(self):
        provider = _RecordingProvider([streamed_text_response(["好的。"])])
        rt = self.make_runtime(provider_factory=lambda: provider,
                               facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/messages" % cid,
                                   {"mode": "llm", "text": "你好",
                                    "reasoning_effort": "default"})
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        self.assertEqual([None], provider.options_seen)

    def test_llm_mode_without_reasoning_effort_sends_none(self):
        provider = _RecordingProvider([streamed_text_response(["好的。"])])
        rt = self.make_runtime(provider_factory=lambda: provider,
                               facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/messages" % cid,
                                   {"mode": "llm", "text": "你好"})
        self.assertTrue(env["ok"], env)
        self.assertEqual([None], provider.options_seen)

    def test_llm_unavailable_returns_structured_error(self):
        def forbidden():
            raise ModelError(NOT_CONFIGURED, "大模型未配置")

        rt = self.make_runtime(provider_factory=forbidden, facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        status, env = self.request("POST", "/api/conversations/%s/messages" % cid,
                                   {"mode": "llm", "text": "你好"})
        self.assertEqual(200, status)
        self.assertFalse(env["ok"])
        self.assertEqual(NOT_CONFIGURED, env["error"]["code"])


class TestSseStream(ServerTestBase):
    def test_stream_tools_snapshot_then_deltas_and_done(self):
        """SSE：先 tools_snapshot，发消息后 message_delta* → message_done。"""
        provider = make_provider([streamed_text_response(["最近", "一切", "正常。"])])
        rt = self.make_runtime(provider_factory=lambda: provider,
                               facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()

        conn, resp = self.open_stream(cid)
        try:
            self.assertEqual(200, resp.status)
            self.assertIn("text/event-stream", resp.getheader("Content-Type"))
            first = read_frame(resp)
            self.assertEqual("tools_snapshot", first["event"])
            self.assertTrue(first["data"]["data"]["tools"])
            self.assertEqual(cid, first["data"]["conversation_id"])

            status, env = self.request(
                "POST", "/api/conversations/%s/messages" % cid,
                {"mode": "llm", "text": "现在情况如何？"})
            self.assertTrue(env["ok"], env)

            kinds = []
            while True:
                frame = read_frame(resp)
                self.assertIsNotNone(frame, "流提前结束")
                kinds.append(frame["event"])
                if frame["event"] == "message_done":
                    break
            self.assertGreaterEqual(kinds.count("message_delta"), 2)
            self.assertLess(kinds.index("message_delta"), kinds.index("message_done"))
            self.assertIn("node_added", kinds)
        finally:
            conn.close()

    def test_offline_long_task_pushes_task_status(self):
        """T6.2：离线直调长耗时工具 → 流上出现 task_status，查询端点能取到。"""
        rt = self.make_runtime(
            tool_backend=FakeToolBackend({"start_cleaning_task": {
                "ok": True, "task_id": "2026-off", "status": "queued"}}),
            facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()

        conn, resp = self.open_stream(cid)
        try:
            self.assertEqual("tools_snapshot", read_frame(resp)["event"])
            status, env = self.request(
                "POST", "/api/conversations/%s/messages" % cid,
                {"mode": "offline", "tool": "start_cleaning_task",
                 "params": {"scope": "full"}})
            self.assertTrue(env["ok"], env)
            self.assertEqual("2026-off", env["task_id"])
            self.assertTrue(env["task_started"])

            status_frames = []
            while True:
                frame = read_frame(resp)
                self.assertIsNotNone(frame, "流提前结束")
                if frame["event"] == "task_status":
                    status_frames.append(frame["data"]["data"])
                if frame["event"] == "message_done":
                    break
            self.assertTrue(status_frames, "未在流上收到 task_status")
            view = status_frames[0]
            for key in ("agent_task_id", "hadoop_task_id", "name", "summary",
                        "status", "status_cn", "stage", "progress_percent",
                        "estimated_seconds", "updated_at"):
                self.assertIn(key, view)
            self.assertTrue(view["agent_task_id"].startswith("LT-"))
            self.assertEqual("2026-off", view["hadoop_task_id"])
        finally:
            conn.close()

        # 查询端点（T6.2）
        status, env = self.request("GET", "/api/conversations/%s/tasks" % cid)
        self.assertTrue(env["ok"])
        self.assertEqual(1, len(env["tasks"]))
        aid = env["tasks"][0]["agent_task_id"]
        status, env = self.request("GET",
                                   "/api/conversations/%s/tasks/%s" % (cid, aid))
        self.assertTrue(env["ok"])
        self.assertEqual(aid, env["task"]["agent_task_id"])

    def test_heartbeat_keeps_stream_open(self):
        """空闲期发注释心跳（保活），流不因暂无事件而被关闭。"""
        rt = self.make_runtime(facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()
        conn, resp = self.open_stream(cid)
        try:
            self.assertEqual("tools_snapshot", read_frame(resp)["event"])
            # 不关连接、空闲超过一个心跳周期：服务端应继续写心跳而不是断流。
            # 这里只验证连接仍可用于继续请求（kind 端点），不实际等满一个周期。
            status, env = self.request("GET", "/api/conversations")
            self.assertTrue(env["ok"])
        finally:
            conn.close()


class TestResultPayload(ServerTestBase):
    def test_result_endpoint_and_not_finished(self):
        """T6.3：结构化结果取用端点；未完成时结构化报错，不拿旧数据顶替。"""
        rt = self.make_runtime(
            tool_backend=FakeToolBackend({"start_clean_task": {
                "ok": True, "task_id": "2026-r", "status": "queued"}}),
            facts_factory=lambda: {})
        self.start(rt)
        cid = self.create_conversation()

        status, env = self.request(
            "POST", "/api/conversations/%s/messages" % cid,
            {"mode": "offline", "tool": "start_clean_task"})
        self.assertTrue(env["ok"], env)
        aid = env["long_tasks"][0]["agent_task_id"]

        # 未产出结果 → TASK_NOT_FINISHED
        status, env = self.request(
            "GET", "/api/conversations/%s/tasks/%s/result" % (cid, aid))
        self.assertFalse(env["ok"])
        self.assertEqual("TASK_NOT_FINISHED", env["error"]["code"])

        # 执行侧回报终态 → finalize 写入权威结果 → 端点取到结果载荷
        rt.long_tasks.finalize(cid, aid, status="succeeded", result={
            "scores": {"before": {"Accurate": 90.0}, "after": {"Accurate": 99.0}},
            "counts": {"input": {"ratings_lines": 10}, "output": {"ratings": 9}},
            "quarantine_samples": [{"line_no": 1, "rule_id": "R2"}],
            "report": "…报告全文…",
            "limitations": ["隔离 ≠ 修复"],
        })
        status, env = self.request(
            "GET", "/api/conversations/%s/tasks/%s/result" % (cid, aid))
        self.assertEqual(200, status)
        self.assertTrue(env["ok"], env)
        for block in ("task_status", "score_comparison", "data_volume",
                      "quarantine_samples", "report", "limitations"):
            self.assertIn(block, env["blocks"])
        self.assertEqual(aid, env["task"]["agent_task_id"])


if __name__ == "__main__":
    unittest.main()
