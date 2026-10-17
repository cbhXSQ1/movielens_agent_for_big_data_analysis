# -*- coding: utf-8 -*-
"""阶段 1（ag-llm）单测：模型接入层 —— 流式解析、工具调用聚合、双供应商差异、
结构化错误分类、配置三级合并与脱敏。

全部用**假传输**驱动，**不调用任何真实大模型网关**。
"""
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import llm  # noqa: E402
from agent import llm_config  # noqa: E402
from agent.llm import FakeStreamTransport  # noqa: E402
from agent.llm.config import ModelConfig  # noqa: E402

MESSAGES = [{"role": "user", "content": "你好"}]


def make_config(provider="openai", session_key="", api_base="https://api.example.com/v1",
                api_key="sk-secret", model="deepseek-chat"):
    """直接构造一份可用配置（不走文件/环境，供供应商测试）。"""
    base = llm_config.LLMConfig({
        "supported": True, "enabled": True, "api_base": api_base,
        "api_key": api_key, "model": model,
    })
    return ModelConfig(base, {"provider": provider, "session_key": session_key})


def sse(obj):
    """把一个 JSON 对象包成一条 SSE data 行（含换行）。"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def http_error(status, body=b'{"error":"x"}'):
    return urllib.error.HTTPError(
        "https://api.example.com/v1/chat/completions", status, "reason", {},
        io.BytesIO(body))


def collect(provider, **kwargs):
    return list(provider.stream_chat(MESSAGES, **kwargs))


# ===========================================================================
# T1.1 流式文本：多个增量片段
# ===========================================================================

class TestStreamingText(unittest.TestCase):
    def test_emits_multiple_text_deltas(self):
        chunks = [
            sse({"choices": [{"index": 0, "delta": {"content": "Hello"}}]}),
            sse({"choices": [{"index": 0, "delta": {"content": ", "}}]}),
            sse({"choices": [{"index": 0, "delta": {"content": "world"}}]}),
            sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
            "data: [DONE]\n\n",
        ]
        transport = FakeStreamTransport(chunks)
        provider = llm.build_provider(make_config(), transport)
        events = collect(provider)

        texts = [e.text for e in events if e.kind == "text_delta"]
        self.assertEqual(["Hello", ", ", "world"], texts)
        self.assertGreaterEqual(len(texts), 3)
        self.assertEqual("done", events[-1].kind)
        self.assertEqual("stop", events[-1].finish_reason)

    def test_chunk_boundaries_do_not_break_lines(self):
        """传输片段可以切在任意位置（甚至切在一行中间），解析器必须行缓冲。"""
        blob = "".join([
            sse({"choices": [{"index": 0, "delta": {"content": "aa"}}]}),
            sse({"choices": [{"index": 0, "delta": {"content": "bb"}}]}),
            "data: [DONE]\n\n",
        ])
        pieces = [blob[i:i + 5] for i in range(0, len(blob), 5)]
        transport = FakeStreamTransport(pieces)
        provider = llm.build_provider(make_config(), transport)
        events = collect(provider)
        self.assertEqual(["aa", "bb"], [e.text for e in events if e.kind == "text_delta"])


# ===========================================================================
# T1.1 工具调用：流式分片 → 工具名 + 参数
# ===========================================================================

class TestToolCallParsing(unittest.TestCase):
    def test_fragmented_tool_call_aggregated(self):
        """arguments 被切成多段字符串，必须按 index 累积后解析成 dict。"""
        chunks = [
            sse({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "add", "arguments": ""}}]}}]}),
            sse({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"a"'}}]}}]}),
            sse({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": ': 12, "b": 30}'}}]}}]}),
            sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]\n\n",
        ]
        transport = FakeStreamTransport(chunks)
        provider = llm.build_provider(make_config(), transport)
        events = collect(provider)

        calls = [e for e in events if e.kind == "tool_call"]
        self.assertEqual(1, len(calls))
        self.assertEqual("add", calls[0].name)
        self.assertEqual({"a": 12, "b": 30}, calls[0].arguments)
        self.assertEqual("call_1", calls[0].call_id)
        self.assertEqual("tool_calls", events[-1].finish_reason)

    def test_parallel_tool_calls_by_index(self):
        parser = llm.SseStreamParser()
        events = []
        events += parser.feed(sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c0", "function": {"name": "list_tasks", "arguments": "{}"}},
            {"index": 1, "id": "c1", "function": {"name": "get_report",
                                                  "arguments": '{"task_id": "t1"}'}}]}}]}))
        events += parser.finish()
        calls = [e for e in events if e.kind == "tool_call"]
        self.assertEqual([("list_tasks", {}), ("get_report", {"task_id": "t1"})],
                         [(c.name, c.arguments) for c in calls])


# ===========================================================================
# T1.2 / T1.3 双供应商请求装配差异
# ===========================================================================

class TestProviderRequests(unittest.TestCase):
    def test_build_provider_picks_class(self):
        self.assertEqual("openai", llm.build_provider(make_config("openai")).provider_name)
        self.assertEqual("opencode",
                         llm.build_provider(make_config("opencode")).provider_name)

    def test_openai_request_shape(self):
        transport = FakeStreamTransport(["data: [DONE]\n\n"])
        provider = llm.build_provider(make_config("openai"), transport)
        tools = [{"type": "function", "function": {"name": "add"}}]
        collect(provider, tools=tools)
        req = transport.requests[0]
        self.assertEqual("https://api.example.com/v1/chat/completions", req["url"])
        self.assertEqual("Bearer sk-secret", req["headers"]["Authorization"])
        self.assertNotIn("x-agent-session", req["headers"])
        body = json.loads(req["body"].decode("utf-8"))
        self.assertTrue(body["stream"])
        self.assertEqual("deepseek-chat", body["model"])
        self.assertEqual(MESSAGES, body["messages"])
        self.assertEqual(tools, body["tools"])

    def test_opencode_adds_stable_session_header(self):
        transport = FakeStreamTransport(["data: [DONE]\n\n"])
        provider = llm.build_provider(make_config("opencode", session_key="conv-42"), transport)
        collect(provider)
        self.assertEqual("conv-42", transport.requests[0]["headers"]["x-agent-session"])

    def test_opencode_without_session_key_omits_header(self):
        transport = FakeStreamTransport(["data: [DONE]\n\n"])
        provider = llm.build_provider(make_config("opencode", session_key=""), transport)
        collect(provider)
        self.assertNotIn("x-agent-session", transport.requests[0]["headers"])


# ===========================================================================
# 思考档位透传：options.reasoning_effort → 请求体（加性，默认行为不变）
# ===========================================================================

class TestReasoningEffortRequest(unittest.TestCase):
    def _body(self, options=None):
        transport = FakeStreamTransport(["data: [DONE]\n\n"])
        provider = llm.build_provider(make_config("openai"), transport)
        collect(provider, options=options) if options is not None else collect(provider)
        return json.loads(transport.requests[0]["body"].decode("utf-8"))

    def test_legal_effort_is_sent(self):
        for value in ("low", "medium", "high"):
            body = self._body({"reasoning_effort": value})
            self.assertEqual(value, body["reasoning_effort"])

    def test_default_and_missing_omit_field(self):
        self.assertNotIn("reasoning_effort", self._body())
        self.assertNotIn("reasoning_effort", self._body({"reasoning_effort": ""}))
        self.assertNotIn("reasoning_effort", self._body({"reasoning_effort": "default"}))
        self.assertNotIn("reasoning_effort", self._body({"reasoning_effort": "bogus"}))

    def test_normalize_helper(self):
        from agent.llm.providers import normalize_reasoning_effort as norm
        self.assertEqual("high", norm("high"))
        self.assertEqual("high", norm(" HIGH "))
        self.assertIsNone(norm(""))
        self.assertIsNone(norm("default"))
        self.assertIsNone(norm("bogus"))
        self.assertIsNone(norm(None))
        self.assertIsNone(norm(3))


# ===========================================================================
# T1.4 结构化错误分类（不静默回退）
# ===========================================================================

class TestErrorClassification(unittest.TestCase):
    def _collect_error(self, transport, provider_name="openai", config=None):
        provider = llm.build_provider(config or make_config(provider_name), transport)
        with self.assertRaises(llm.ModelError) as ctx:
            collect(provider)
        return ctx.exception

    def test_not_configured(self):
        base = llm_config.LLMConfig({"enabled": False})
        cfg = ModelConfig(base, {})
        provider = llm.build_provider(cfg, FakeStreamTransport([]))
        with self.assertRaises(llm.ModelError) as ctx:
            provider.stream_chat(MESSAGES)
        self.assertEqual(llm.NOT_CONFIGURED, ctx.exception.code)

    def test_provider_unknown(self):
        cfg = make_config("bogus")
        with self.assertRaises(llm.ModelError) as ctx:
            llm.build_provider(cfg)
        self.assertEqual(llm.PROVIDER_UNKNOWN, ctx.exception.code)

    def test_auth_failed(self):
        err = self._collect_error(FakeStreamTransport(error=http_error(401)))
        self.assertEqual(llm.AUTH_FAILED, err.code)

    def test_bad_path(self):
        err = self._collect_error(FakeStreamTransport(error=http_error(404)))
        self.assertEqual(llm.BAD_PATH, err.code)

    def test_rate_limited(self):
        err = self._collect_error(FakeStreamTransport(error=http_error(429)))
        self.assertEqual(llm.RATE_LIMITED, err.code)

    def test_timeout(self):
        err = self._collect_error(FakeStreamTransport(error=socket.timeout("timed out")))
        self.assertEqual(llm.TIMEOUT, err.code)

    def test_network_unreachable(self):
        exc = urllib.error.URLError(ConnectionRefusedError(10061, "connection refused"))
        err = self._collect_error(FakeStreamTransport(error=exc))
        self.assertEqual(llm.NETWORK_UNREACHABLE, err.code)

    def test_bad_response(self):
        err = self._collect_error(FakeStreamTransport(["data: {not-json\n\n"]))
        self.assertEqual(llm.BAD_RESPONSE, err.code)

    def test_model_error_in_stream(self):
        err = self._collect_error(FakeStreamTransport(
            [sse({"error": {"message": "model not found"}})]))
        self.assertEqual(llm.MODEL_ERROR, err.code)

    def test_error_envelope_shape(self):
        err = self._collect_error(FakeStreamTransport(error=http_error(401, b"bad key")))
        env = err.to_envelope()
        self.assertFalse(env["ok"])
        self.assertEqual(llm.AUTH_FAILED, env["error"]["code"])
        self.assertNotIn("sk-secret", json.dumps(env, ensure_ascii=False))


# ===========================================================================
# T1.5 配置三级合并与脱敏
# ===========================================================================

class TestConfigMerge(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self._orig_path = llm_config.CONFIG_PATH
        self._env_backup = {}
        for name in ("AGENT_LLM_API_BASE", "AGENT_LLM_API_KEY", "AGENT_LLM_MODEL",
                     "AGENT_LLM_PROVIDER", "AGENT_LLM_SESSION_KEY"):
            self._env_backup[name] = os.environ.get(name)
            os.environ.pop(name, None)

        def restore():
            llm_config.CONFIG_PATH = self._orig_path
            for name, value in self._env_backup.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)
        llm_config.CONFIG_PATH = self.path

    def _write_file(self, data):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_request_over_env_over_file(self):
        self._write_file({
            "supported": True, "enabled": True,
            "api_base": "https://file/v1", "api_key": "file-key", "model": "file-model",
            "provider": "opencode", "session_key": "file-sess",
        })
        os.environ["AGENT_LLM_API_BASE"] = "https://env/v1"      # env > file
        os.environ["AGENT_LLM_PROVIDER"] = "openai"              # env > file
        cfg = llm.load({"model": "req-model",                     # request > file
                        "provider": "opencode",                   # request > env
                        "session_key": "req-sess"})

        self.assertTrue(cfg.usable)
        self.assertEqual("https://env/v1", cfg.base.api_base)
        self.assertEqual("file-key", cfg.base.api_key)
        self.assertEqual("req-model", cfg.base.model)
        self.assertEqual("opencode", cfg.provider)
        self.assertEqual("req-sess", cfg.session_key)

    def test_file_only(self):
        self._write_file({
            "supported": True, "enabled": True,
            "api_base": "https://file/v1", "api_key": "file-key", "model": "file-model",
            "provider": "opencode", "session_key": "s",
        })
        cfg = llm.load()
        self.assertEqual("opencode", cfg.provider)
        self.assertEqual("file-model", cfg.base.model)

    def test_describe_hides_secrets(self):
        self._write_file({
            "supported": True, "enabled": True,
            "api_base": "https://file/v1", "api_key": "sk-super-secret",
            "model": "m", "provider": "opencode", "session_key": "sess-xyz",
        })
        cfg = llm.load()
        view = cfg.describe()
        blob = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("api_key", view)
        self.assertNotIn("sk-super-secret", blob)
        self.assertNotIn("sess-xyz", blob)
        self.assertTrue(view["session_key_set"])
        self.assertEqual("opencode", view["provider"])


if __name__ == "__main__":
    unittest.main()
