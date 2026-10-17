# -*- coding: utf-8 -*-
"""真实 MCP 客户端：按标准协议连远端能力服务的 /mcp 入口。

连接方式与 `hadoop/tools/mcp_client.py` 一致：带鉴权头的 HTTP 客户端 + 传输层 + 会话，
每次动作开一次短会话，动作完成即关。任何连接/协议异常都翻成可读说明，不把堆栈甩给上层。

红线：口令只放进请求头 `Authorization: Bearer <口令>`，**绝不**进日志或信封。
不可用（无地址或无口令）时所有调用返回结构化 `MCP_UNAVAILABLE`，不发起任何网络请求。
"""

import asyncio
import json

from . import mcp_contract
from .mcp_config import McpConfig

#: 远端工具在工具表里的来源标记（契约层约定，全局唯一）。
SOURCE_MCP = "mcp"


def _flatten(exc):
    """把异常组摊平：连接失败常被包成异常组，真正的原因藏在里层。"""
    out = []
    stack = [exc]
    seen = set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        out.append(item)
        for sub in getattr(item, "exceptions", ()) or ():
            stack.append(sub)
    return out


def explain(exc, url):
    """把底层异常翻成能照着做的话（区分：口令不对 / 连不上 / 其它）。"""
    flat = _flatten(exc)
    text = "；".join("%s: %s" % (type(x).__name__, x) for x in flat[:3]) or str(exc)
    low = " ".join(str(x).lower() for x in flat) or str(exc).lower()
    if "401" in low or "unauthorized" in low:
        return ("连接凭据不对：确认口令与请求头 Authorization: Bearer <口令>。"
                "原始错误：%s" % text)
    if "refused" in low or "connect" in low or "unreachable" in low:
        return ("连不上 %s：服务可能没启动，或宿主机侧没配端口转发（虚拟机侧固定 8765）。"
                "原始错误：%s" % (url, text))
    return "调用失败：%s" % text


