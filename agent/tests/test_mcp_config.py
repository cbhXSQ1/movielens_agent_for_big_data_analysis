# -*- coding: utf-8 -*-
"""远端能力服务连接配置（Task 1）：默认不可用、环境变量覆盖、口令脱敏。

红线：口令绝不进任何用户可见输出；`safe_dict()` 只暴露是否已配置。
"""
import os
import sys

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import mcp_config  # noqa: E402


def test_defaults_unusable_without_url_and_token(monkeypatch):
    for k in ("AGENT_MCP_URL", "AGENT_MCP_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    # 隔离本机配置：真实 config/mcp.settings.json 存在时不影响本用例。
    monkeypatch.setattr(mcp_config, "_read_file", lambda: {})
    cfg = mcp_config.load()
    assert cfg.usable is False
    assert cfg.interface_version == "1.0"


def test_env_overrides_and_usable(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_URL", "http://127.0.0.1:8900/mcp")
    monkeypatch.setenv("AGENT_MCP_TOKEN", "x" * 20)
    cfg = mcp_config.load()
    assert cfg.usable is True
    assert cfg.protocol_url() == "http://127.0.0.1:8900/mcp"
    assert cfg.health_url() == "http://127.0.0.1:8900/health"


def test_safe_dict_hides_token(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_URL", "http://127.0.0.1:8900/mcp")
    monkeypatch.setenv("AGENT_MCP_TOKEN", "secret-token-value")
    d = mcp_config.load().safe_dict()
    assert "secret-token-value" not in str(d)
    assert d["token_set"] is True
