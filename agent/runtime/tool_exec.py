# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· T3.3：本地工具执行后端（执行前已过策略校验）。
阶段 8（ag-mcp）· T8.1 / T8.2：执行后端抽象 + 来源路由 + MCP 占位后端。

工具表取自 `tool_specs`；执行落到 `tools.py` 的现有实现。只传 schema 声明过的
参数（未声明的一律丢弃），与本仓库旧循环的 `_execute_tool` 同口径。

**执行后端抽象（T8.1）**：上层（`graph` 的循环、`offline` 的直调）只认
`execute(name, args) -> (ok, envelope)`。本地直调（`LocalToolBackend`）与将来
的远程 MCP 调用（`McpToolBackend`）都实现同一接口，故**换后端不改上层**。

**来源路由（T8.2）**：`RoutingBackend` 按工具表「来源」字段分派 —— `mcp` 工具走
MCP 后端、`local` 工具走本机；来源是唯一选择依据（`tool_specs.source_for`）。
"""

from .. import mcp_contract, tool_specs


class ToolBackend(object):
    """执行后端的统一接口（T8.1）：`execute(name, args) -> (ok, envelope)`。

    这是上层唯一依赖的形状；本地直调、MCP 调用、假后端、长任务装饰器全部照此。
    """

    def execute(self, name, args):
        raise NotImplementedError

    #: 该后端负责的来源标签（仅用于自描述 / 调试；路由以工具表字段为准）。
    source = None


class LocalToolBackend(ToolBackend):
    """本机直调：按工具名取 tools.py 函数执行。"""

    source = tool_specs.SOURCE_LOCAL

    def execute(self, name, args):
        """返回 (ok: bool, envelope: dict)。未注册工具 / 参数不合法给结构化错误。"""
        spec = tool_specs.schema_for(name)
        if spec is None:
            return False, {"ok": False, "error": {
                "code": "USAGE", "message": "未注册工具：%s" % name}}
        fn_name = tool_specs.SPEC_TO_FUNCTION.get(name)
        fn = getattr(tool_specs.tools_module(), fn_name, None)
        if fn is None:
            return False, {"ok": False, "error": {
                "code": "USAGE", "message": "工具实现缺失：%s" % name}}
        allowed = set(spec["parameters"]["properties"])
        clean = {key: value for key, value in (args or {}).items()
                 if key in allowed and value is not None}
        try:
            envelope = fn(**clean)
        except TypeError as exc:
            return False, {"ok": False, "error": {
                "code": "USAGE", "message": "参数不合法：%s" % exc}}
        return bool(envelope and envelope.get("ok")), (envelope or {})


class McpToolBackend(ToolBackend):
    """远程 MCP 执行后端（T8.1 / T8.2）：**只留接缝，不真正连网**。

    真正的远程调用由注入的 `McpClient`（见 `agent/mcp_contract.py`）提供；未注入
    时用 `NullMcpClient` 占位 —— 任何调用都返回结构化 `MCP_UNAVAILABLE`。返回的信封
    错误码统一经 `mcp_contract.normalize_result` 归一（§5.4，语义无损）。
    """

    source = tool_specs.SOURCE_MCP

    def __init__(self, client=None):
        self._client = client if client is not None else mcp_contract.NullMcpClient()

    @property
    def client(self):
        return self._client

    def execute(self, name, args):
        try:
            envelope = self._client.call_tool(name, args)
        except Exception as exc:                       # 传输层异常 → 结构化错误
            return False, {"ok": False, "error": {
                "code": mcp_contract.CODE_DRIVER_UNREACHABLE,
                "message": "MCP 调用异常：%s" % exc}}
        ok, envelope = mcp_contract.normalize_result(envelope)
        return ok, envelope


class RoutingBackend(ToolBackend):
    """按工具表「来源」字段路由到 local / mcp 后端（T8.2）。

    来源取自 `tool_specs.source_for`（可注入 `source_resolver` 以便测试）；上层不
    感知路由，拿到的仍是同一个 `execute(name, args)` 接口。
    """

    def __init__(self, local_backend, mcp_backend, source_resolver=None):
        self._local = local_backend
        self._mcp = mcp_backend
        self._source_for = source_resolver or tool_specs.source_for

    @property
    def local_backend(self):
        return self._local

    @property
    def mcp_backend(self):
        return self._mcp

    def backend_for(self, name):
        """该工具名应走的后端（来源是唯一依据）。"""
        if self._source_for(name) == tool_specs.SOURCE_MCP:
            return self._mcp
        return self._local

    def execute(self, name, args):
        return self.backend_for(name).execute(name, args)


def build_tool_backend(mcp_client=None):
    """装配默认执行后端：本地直调 + MCP 占位，按来源路由（T8.1）。

    这是**运行时拿执行后端的唯一入口**：将来接入真实 MCP 只需传入一个 `McpClient`，
    上层（运行时 / 两条模式）无需改动。
    """
    return RoutingBackend(LocalToolBackend(), McpToolBackend(mcp_client))


class LongTaskBackend(ToolBackend):
    """执行出口的长任务装饰器（阶段 4 T4.1）。

    在**任何**工具执行后端（本地 / 假 / 将来 MCP）的出口做同一件事：工具成功且
    属长耗时时，登记一个长任务并把句柄并入信封，**当轮立即返回、不阻塞**。
    装饰器只暴露 `execute(name, args)`，与循环/tool_specs 的调用形状一致，故上层
    循环无需感知长任务；阶段 8 的 MCP 后端套上它即获得同样的挂起能力。
    """

    def __init__(self, inner, manager, conversation_id):
        self._inner = inner
        self._manager = manager
        self._conversation_id = conversation_id

    def execute(self, name, args):
        ok, envelope = self._inner.execute(name, args)
        if not ok or self._manager is None:
            return ok, envelope
        if not self._manager.is_long_running(name):
            return ok, envelope
        handle = self._manager.register(self._conversation_id, name, args, envelope)
        envelope = dict(envelope or {})
        envelope["long_task"] = True
        envelope["agent_task_id"] = handle["agent_task_id"]
        envelope["task_handle"] = handle
        return ok, envelope

    def __getattr__(self, item):
        return getattr(self._inner, item)


#: 供后端层复用的传输层错误码（来自 driver_client，经 mcp_contract 归一）。
DRIVER_TRANSPORT_CODES = tuple(mcp_contract.TRANSPORT_CODE_MAP)