class McpHttpClient(mcp_contract.McpClient):
    """标准协议 MCP 客户端；`session_factory` 仅供测试注入（不连网）。"""

    def __init__(self, config=None, session_factory=None):
        self._config = config or McpConfig({})
        self._session_factory = session_factory
        self.interface_version = self._config.interface_version

    @property
    def config(self):
        return self._config

    # -- 内部 -----------------------------------------------------------------

    def _envelope_error(self, code, message):
        return {"ok": False, "interface_version": self.interface_version,
                "error": {"code": code, "message": message}}

    def _unavailable(self, action):
        return self._envelope_error(
            mcp_contract.CODE_MCP_UNAVAILABLE,
            "远端能力服务未接入或未配置：%s" % action)

    def _run(self, worker):
        """开一次会话跑 `await worker(session)`；异常翻成 `{"_error": <可读说明>}`。

        `worker` 是一个 `async def`，接收已握手的会话。
        """
        if self._session_factory is not None:
            async def _fake():
                session = self._session_factory()
                await session.initialize()
                return await worker(session)

            try:
                return asyncio.run(_fake())
            except Exception as exc:                    # noqa: BLE001 —— 这里就是要报清楚
                return {"_error": explain(exc, self._config.protocol_url())}

        from mcp import ClientSession
        from mcp.client.streamable_http import (
            create_mcp_http_client, streamable_http_client)

        headers = {"Authorization": "Bearer %s" % self._config.token}

        async def _real():
            async with create_mcp_http_client(headers=headers) as http_client:
                async with streamable_http_client(
                        self._config.protocol_url(), http_client=http_client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        return await worker(session)

        try:
            return asyncio.run(_real())
        except Exception as exc:                        # noqa: BLE001 —— 这里就是要报清楚
            return {"_error": explain(exc, self._config.protocol_url())}

    @staticmethod
    def _tool_row(tool):
        schema = getattr(tool, "input_schema", None) or {}
        return {"name": tool.name,
                "title": getattr(tool, "title", None) or tool.name,
                "description": getattr(tool, "description", "") or "",
                "source": SOURCE_MCP,
                "parameters": schema}

    # -- 契约方法 -------------------------------------------------------------

    def list_tools(self):
        if not self._config.usable:
            return []

        async def worker(session):
            result = await session.list_tools()
            return [self._tool_row(tool)
                    for tool in list(getattr(result, "tools", []) or [])]

        rows = self._run(worker)
        return rows if isinstance(rows, list) else []

    def call_tool(self, name, args):
        if not self._config.usable:
            return self._unavailable("call_tool(%s)" % name)

        async def worker(session):
            result = await session.call_tool(name, args or {})
            texts = []
            for item in getattr(result, "content", []) or []:
                text = getattr(item, "text", None)
                if text is not None:
                    texts.append(text)
            return "\n".join(texts)

        text = self._run(worker)
        if isinstance(text, dict) and text.get("_error"):
            return self._envelope_error(mcp_contract.CODE_DRIVER_UNREACHABLE,
                                        text["_error"])
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return self._envelope_error(
                mcp_contract.CODE_DRIVER_BAD_OUTPUT,
                "远端返回非信封：%s" % str(text)[:300])
        if not isinstance(payload, dict):
            return self._envelope_error(mcp_contract.CODE_DRIVER_BAD_OUTPUT,
                                        "远端返回非对象信封")
        return payload

    def get_task_status(self, task_id):
        return self.call_tool("get_task_status", {"task_id": task_id})

    def describe_capabilities(self):
        return self.call_tool("describe_capabilities", {})

    # -- 启动时一致性校验 -----------------------------------------------------

    @staticmethod
    def _declined(reason):
        """校验没跑成（未接入 / 调用失败）时的**非抛**结果，带可读原因。"""
        return {"ok": False, "missing": [], "extra": [],
                "version_ok": False, "peer_version": None, "reason": reason}

    @staticmethod
    def _tool_names_in(envelope):
        """从能力自描述信封里取工具名集合（`tools` 为条目 dict 或裸名串）。"""
        names = set()
        for item in envelope.get("tools") or []:
            name = item.get("name") if isinstance(item, dict) else item
            if isinstance(name, str) and name:
                names.add(name)
        return names

    def verify_tools(self, declared):
        """用能力自描述校验本端工具表与远端是否一致（**绝不抛异常**）。

        返回 `{"ok", "missing", "extra", "version_ok", "peer_version"}`：
          * `missing` —— 本端声明而远端没有的工具名（排序）；
          * `extra`   —— 远端有而本端未声明的工具名（排序）；
          * `ok`      —— 名字集合完全一致且接口版本同主版本时为 True。

        客户端不可用 / 调用失败时返回 `ok=False` 且带 `reason`，不改写、不静默。
        """
        declared = set(declared or ())
        if not self._config.usable:
            return self._declined("远端能力服务未接入或未配置：verify_tools")
        envelope = self.describe_capabilities()
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            error = (envelope or {}).get("error") or {}
            return self._declined(error.get("message") or "能力自描述调用失败")
        peer_version = envelope.get("interface_version")
        server_names = self._tool_names_in(envelope)
        missing = sorted(declared - server_names)
        extra = sorted(server_names - declared)
        version_ok = bool(mcp_contract.negotiate(
            peer_version, self.interface_version).get("compatible"))
        return {"ok": not missing and not extra and version_ok,
                "missing": missing, "extra": extra,
                "version_ok": version_ok, "peer_version": peer_version}

    def health(self):
        """健康检查：不进协议入口，直接 GET /health（免口令）。"""
        if not self._config.usable:
            return self._unavailable("health")
        import httpx
        try:
            resp = httpx.get(self._config.health_url(),
                             timeout=self._config.timeout_sec)
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True, "raw": data}
        except Exception as exc:                        # noqa: BLE001 —— 这里就是要报清楚
            return self._envelope_error(
                mcp_contract.CODE_DRIVER_UNREACHABLE,
                explain(exc, self._config.health_url()))
