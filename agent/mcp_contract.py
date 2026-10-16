# -*- coding: utf-8 -*-
"""阶段 8（ag-mcp）· T8.2 / T8.3：Agent ↔ Hadoop 的 MCP 契约（接缝，不实现 Hadoop 侧）。

本模块是**纯契约层**：只放常量、映射函数与接口骨架，不含任何执行逻辑、不连网、
不引入超已装依赖。执行后端（`runtime/tool_exec.py`）按需复用它。

对齐 `docs/agent/Agent_接口文档.md` 第 5 节：

  * §5.4 错误码映射 —— 把 driver 退出码 / 传输错误映射为**结构化错误码**，
    语义无损（`EXIT_CODE_MAP` / `TRANSPORT_CODE_MAP` / `normalize_error`）；
  * §5.5 长任务与完成通知 —— `McpClient.subscribe_task_done` 是「完成通知」接缝，
    若 Hadoop 侧不提供主动通知，Agent 侧仍可退化为定期查询（本模块只留形状）；
  * §5.6 版本协商 —— `MCP_INTERFACE_VERSION` + `negotiate`：契约只做加性变更。

T8.3：`McpClient` 是 Hadoop 侧实现时必须满足的接口契约（工具发现 / 调用 / 错误 /
长任务完成通知 / 版本协商）；`NullMcpClient` 是占位实现（未接入时不连网、如实报错）。
"""

from . import driver_client as dc

# ---------------------------------------------------------------------------
# 接口版本（§5.6：MCP 契约自带版本号，只做加性变更）
# ---------------------------------------------------------------------------

#: MCP 契约自身的接口版本（与 driver 的 `interface_version` 相互独立）。
MCP_INTERFACE_VERSION = "1.0"

# ---------------------------------------------------------------------------
# 结构化错误码（接口文档 §5.4）—— 唯一来源，供后端层复用
# ---------------------------------------------------------------------------

#: 参数非法
CODE_USAGE = "USAGE"
#: 任务失败
CODE_TASK_FAILED = "TASK_FAILED"
#: 任务不存在
CODE_TASK_NOT_FOUND = "TASK_NOT_FOUND"
#: 任务未完成
CODE_TASK_NOT_FINISHED = "TASK_NOT_FINISHED"
#: 版本/发布冲突
CODE_VERSION_CONFLICT = "VERSION_CONFLICT"
#: 前置依赖缺失（§5.4 新增）
CODE_DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
#: 已有任务在跑（§5.4 新增）
CODE_TASK_ALREADY_RUNNING = "TASK_ALREADY_RUNNING"

#: 传输层错误码（Agent 侧，与 driver_client 同源，避免常量漂移）
CODE_DRIVER_NO_OUTPUT = dc.CODE_NO_OUTPUT
CODE_DRIVER_BAD_OUTPUT = dc.CODE_BAD_OUTPUT
CODE_DRIVER_UNREACHABLE = dc.CODE_UNREACHABLE
CODE_DRIVER_TIMEOUT = dc.CODE_TIMEOUT

#: MCP 客户端不可用（未接入 / 未配置）—— §5.4 传输层的 MCP 侧类比，加性扩展。
CODE_MCP_UNAVAILABLE = "MCP_UNAVAILABLE"

#: 结构化错误码全集（§5.4 表 + 两项新增 + 传输层）。
STRUCTURED_ERROR_CODES = (
    CODE_USAGE,
    CODE_TASK_FAILED,
    CODE_TASK_NOT_FOUND,
    CODE_TASK_NOT_FINISHED,
    CODE_VERSION_CONFLICT,
    CODE_DEPENDENCY_MISSING,
    CODE_TASK_ALREADY_RUNNING,
    CODE_DRIVER_NO_OUTPUT,
    CODE_DRIVER_BAD_OUTPUT,
    CODE_DRIVER_UNREACHABLE,
    CODE_DRIVER_TIMEOUT,
    CODE_MCP_UNAVAILABLE,
)

#: driver 自产的结构化码（不在 §5.4 表内，但语义比退出码更细，映射时**原样保留**）。
DRIVER_NATIVE_CODES = ("CONFIG_INVALID",)

# ---------------------------------------------------------------------------
# 映射表与映射函数（§5.4）—— 集中一处，供后端层复用
# ---------------------------------------------------------------------------

#: driver 退出码 → 结构化错误码（§5.4 表；0=成功 → None）。
EXIT_CODE_MAP = {
    dc.EXIT_OK: None,
    dc.EXIT_USAGE: CODE_USAGE,
    dc.EXIT_FAILED: CODE_TASK_FAILED,
    dc.EXIT_NOT_FOUND: CODE_TASK_NOT_FOUND,
    dc.EXIT_NOT_FINISHED: CODE_TASK_NOT_FINISHED,
    dc.EXIT_CONFLICT: CODE_VERSION_CONFLICT,
}

#: 传输层错误码 → 结构化错误码（恒等；集中登记便于后端层统一复用）。
TRANSPORT_CODE_MAP = {
    dc.CODE_NO_OUTPUT: CODE_DRIVER_NO_OUTPUT,
    dc.CODE_BAD_OUTPUT: CODE_DRIVER_BAD_OUTPUT,
    dc.CODE_UNREACHABLE: CODE_DRIVER_UNREACHABLE,
    dc.CODE_TIMEOUT: CODE_DRIVER_TIMEOUT,
}


def code_for_exit(exit_code):
    """退出码 → 结构化错误码；0 或未知返回 None（调用方保留原码，不静默丢语义）。"""
    if exit_code is None:
        return None
    return EXIT_CODE_MAP.get(exit_code)


