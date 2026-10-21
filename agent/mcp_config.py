# -*- coding: utf-8 -*-
"""远端 Hadoop 能力服务的连接配置（标准协议入口 /mcp + Bearer 口令）。

配置来源（后者覆盖前者）：
  1) config/mcp.settings.json（可选，不建议入库）
  2) 环境变量 AGENT_MCP_*

红线：口令不落日志；默认不可用（地址或口令缺一即视为未接入）。
"""

import json
import os

from .mcp_contract import MCP_INTERFACE_VERSION

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(os.path.dirname(_HERE), "config", "mcp.settings.json")

ENV = {
    "url": "AGENT_MCP_URL",
    "token": "AGENT_MCP_TOKEN",
    "timeout_sec": "AGENT_MCP_TIMEOUT",
    "interface_version": "AGENT_MCP_INTERFACE_VERSION",
}

DEFAULT_TIMEOUT = 15.0
MAX_TIMEOUT = 120.0


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class McpConfig(object):
    def __init__(self, raw=None):
        d = dict(raw or {})
        self.url = (d.get("url") or "").strip()
        self.token = (d.get("token") or "").strip()
        self.timeout_sec = min(_as_float(d.get("timeout_sec"), DEFAULT_TIMEOUT), MAX_TIMEOUT)
        self.interface_version = (d.get("interface_version") or MCP_INTERFACE_VERSION).strip()

    @property
    def usable(self):
        return bool(self.url and self.token)

    def _base(self):
        base = self.url
        for suffix in ("/mcp", "/"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base

    def protocol_url(self):
        return self._base() + "/mcp"

    def health_url(self):
        return self._base() + "/health"

    def safe_dict(self):
        """脱敏视图：不含口令。"""
        return {"url": self.url or None, "token_set": bool(self.token),
                "timeout_sec": self.timeout_sec,
                "interface_version": self.interface_version, "usable": self.usable}


def _read_file():
    if not os.path.isfile(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def load(overrides=None):
    raw = {}
    raw.update(_read_file())
    for key, name in ENV.items():
        val = os.environ.get(name)
        if val not in (None, ""):
            raw[key] = val
    if isinstance(overrides, dict):
        raw.update(overrides)
    return McpConfig(raw)
