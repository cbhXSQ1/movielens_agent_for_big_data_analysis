# -*- coding: utf-8 -*-
"""mcpserver/contract.py —— 契约版本、状态取值、错误码与信封（纯常量与纯函数）。

对齐三份材料：
  * `docs/hadoop/迭代二_MCP端侧开发要求.md` §6（返回信封）、§9（错误码映射）、§7（长任务）；
  * `docs/agent/Agent_接口文档.md` §5.4（错误码映射）、§3.8（任务状态取值）；
  * `hadoop/driver/run_task.py` 的退出码与错误码常量（**唯一实现来源**，不另造一套）。

两条不变量（改这里之前请先读）：
  1. **语义无损**：已经是明确错误类别的错误原样向上传递，绝不压成笼统错误；
     只有退出码可依时才按表换算，并把原退出码记进 `driver_code` 留档。
  2. **只加不减**：对外字段只增加不修改；确要改语义时先升 `INTERFACE_VERSION`。

本模块不导入任何第三方包，也不导入 driver 执行链 —— 契约要能在裸解释器里被测试。
"""

import re

# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------

#: 本端对外契约版本。对齐对端占位里的预留值（`agent/mcp_contract.py`），两端同号。
#: 加性变更不升号；字段语义变更必须先升号并同步两端。
INTERFACE_VERSION = "1.0"

#: 被包装的 driver 契约版本（`docs/hadoop/agent-interface.md` 当前为 1.1）。
#: 只作为能力自描述里的信息，不参与本端版本判断。
DRIVER_INTERFACE_VERSION = "1.1"

# ---------------------------------------------------------------------------
# 任务状态
# ---------------------------------------------------------------------------

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
#: 本端加性新增：任务被中止。**必须由本端真的写进状态文件**，否则中止后
#: 任务会永久停在运行中、把占用一直挂住（见实现方案 §7）。
STATUS_ABORTED = "aborted"

#: 全部状态取值（顺序即展示顺序）。
STATUSES = (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_ABORTED,
)

#: 终态：到这些状态后不会再变化。`aborted` 也是终态 —— 它不再占用任务锁。
TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_ABORTED)

#: 中文显示名，供对端直接转述给用户（对端不需要自己维护一份）。
STATUS_LABELS = {
    STATUS_QUEUED: "排队",
    STATUS_RUNNING: "运行",
    STATUS_SUCCEEDED: "成功",
    STATUS_FAILED: "失败",
    STATUS_ABORTED: "已中止",
}

# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------

# 与 driver 同名的结构化码（driver 自产，映射时原样保留）
CODE_USAGE = "USAGE"
CODE_CONFIG_INVALID = "CONFIG_INVALID"
CODE_TASK_FAILED = "TASK_FAILED"
CODE_TASK_NOT_FOUND = "TASK_NOT_FOUND"
CODE_TASK_NOT_FINISHED = "TASK_NOT_FINISHED"
CODE_TASK_ALREADY_RUNNING = "TASK_ALREADY_RUNNING"
CODE_VERSION_CONFLICT = "VERSION_CONFLICT"
CODE_DEPENDENCY_MISSING = "DEPENDENCY_MISSING"

# 传输层：本端调用 driver 时的失败（无输出 / 输出不合法 / 跑不起来 / 超时）
CODE_DRIVER_NO_OUTPUT = "DRIVER_NO_OUTPUT"
CODE_DRIVER_BAD_OUTPUT = "DRIVER_BAD_OUTPUT"
CODE_DRIVER_UNREACHABLE = "DRIVER_UNREACHABLE"
CODE_DRIVER_TIMEOUT = "DRIVER_TIMEOUT"

# 只读类（加性新增）。命名**沿用对端接口文档 §3.11.5 的既有名称**，两端不各造一套。
CODE_FILE_NOT_FOUND = "FILE_NOT_FOUND"
CODE_FORBIDDEN_PATH = "FORBIDDEN_PATH"
CODE_CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
#: 预留：对端接口文档里有这个名字，保留常量便于两端对齐。但本端**不以错误形式**
#: 表达"不能预览"——按对端 §3.11.4 的既有约定，二进制产物一律回"成功 + 不可预览"。
#: 因此它**不出现在对外公布的错误码集合里**（公布了却永不出现，等于给对端一个假承诺）。
CODE_NOT_PREVIEWABLE = "NOT_PREVIEWABLE"

# 入口层（加性新增）：这两条发生在协议进入业务之前，但对端最容易撞到 ——
# 口令配错、端口转发配错时看到的就是它们，必须出现在能力自描述里，否则对端无从归类。
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_FORBIDDEN_HOST = "FORBIDDEN_HOST"

