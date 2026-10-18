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

#: 远端只读类（加性扩展）：沿用远端能力服务 `hadoop/mcpserver/contract.py` 的既有名称，
#: 两端各造一套会让对端归错类，因此这里与之同名同义。
CODE_FILE_NOT_FOUND = "FILE_NOT_FOUND"
CODE_FORBIDDEN_PATH = "FORBIDDEN_PATH"
CODE_CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
#: 同名方案已存在且未允许覆盖（远端 `upload_scheme`；与 FORBIDDEN_PATH 等只读类同源）。
CODE_SCHEME_EXISTS = "SCHEME_EXISTS"
#: 预留码：远端登记在保留集里（当前不以错误形式出现，而是「成功 + 不可预览」）。
#: 本端词汇表仍收录它，便于对端出现该码时能给出可读说明，而不是裸露原始码。
CODE_NOT_PREVIEWABLE = "NOT_PREVIEWABLE"

#: 入口层（加性扩展）：发生在协议进入业务之前 —— 口令配错、来源地址配错时看到的就是它们。
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_FORBIDDEN_HOST = "FORBIDDEN_HOST"

#: 中止类（加性扩展）：信号发不出去，或任务已不在可中止的状态。
CODE_CANCEL_FAILED = "CANCEL_FAILED"
#: 任务已被中止（**终态**），没有结果可取。不归成「尚未完成」——那会让对端反复重试。
CODE_ABORTED = "ABORTED"

#: 等待名额用尽（加性扩展）：并发等待有上限，超出时立刻如实拒绝。
CODE_WAIT_LIMIT_REACHED = "WAIT_LIMIT_REACHED"

#: 产物内容不可用（加性扩展）：文件在，但内容读不出或不合法。与 FILE_NOT_FOUND 区分。
CODE_PRODUCT_INVALID = "PRODUCT_INVALID"

#: 远端内部错误（加性扩展）：处理中出现未预期异常，说明里带原因，不掩盖。
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

#: 模型不存在（加性扩展，对齐迭代二数据契约 §5）：预测或取模型时编号不存在，
#: 详情里带 `model_id`。与 TASK_NOT_FOUND / FILE_NOT_FOUND 区分——它专指模型编号。
CODE_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"

#: 结构化错误码全集（§5.4 表 + 远端扩展码 + 传输层）—— 本端可能出现在信封
#: `error.code` 里的全部取值，供 `normalize_error` / `describe_error` 复用。
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
    CODE_ABORTED,
    CODE_FILE_NOT_FOUND,
    CODE_FORBIDDEN_PATH,
    CODE_CONTENT_TOO_LARGE,
    CODE_SCHEME_EXISTS,
    CODE_PRODUCT_INVALID,
    CODE_CANCEL_FAILED,
    CODE_UNAUTHORIZED,
    CODE_FORBIDDEN_HOST,
    CODE_WAIT_LIMIT_REACHED,
    CODE_INTERNAL_ERROR,
    CODE_NOT_PREVIEWABLE,
    CODE_MODEL_NOT_FOUND,
)

#: 拒绝/排除原因的**细类**（写进错误细节的 `reason_kind` 字段）—— 与远端读取层
#: 共用**同一套取值**（`hadoop/mcpserver/readers.py::REASON_KINDS`），对端归一次类即可：
#:   `control`          服务内部的控制文件（中止标记、提交凭据）
#:   `sidecar`          文件系统层面的附属物（隐藏文件、校验和附属文件）
#:   `residue`          流程中途的残留（临时文件、半成品）
#:   `directory`        该路径是目录，不是可读/可下载的文件
#:   `not_regular_file` 不是普通文件（命名管道、设备、套接字）—— 读它会挂住
REASON_KINDS = ("control", "sidecar", "residue", "directory", "not_regular_file")

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