def code_for_transport(transport_code):
    """传输层错误码 → 结构化错误码（未知原样透传）。"""
    return TRANSPORT_CODE_MAP.get(transport_code, transport_code)


def normalize_error(err, exit_code=None):
    """把一个错误 dict 归一为结构化错误码；**语义无损**。

    规则（优先级从高到低）：
      1. 已是传输层码 → 归一（恒等，集中登记）；
      2. 已是结构化码 / driver 自产码 → **原样保留**（绝不降级成通用码）；
      3. 仅有退出码可依 → 按 §5.4 表映射，并把原始码记到 `driver_code`（不丢）；
      4. 都没有 → 保留原码；实在无码才回落到 DRIVER_BAD_OUTPUT。
    `message` 等既有字段一律保留。
    """
    if not isinstance(err, dict):
        return {"code": CODE_DRIVER_BAD_OUTPUT,
                "message": "非结构化错误：%r" % (err,)}
    out = dict(err)
    code = out.get("code")
    if code in TRANSPORT_CODE_MAP:
        return out
    if code in STRUCTURED_ERROR_CODES or code in DRIVER_NATIVE_CODES:
        return out
    mapped = code_for_exit(exit_code)
    if mapped:
        out["code"] = mapped
        if code and code != mapped:
            out.setdefault("driver_code", code)
        return out
    if code:
        return out
    out["code"] = CODE_DRIVER_BAD_OUTPUT
    return out


def normalize_result(envelope):
    """把后端返回的信封归一：成功照旧；失败确保是**结构化错误码**。

    返回 `(ok: bool, envelope: dict)`，与执行后端 `execute` 的出口形状一致。
    """
    if not isinstance(envelope, dict):
        return False, {"ok": False, "error": {
            "code": CODE_DRIVER_BAD_OUTPUT,
            "message": "后端返回非信封：%r" % (envelope,)}}
    if envelope.get("ok"):
        return True, envelope
    out = dict(envelope)
    out["ok"] = False
    out["error"] = normalize_error(envelope.get("error"),
                                   exit_code=envelope.get("_exit_code"))
    return False, out


# ---------------------------------------------------------------------------
# 版本协商（§5.6）—— 只做加性变更
# ---------------------------------------------------------------------------

def parse_version(text):
    """把 "1.2.0" 这类版本串解析为整数元组；非法片段按 0。"""
    out = []
    for part in str(text or "").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def negotiate(peer_version, local_version=MCP_INTERFACE_VERSION):
    """版本协商（§5.6）：同主版本视为加性兼容；返回结构化结果，不抛异常。"""
    lp = parse_version(local_version)
    pp = parse_version(peer_version)
    compatible = bool(lp) and bool(pp) and lp[0] == pp[0]
    return {"ok": True, "compatible": compatible,
            "local_version": local_version, "peer_version": peer_version,
            "additive_only": True}


# ---------------------------------------------------------------------------
# MCP 客户端接口契约（T8.3）—— 只留形状与占位，不实现 Hadoop 侧
# ---------------------------------------------------------------------------

class McpClient(object):
    """MCP 客户端接口（Hadoop 侧实现时必须满足；本阶段只留形状）。

    契约方法：
      * 工具发现   `list_tools()` → §3.7 形态的工具表条目（source 一律为 `mcp`）；
      * 调用       `call_tool(name, args)` → 统一信封（成功 `{"ok": true, ...}`，
                   失败 `{"ok": false, "error": {"code": <结构化错误码>, ...}}`）；
      * 错误       `error.code` 取 §5.4 的结构化码；传输层不可达用 `DRIVER_*`；
      * 查状态     `get_task_status(task_id)` → 状态信封（长任务可查）；
      * 完成通知   `subscribe_task_done(callback, task_id=None)` → 通知接缝
                   （§5.5；Hadoop 侧不提供时返回 None，Agent 侧退化为定期查询）；
      * 版本协商   `negotiate_version(peer_version=None)` → `negotiate` 结果。

    约束：契约**只做加性变更**；每条工具与信封都带 `interface_version`。
    """

    #: 本实现遵循的 MCP 契约版本。
    interface_version = MCP_INTERFACE_VERSION

    def list_tools(self):
        raise NotImplementedError

    def call_tool(self, name, args):
        raise NotImplementedError

    def get_task_status(self, task_id):
        raise NotImplementedError

    def subscribe_task_done(self, callback, task_id=None):
        """登记完成通知回调；返回可传给 `unsubscribe_task_done` 的句柄，或 None。"""
        raise NotImplementedError

    def unsubscribe_task_done(self, handle):
        """注销完成通知；默认无操作。"""
        return None

    def negotiate_version(self, peer_version=None):
        peer = peer_version if peer_version is not None else self.interface_version
        return negotiate(peer, self.interface_version)

    def close(self):
        """关闭连接 / 释放资源；默认无操作。"""
        return None


class NullMcpClient(McpClient):
    """占位客户端：未接入 Hadoop 侧时所有调用返回结构化错误，**不连网**。"""

    interface_version = MCP_INTERFACE_VERSION

    def _unavailable(self, action):
        return {"ok": False, "interface_version": self.interface_version,
                "error": {"code": CODE_MCP_UNAVAILABLE,
                          "message": "MCP 客户端未接入（阶段 8 只预留接缝）：%s"
                                     % action}}

    def list_tools(self):
        return []

    def call_tool(self, name, args):
        return self._unavailable("call_tool(%s)" % name)

    def get_task_status(self, task_id):
        return self._unavailable("get_task_status(%s)" % task_id)

    def subscribe_task_done(self, callback, task_id=None):
        return None