# 中止类（加性新增）：信号发不出去、或任务已不在可中止的状态
CODE_CANCEL_FAILED = "CANCEL_FAILED"

# 等待名额用尽（加性新增）：并发等待有上限，超出时立刻如实拒绝而不是排队排到天荒地老。
CODE_WAIT_LIMIT_REACHED = "WAIT_LIMIT_REACHED"

# 任务已被中止、因此没有结果可取（加性新增）。为什么不归成"尚未完成"：中止是**终态**，
# 不会再变化；说成"未完成"会让对端反复重试、等一个永远不会来的结果。
CODE_ABORTED = "ABORTED"

# 产物内容不可用（加性新增）：文件在，但内容读不出来或不合法（例如计数文件损坏）。
# 与 FILE_NOT_FOUND 区分开 —— 一个说"没有"，一个说"有但用不了"，补救办法不同。
CODE_PRODUCT_INVALID = "PRODUCT_INVALID"

# 本端内部错误（加性新增）：处理过程中出现未预期的异常。**不掩盖** —— 说明里
# 带上异常类型与信息，便于排障；绝不因此返回半份业务结果。
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

#: driver 自产的码（映射时原样保留，绝不降级）。顺序即优先级无关，仅作登记。
DRIVER_NATIVE_CODES = (
    CODE_CONFIG_INVALID,
    CODE_TASK_ALREADY_RUNNING,
    CODE_DEPENDENCY_MISSING,
)

#: 结构化错误码全集（本端可能出现在信封 `error.code` 里的全部取值）。
STRUCTURED_ERROR_CODES = (
    CODE_USAGE,
    CODE_CONFIG_INVALID,
    CODE_TASK_FAILED,
    CODE_TASK_NOT_FOUND,
    CODE_TASK_NOT_FINISHED,
    CODE_TASK_ALREADY_RUNNING,
    CODE_VERSION_CONFLICT,
    CODE_DEPENDENCY_MISSING,
    CODE_DRIVER_NO_OUTPUT,
    CODE_DRIVER_BAD_OUTPUT,
    CODE_DRIVER_UNREACHABLE,
    CODE_DRIVER_TIMEOUT,
    CODE_FILE_NOT_FOUND,
    CODE_FORBIDDEN_PATH,
    CODE_CONTENT_TOO_LARGE,
    CODE_CANCEL_FAILED,
    CODE_ABORTED,
    CODE_WAIT_LIMIT_REACHED,
    CODE_PRODUCT_INVALID,
    CODE_INTERNAL_ERROR,
    CODE_UNAUTHORIZED,
    CODE_FORBIDDEN_HOST,
)

#: 保留但当前不会以错误形式出现的码（见各常量的说明）。
RESERVED_ERROR_CODES = (CODE_NOT_PREVIEWABLE,)

# ---------------------------------------------------------------------------
# 退出码 → 结构化错误码
# ---------------------------------------------------------------------------

#: driver 退出码（与 `hadoop/driver/run_task.py` 的 EXIT_* 一致；此处写字面量，
#: 避免契约层反向依赖执行层。测试里会与 driver 常量逐项比对，防止漂移）。
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 3
EXIT_NOT_FOUND = 4
EXIT_NOT_FINISHED = 5
EXIT_CONFLICT = 6

#: 退出码 → 结构化码；成功返回 None。
EXIT_CODE_MAP = {
    EXIT_OK: None,
    EXIT_USAGE: CODE_USAGE,
    EXIT_FAILED: CODE_TASK_FAILED,
    EXIT_NOT_FOUND: CODE_TASK_NOT_FOUND,
    EXIT_NOT_FINISHED: CODE_TASK_NOT_FINISHED,
    EXIT_CONFLICT: CODE_VERSION_CONFLICT,
}

#: 本端内部调用 driver 时的失败分类（恒等映射，集中登记便于统一复用）。
TRANSPORT_CODE_MAP = {
    CODE_DRIVER_NO_OUTPUT: CODE_DRIVER_NO_OUTPUT,
    CODE_DRIVER_BAD_OUTPUT: CODE_DRIVER_BAD_OUTPUT,
    CODE_DRIVER_UNREACHABLE: CODE_DRIVER_UNREACHABLE,
    CODE_DRIVER_TIMEOUT: CODE_DRIVER_TIMEOUT,
}


def code_for_exit(exit_code):
    """退出码 → 结构化码；0 或未知返回 None（调用方保留原码，不静默丢语义）。"""
    if exit_code is None:
        return None
    return EXIT_CODE_MAP.get(exit_code)


