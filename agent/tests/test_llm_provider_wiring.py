# -*- coding: utf-8 -*-
"""请求覆盖（body.llm）装配 provider 必须用 ModelConfig，而不是旧 LLMConfig。

回归：此前 `_provider_from_body` 用 llm_config.load() 得到 LLMConfig，
provider 读 `config.base` 直接 AttributeError（`'LLMConfig' object has no attribute 'base'`）。
"""

import os
import sys

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api, llm, llm_config  # noqa: E402


def _body(model="deepseek-v4.1-flash"):
    return {"llm": {
        "enabled": True,
        "api_base": "https://opencode.ai/inference/go/openai/v1",
        "api_key": "k" * 24,
        "model": model,
        "mode": "llm",
    }}


def test_provider_from_body_uses_model_config(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_SUPPORTED", "1")
    monkeypatch.setattr(llm_config, "_read_file", lambda: {})
    monkeypatch.setattr(llm.config, "_read_file", lambda: {})

    provider, err = http_api._provider_from_body(_body())
    assert err is None
    assert provider is not None
    # 关键：provider 拿到的必须是带 .base 的 ModelConfig
    assert provider.config.base.model == "deepseek-v4.1-flash"
    assert provider.config.base.api_base.endswith("/v1")


def test_provider_from_body_missing_llm_returns_none():
    provider, err = http_api._provider_from_body({})
    assert provider is None
    assert err is None
