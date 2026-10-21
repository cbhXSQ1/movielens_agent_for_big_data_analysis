# -*- coding: utf-8 -*-
"""llm_client 的传输分派：opencode 网关走 curl（自动补会话头），标准端点走 urllib。

这里只测**分派与错误分类**，不联网：把 is_opencode_gateway / stream_via_curl 换成假的。
"""

from agent import llm_client


class Cfg(object):
    usable = True
    api_key = "k" * 10
    model = "deepseek-v4.1-flash"
    timeout_sec = 5.0
    max_tokens = 16
    temperature = 0.0

    def endpoint(self):
        return "https://opencode.ai/inference/go/openai/v1/chat/completions"


def test_opencode_goes_through_curl_and_parses(monkeypatch):
    seen = {}

    def fake_curl(url, headers, body, timeout=None, curl_bin="curl"):
        seen["url"] = url
        seen["headers"] = headers or {}
        yield 'data: {"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}\n\n'

    monkeypatch.setattr(llm_client, "is_opencode_gateway", lambda url: True)
    monkeypatch.setattr(llm_client, "stream_via_curl", fake_curl)

    body, err = llm_client._post_chat(Cfg(), {"model": "x", "messages": []})
    assert err is None
    assert body["choices"][0]["message"]["content"] == "ok"
    assert seen["url"].endswith("/chat/completions")
    assert seen["headers"].get("Authorization").startswith("Bearer ")


def test_gateway_missing_session_error_is_readable(monkeypatch):
    def fake_curl(*_a, **_k):
        yield ('data: {"type":"error","error":{"type":"MissingSessionID",'
               '"message":"Request is missing x-opencode-session"}}\n\n')

    monkeypatch.setattr(llm_client, "is_opencode_gateway", lambda url: True)
    monkeypatch.setattr(llm_client, "stream_via_curl", fake_curl)

    body, err = llm_client._post_chat(Cfg(), {})
    assert body is None
    assert "会话头" in err


def test_gateway_model_error_points_to_model_id(monkeypatch):
    def fake_curl(*_a, **_k):
        yield ('data: {"type":"error","error":{"type":"ModelNotFound",'
               '"message":"model not found or no permission"}}\n\n')

    monkeypatch.setattr(llm_client, "is_opencode_gateway", lambda url: True)
    monkeypatch.setattr(llm_client, "stream_via_curl", fake_curl)

    body, err = llm_client._post_chat(Cfg(), {})
    assert body is None
    assert "Model ID" in err


def test_plain_openai_endpoint_uses_urllib(monkeypatch):
    """标准端点仍走 urllib：这里只断言分派结果（不真的联网）。"""
    monkeypatch.setattr(llm_client, "is_opencode_gateway", lambda url: False)

    def boom(*_a, **_k):
        raise AssertionError("标准端点不应走 curl")

    monkeypatch.setattr(llm_client, "stream_via_curl", boom)

    # 用一个必然失败的 urlopen，确认走到 urllib 分支（错误被吞成可读文案）。
    def fake_urlopen(req, timeout=None):
        raise llm_client.urllib.error.URLError("unreachable-for-test")

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    body, err = llm_client._post_chat(Cfg(), {})
    assert body is None
    assert "网络不可达" in err