def code_for_transport(transport_code):
    """传输层码 → 结构化码（未知原样透传）。"""
    return TRANSPORT_CODE_MAP.get(transport_code, transport_code)


def _known_code(code):
    return code in STRUCTURED_ERROR_CODES or code in DRIVER_NATIVE_CODES


def normalize_error(err, exit_code=None):
    """把一个错误归一为结构化错误码；**语义无损**。

    优先级从高到低：
      1. 已是结构化码或 driver 自产码 → 原样保留（绝不降级）；
      2. 仅有退出码可依 → 按表换算，并把原退出码记进 `driver_code`；
      3. 什么都没有 → 回落到 DRIVER_BAD_OUTPUT（并保留 message）。

    `message`、`details`、`stage` 等既有字段一律保留；只做加法，不删字段。
    """
    out = {}
    if isinstance(err, dict):
        out.update(err)
    elif err is not None:
        out["message"] = str(err)

    code = out.get("code")
    if isinstance(code, str) and _known_code(code):
        # 已是明确类别：原样保留；顺便记下退出码（若无则补 None，便于排障对照）
        if exit_code is not None and "driver_code" not in out:
            out["driver_code"] = exit_code
        return out

    mapped = code_for_exit(exit_code)
    if mapped:
        if code and code != mapped:
            # 原码不丢：救回一个字段，便于排障时对照 driver 的实现细节
            out.setdefault("driver_code_text", code)
        out["code"] = mapped
        out["driver_code"] = exit_code
        return out

    if code:
        # 未知码：保留原样，不做静默改写
        if exit_code is not None:
            out.setdefault("driver_code", exit_code)
        return out

    out["code"] = CODE_DRIVER_BAD_OUTPUT
    if exit_code is not None:
        out["driver_code"] = exit_code
    return out


# ---------------------------------------------------------------------------
# 信封
# ---------------------------------------------------------------------------

def envelope_ok(**payload):
    """成功信封：`ok=true` + 契约版本 + 业务字段。

    只做加法 —— 传入什么就带什么，不替调用方编造任何业务数字。
    """
    out = {"ok": True, "interface_version": INTERFACE_VERSION}
    out.update(payload)
    return out


def envelope_error(code, message, **extra):
    """失败信封：`ok=false` + `error{code,message,...}`。

    `message` 面向用户，应当能直接转述；`extra` 放定位信息（哪个任务、哪份产物）。
    """
    err = {"code": code, "message": message}
    for key, value in extra.items():
        if value is not None:
            err[key] = value
    return {"ok": False, "interface_version": INTERFACE_VERSION, "error": err}


# ---------------------------------------------------------------------------
# 任务目录里的服务控制文件（**唯一来源**）
# ---------------------------------------------------------------------------

#: 中止请求标记：服务写下它表示"这个任务被要求停下"，执行侧据此在收尾时如实写成已中止。
CONTROL_FILE_CANCEL = "cancel_request.json"
#: 提交凭据：服务受理成功后写下它，证明"这个任务是经本服务提交的"，
#: 用于在"状态说在跑、进程其实没了"时安全地释放执行位。
CONTROL_FILE_SUBMIT = "mcp_submit.json"
#: 全部控制文件。写入方、排除方、判定方一律引用这里，不要各写字面量 ——
#: 改名时只改一处；另有一条跨模块断言测试锁住"三个地方的名单一致"。
CONTROL_FILE_NAMES = (CONTROL_FILE_CANCEL, CONTROL_FILE_SUBMIT)

# ---------------------------------------------------------------------------
# 任务编号
# ---------------------------------------------------------------------------

#: 任务编号的合法形状：**单层目录名**。执行侧把任务编号直接拼进产物目录路径，
#: 因此这里必须挡住上级跳转、路径分隔符、绝对路径、反斜杠与空字节 —— 否则
#: 「读某个任务的状态」会被用来读任务根之外的任意目录（实测可行，见审查记录）。
TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


def valid_task_id(value):
    """任务编号形状是否合法（与执行侧生成的编号形状一致）。"""
    if not isinstance(value, str):
        return False
    if not value or value in (".", ".."):
        return False
    if any(ch in value for ch in ("/", "\\", "\x00")):
        return False
    return re.match(TASK_ID_PATTERN, value) is not None


class StructuredError(Exception):
    """工具内部抛出的、已经带结构化码的错误；由服务层转成失败信封。

    用它而不是到处 `return envelope_error(...)`，是为了让"业务判定"与
    "协议封装"分开：工具体只负责判断与抛错，服务层统一成信封。
    """

    def __init__(self, code, message, **extra):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.extra = dict(extra)

    def to_envelope(self):
        return envelope_error(self.code, self.message, **self.extra)