#: 结构化错误码 → 面向用户的**可读中文**说明。出口文案统一从这里取，避免各处各写一套。
#: 红线：文案用自然语言描述场景，**不得**出现行业黑话等受限词。
ERROR_MESSAGES = {
    CODE_USAGE: "参数不合法：请检查调用参数后重试。",
    CODE_TASK_FAILED: "任务执行失败：请查看任务日志了解具体原因。",
    CODE_TASK_NOT_FOUND: "找不到该任务：请确认任务编号是否正确。",
    CODE_TASK_NOT_FINISHED: "任务尚未完成：请稍后查询状态。",
    CODE_VERSION_CONFLICT: "版本冲突：数据版本已变化，请重新发起。",
    CODE_DEPENDENCY_MISSING: "缺少前置依赖：请先完成所依赖的步骤。",
    CODE_TASK_ALREADY_RUNNING: "已有同类任务在运行：请等待其结束后再试。",
    CODE_DRIVER_NO_OUTPUT: "执行端没有返回内容：请稍后重试，或联系维护人员。",
    CODE_DRIVER_BAD_OUTPUT: "执行端返回内容无法解析：请联系维护人员。",
    CODE_DRIVER_UNREACHABLE: "无法连接执行端：请确认服务是否已启动。",
    CODE_DRIVER_TIMEOUT: "调用执行端超时：请稍后重试。",
    CODE_MCP_UNAVAILABLE: "远端能力服务未接入或未配置。",
    CODE_ABORTED: "任务已被中止：没有可用的结果。",
    CODE_FILE_NOT_FOUND: "文件不存在：请确认路径是否正确。",
    CODE_FORBIDDEN_PATH: "该路径不允许访问。",
    CODE_CONTENT_TOO_LARGE: "内容过大，无法在界面展示：请下载后查看。",
    CODE_SCHEME_EXISTS: "同名方案已存在：请改名后重试，或允许覆盖同名方案。",
    CODE_PRODUCT_INVALID: "产物内容不可用：文件存在，但内容无法读取。",
    CODE_CANCEL_FAILED: "中止任务失败：任务可能已结束或无法中止。",
    CODE_UNAUTHORIZED: "连接凭据不正确：请核对口令配置。",
    CODE_FORBIDDEN_HOST: "该来源地址不允许访问：请核对服务地址与转发配置。",
    CODE_WAIT_LIMIT_REACHED: "等待名额已满：请稍后再试。",
    CODE_INTERNAL_ERROR: "服务内部错误：请稍后重试，或联系维护人员。",
    CODE_NOT_PREVIEWABLE: "该文件不支持在线预览：可下载后查看。",
    CODE_MODEL_NOT_FOUND: "模型编号不存在。",
}

#: driver 自产码的中文说明（不在 §5.4 表内，但与结构化码一并给出可读文案）。
_DRIVER_NATIVE_MESSAGES = {
    "CONFIG_INVALID": "配置不合法：请检查配置内容后重试。",
}

#: 兜底说明（无码、无 message 时）。
UNKNOWN_ERROR_MESSAGE = "发生未知错误：请联系维护人员。"


def describe_error(error):
    """把一个错误（dict 或码字符串）翻成面向用户的**可读中文**。

    回退顺序（前者命中即返回）：
      1. 已知结构化码 / driver 自产码 → 本端固定中文说明；
      2. 远端信封里的 `error.message`（原样转述，不改成别的结论）；
      3. 原始码（未知码也不丢，便于排障）；
      4. 兜底说明。
    """
    code = None
    message = None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
    elif isinstance(error, str):
        code = error
    elif error is not None:
        message = str(error)

    if code in ERROR_MESSAGES:
        return ERROR_MESSAGES[code]
    if code in _DRIVER_NATIVE_MESSAGES:
        return _DRIVER_NATIVE_MESSAGES[code]
    if message:
        return str(message)
    if code:
        return str(code)
    return UNKNOWN_ERROR_MESSAGE


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
