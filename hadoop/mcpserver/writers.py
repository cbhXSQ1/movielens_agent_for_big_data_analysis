# -*- coding: utf-8 -*-
"""mcpserver/writers.py —— 写操作类能力的处理器（校验方案、列方案、发起任务、独立评分）。

本段负责六条能力（`docs/hadoop/迭代一重构_MCP_实现方案.md` §3.1 + 端侧要求 §22）：

    validate_config      → driver `validate`
    list_schemes         → driver `schemes`
    start_cleaning_task  → driver `start --task-type full`
    start_clean_task     → driver `start --task-type clean`
    score_task           → driver `score --source raw|published|task`
    upload_scheme        → 落一份自定义方案到方案目录（先校验后落盘，见 `handle_upload_scheme`）

分工：本文件**只做参数搬运与说明补全**，执行体一律经 `driver_bridge` 调现有命令，
不复制任何业务逻辑（实现方案 §13「不动现有业务」）。处理器成功时回业务字段、
失败时抛 `StructuredError`；`ok` 与 `interface_version` 由分派层统一包。

四条写法约定（照做，别各写一套）
--------------------------------
1. **判定权在 driver。** 版本冲突、任务占用、依赖缺失，一律由 driver 判定；本端只在
   它的结论上补「占用者是谁、怎么解除」「两边版本各是什么」这类说明，**绝不抢先
   得出一个可能与之不同的结论**（两边各说一套是这类系统最难查的故障）。
   唯一的例外是**本机读得到、结论确定**的前置条件：独立评分要「指定任务」的产物时，
   提交前先同步看一眼任务目录（见 `check_task_dependency`）。它不推翻 driver 的任何
   结论，只是把本可以当场回答的问题当场回答掉；需要问集群的来源不做同样的检查。
2. **进度只来自真实状态；耗时是明确标注的参考值。** 返回里不放任何自己算出来的业务数字：
   进度、阶段、计数与评分一律只在查询类能力里从真实产物取；受理类能力**不报告进度**。
   唯一例外是受理时给出的预计耗时 —— 它取自能力清单里为该能力声明的参考值，并**显式
   标注为估算**（附一句"不是本次实测"），因为对端要用它决定挂起多久，而真实耗时只有等
   任务进了终态才知道。绝不把参考值冒充成实测值。
3. **不暴露机器上的绝对路径。** driver 的信封里有 `task_dir` 之类的本机绝对位置，
   对外一律换成「任务编号」；对端要取产物时用任务编号 + 相对路径。
4. **受理成功要留凭据。** 三条发起/评分能力在 driver 受理成功之后，往任务目录写一份
   提交标记（文件名引用契约层的唯一来源，见 `write_submit_marker`）：它是「这台机器上的任务是谁起的」的
   可靠依据，没有它，中止能力遇到「状态写着在跑、进程却已经没了」只能保守拒绝，
   执行位会一直挂着。

执行体一律经 `driver_bridge` 调现有命令，本模块**不直接引用 driver 执行链**
（不 import `driver.*`），避免同一个进程里同时存在桥接层与外层两份执行状态 ——
出事时谁也说不清是谁的问题。

五个处理器导在 `HANDLERS` 里，由 `dispatch.load_handlers` 取用。
"""

import datetime
import io
import json
import os
import sys

from .contract import (
    CODE_CONFIG_INVALID,
    CODE_CONTENT_TOO_LARGE,
    CODE_DEPENDENCY_MISSING,
    CODE_DRIVER_BAD_OUTPUT,
    CODE_FORBIDDEN_PATH,
    CODE_SCHEME_EXISTS,
    CODE_USAGE,
    CODE_TASK_ALREADY_RUNNING,
    CODE_VERSION_CONFLICT,
    CONTROL_FILE_SUBMIT,
    STATUS_LABELS,
    STATUS_SUCCEEDED,
    StructuredError,
    valid_task_id,
)
from . import driver_bridge as bridge
from .tools import get_tool

#: 执行方式：只接受集群执行（实现方案 §4「执行方式」行；本机本地执行属排障手段）。
EXEC_MODE_CLUSTER = "cluster"

#: 发起类能力的任务类型取值（与 driver 的 `--task-type` 枚举一致）。
TASK_TYPE_FULL = "full"
TASK_TYPE_CLEAN = "clean"
#: 独立评分的任务类型取值（driver 的 `TASK_TYPES` 里的第三种）。
TASK_TYPE_SCORE = "score"

#: 强制开关打开时的后果说明。开关能用与否在返回里都要写清（方案 §8）。
FORCE_WARNING = (
    "已打开强制开关：本次发起忽略了「已有任务在运行」的限制，"
    "可能与占用者并行推进；两者若写同一批清洗产物或同一个发布版本，结果需自行核对。"
)

#: 受理时给出的预计耗时是**参考值**，必须与实测区分开（方案 §5：长任务要给对端
#: 一个可挂起展示的预计时长，但不得冒充真实观测）。
ESTIMATE_NOTE = (
    "预计耗时取自能力清单里声明的参考值，不是本次实测；"
    "真实耗时以任务进入终态时记录的时间为准。"
)

#: 「本次任务由本服务提交」标记的文件名。**唯一来源在契约层**
#: （`contract.CONTROL_FILE_SUBMIT`）：写入方（本模块）、排除方（只读能力）、
#: 判定方（中止能力）都引用同一份名单，改名只改一处。这里只做别名，便于本模块内引用。
SUBMIT_MARKER = CONTROL_FILE_SUBMIT

#: 标记里「谁提交的」的取值；标明这份凭据是本服务留下的。
SUBMIT_BY = "mcp"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _now_utc():
    """与 driver 同格式的 UTC 时间戳（`2026-10-17T12:00:00Z`），便于两边直接对照。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_dir(task_id, ctx):
    """任务目录：`<产物目录>/tasks/<任务编号>`。

    产物目录取 `ML_VAR_DIR` 的解析结果（进程环境 / 上下文 / 环境脚本三处，与
    driver 子进程看到的是同一个值）；一处都没给时按 driver 的默认落在仓库根的
    `var` 下。**不写死任何绝对路径**。
    """
    var_dir = bridge.env_value("ML_VAR_DIR", ctx) \
        or os.path.join(bridge.repo_root_of(ctx), "var")
    return os.path.join(var_dir, "tasks", task_id)


def write_submit_marker(task_id, ctx, tool, orientation=None):
    """在任务目录里写下「本次任务由本服务提交」的标记；返回文件路径或 None。

    为什么要这个标记：执行位可能卡在「状态写着在跑、进程其实已经没了」——这时谁也不敢
    凭「找不到进程」就改状态，因为那可能误伤别人在终端里以前台方式起的任务，结果执行位
    一直挂着。有了这份凭据，中止能力才能在「进程确实已消失」时安全地把任务落成已中止、
    释放执行位；没有凭据时只能保守拒绝。所以它必须**由受理成功的那一方写**，而不是
    由事后判断的那一方补。

    写失败**不影响受理结果**：任务确确实实已经被 driver 接受了，因为一份辅助标记写不进去
    就回「没提交成功」，只会让用户重复发起、把执行位真的占死。出错只记一行日志。

    落盘用「先写临时文件再改名」：读的一方要么看到完整标记，要么看不到，不会读到半个文件
    而误判成「有凭据」。
    """
    payload = {
        "submitted_by": SUBMIT_BY,
        "submitted_at": _now_utc(),
        "tool": tool,
        "task_id": task_id,
    }
    for key, value in (orientation or {}).items():
        if value not in (None, ""):
            payload[key] = value

    directory = _task_dir(task_id, ctx)
    path = os.path.join(directory, SUBMIT_MARKER)
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
        temp_path = path + ".tmp"
        with io.open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            handle.write(u"\n")
        os.replace(temp_path, path)
    except (IOError, OSError, ValueError) as exc:
        # 只记日志：受理已经成功，不能因为这份辅助标记写不进去就改结论
        sys.stderr.write("[mcpserver] 任务 %s 的提交标记没写成（不影响受理结果）：%s: %s\n"
                         % (task_id, type(exc).__name__, exc))
        return None
    return path


def _scheme_options(args):
    """两份方案路径 → driver 选项；没给就不传（沿用 driver 的默认方案）。"""
    options = {}
    if args.get("rules"):
        options["rules"] = args["rules"]
    if args.get("scoring"):
        options["scoring"] = args["scoring"]
    return options


def _join_message(base, remedy):
    """把 driver 的原话与补救说明拼成一段能直接转述给用户的话（不覆盖原话）。"""
    base = (base or "").strip()
    if base and not base.endswith(("。", ".", "；", ";")):
        base += "。"
    return (base + remedy).strip()


def _passthrough(err, drop=()):
    """driver 错误里的其余字段原样带上去（定位信息不能丢）。"""
    skip = {"code", "message"}
    skip.update(drop)
    return {key: value for key, value in err.items() if key not in skip}


def _estimated_seconds(tool_name):
    """取该能力在工具表里声明的预计耗时；没有就返回 None（不编数字）。"""
    tool = get_tool(tool_name) or {}
    value = tool.get("estimated_seconds")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


#: 从任务状态文件里抄得出来的"真实生效"取向字段。
_STATUS_ORIENTATION_KEYS = ("scope", "data_version", "task_type")


def _effective_from_status(task_id, ctx):
    """受理后从任务状态文件里读**真实生效**的取向（范围、数据版本、任务类型）。

    为什么读状态文件而不是直接回用户请求：用户没给数据版本时，真正用的是方案里声明的
    那一版，只有执行侧知道；把它原样抄回来，对端拿到的就是事实，而不是"请求里没写"。
    读不到就差集回退给调用方用归一后的请求值（状态文件刚写完、极少数情况下还没落盘）。

    没有任务编号时直接回空：信封缺任务编号是"执行侧回的东西不完整"，该由 `_accepted`
    统一报出来，不在这里因为拼路径而炸成另一个错误。
    """
    if not task_id:
        return {}
    status = _read_json_file(os.path.join(_task_dir(task_id, ctx), "status.json"))
    if not isinstance(status, dict):
        return {}
    return {key: status[key] for key in _STATUS_ORIENTATION_KEYS
            if isinstance(status.get(key), str) and status[key].strip()}


def _accepted(payload, task_type, effective, tool_name):
    """受理类能力的返回：任务编号、状态、受理时间、真实生效的取向、预计耗时。

    受理时间用 driver 写的 `started_at`（它就是在受理那一刻写入的），不另造一个
    时间字段；`status` 照 driver 的原值回（当前实现是 `queued`），本端不替它推断。

    `effective` 里的取值必须是**真实生效**的那一份：优先取执行侧状态文件回传的值，
    取不到才用分派层归一后的请求值 —— 绝不能回"用户请求里写了什么"就完事，否则
    对端会拿到一个和实际执行不一致的取向（例如请求里没写版本、却回了空版本）。
    """
    out = {}
    for key in ("task_id", "status", "started_at"):
        if payload.get(key) is not None:
            out[key] = payload[key]
    if not out.get("task_id"):
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 回了成功信封，但里面没有任务编号，无法跟进这次发起；请重试",
            subcommand=bridge.CMD_START, task_type=task_type)

    out["task_type"] = effective.get("task_type") or task_type
    for key in ("scope", "data_version"):
        if effective.get(key):
            out[key] = effective[key]
    if effective.get("force"):
        out["warnings"] = [FORCE_WARNING]

    estimate = _estimated_seconds(tool_name)
    if estimate is not None:
        out["estimated_seconds"] = estimate
        out["estimated_seconds_note"] = ESTIMATE_NOTE
    return out


# ---------------------------------------------------------------------------
# 失败说明：只加信息，不改结论
# ---------------------------------------------------------------------------

def _busy_failure(err, force, supports_force):
    """占用冲突（`TASK_ALREADY_RUNNING`）：说清占用者是谁、怎么解除。

    结论（码）用 driver 的；driver 已经把占用者写进 `task_id` 字段，这里原样保留
    并额外给一个语义明确的名字，省得对端猜。
    """
    occupier = err.get("task_id") or err.get("occupier") or ""
    head = err.get("message") or "已有任务在运行"
    if occupier and occupier not in head:
        head = _join_message(head, "当前占用执行位的是任务 %s。" % occupier)

    if supports_force:
        if force:
            remedy = ("已经打开强制开关，driver 仍判定存在占用，说明不是「有任务在跑」这一条，"
                      "请按上面说明的其它原因处理。")
        else:
            remedy = ("解除办法有二：等占用者跑到终态（用查状态或等任务工具跟进），"
                      "或在确认不影响产物与发布的前提下打开强制开关重试 —— "
                      "强制开关会让两个任务并行走，后果需自行承担。")
    else:
        remedy = ("独立评分与发起类任务共用同一个执行位，当前实现没有提供绕过它的开关；"
                  "请等占用者跑到终态后再发起。")

    detail = _passthrough(err)
    detail.update({
        "occupier": occupier or None,
        "force": bool(force),
        "force_supported": bool(supports_force),
        "remedy": remedy,
    })
    return StructuredError(err.get("code") or CODE_TASK_ALREADY_RUNNING,
                           _join_message(head, remedy), **detail)


def _version_conflict_failure(err, args):
    """版本冲突（`VERSION_CONFLICT`）：把「两边各是什么」和「怎么办」说清。

    driver 的说明里已经写了请求的版本与方案声明的版本；本端补上处理办法，
    并把用户这次请求的版本单独留一份字段，便于对端直接展示。
    """
    requested = args.get("data_version") or ""
    if requested:
        remedy = ("处理办法：要沿用这份方案，就把请求的数据版本改成方案里声明的那一个；"
                  "要采用新的方案内容，就先升数据版本再发起。")
    else:
        remedy = ("处理办法：同一个数据版本不允许装不同内容，内容变了就必须升数据版本再发起。")
    detail = _passthrough(err)
    detail.update({
        "requested_data_version": requested or None,
        "remedy": remedy,
    })
    return StructuredError(err.get("code") or CODE_VERSION_CONFLICT,
                           _join_message(err.get("message"), remedy), **detail)


def _dependency_failure(err, args):
    """前置依赖缺失（`DEPENDENCY_MISSING`）：原样透传 driver 的结论，只加补救提示。"""
    source = args.get("source") or ""
    remedy = {
        "task": "按上面说明补齐后重试：被评分的来源任务必须处于成功状态，且它的清洗产物齐全。",
        "published": "按上面说明补齐后重试：发布区里必须存在这次要评分的那个数据版本。",
        "raw": "按上面说明补齐后重试：原始三表必须可读。",
    }.get(source, "按上面说明补齐后再试。")
    detail = _passthrough(err)
    detail.update({
        "source": source or None,
        "from_task": args.get("from_task") or None,
        "remedy": remedy,
    })
    return StructuredError(err.get("code") or CODE_DEPENDENCY_MISSING,
                           _join_message(err.get("message"), remedy), **detail)


# ---------------------------------------------------------------------------
# 上传自定义方案（端侧要求 §22）
# ---------------------------------------------------------------------------

#: 方案配置目录（相对仓库根）。与执行侧「列方案 / 校验方案」看的是同一个目录。
SCHEME_DIR = "config"

#: 两份**随仓库交付的默认方案**文件名。它们不许被上传覆盖（见 `_reject_default_scheme`）：
#: 覆盖会把仓库改脏，并悄悄改变所有人的默认行为 —— 即使调用方显式要求覆盖也不行。
#: 这两个名字来自执行侧（`driver/commands.py` 的 DEFAULT_RULES / DEFAULT_SCORING），
#: 本段不导入执行链，改由测试逐项比对防止漂移。
DEFAULT_RULES_NAME = "cleaning_rules.v1.json"
DEFAULT_SCORING_NAME = "scoring_scheme.v1.json"

#: 方案类型的两种取值（与内容里的 `config_type` 一致）。
SCHEME_TYPE_CLEANING = "cleaning_rules"
SCHEME_TYPE_SCORING = "scoring_scheme"
SCHEME_TYPES = (SCHEME_TYPE_CLEANING, SCHEME_TYPE_SCORING)

#: 上传内容大小上限（字节）。1 MB 的依据：方案是声明式配置，正常的清洗规则约几十 KB；
#: 这个上限防的是把整份数据集当"方案"塞进来，不是防正常使用。
MAX_SCHEME_BYTES = 1024 * 1024

#: 上传落盘用的临时文件名前缀：**隐藏名，且故意不带 .json 后缀** ——
#: 「列方案」只认 `.json` 结尾，这样校验窗口里它不会被当成一个新方案露出来。
SCHEME_TEMP_PREFIX = ".__upload_tmp_"


def _scheme_directory(ctx):
    """方案配置目录的绝对路径（相对仓库根，不写死任何前缀）。"""
    return os.path.join(bridge.repo_root_of(ctx), SCHEME_DIR)


def _scheme_relative(name):
    """对外展示用的相对位置（与「列方案」条目里的 path 同形）。"""
    return "%s/%s" % (SCHEME_DIR, name)


def _validate_uploaded_pair(temp_relative, scheme_type, ctx):
    """用与「校验方案」**同一条通路**做校验：校验器是成对校验两份配置的。

    上传清洗规则 → 它当规则、配默认评分方案；
    上传评分方案 → 它当评分方案、配默认清洗规则。
    这样"上传时能不能用"与"校验方案时说能不能用"是同一个结论，不会两边各说一套。
    """
    options = {}
    if scheme_type == SCHEME_TYPE_CLEANING:
        options["rules"] = temp_relative
        options["scoring"] = _scheme_relative(DEFAULT_SCORING_NAME)
    else:
        options["rules"] = _scheme_relative(DEFAULT_RULES_NAME)
        options["scoring"] = temp_relative
    return bridge.run_driver(bridge.CMD_VALIDATE, options, ctx)


def _reject_default_scheme(name):
    """默认方案不许被覆盖：先把这条护栏放在最前面，`overwrite=true` 也照拒。

    理由不是"怕手滑"，而是这两个文件定义了**所有人**的默认行为：覆盖它等于悄悄改了
    别人的默认结果，还会把纳入版本库的仓库改脏。要改默认行为，应当另起文件名。
    """
    if name in (DEFAULT_RULES_NAME, DEFAULT_SCORING_NAME):
        raise StructuredError(
            CODE_USAGE,
            "%s 是随仓库交付的默认方案，不允许被上传覆盖（即使要求覆盖也不行）："
            "请另起一个文件名，例如 %s"
            % (name, "my_" + name.replace(".v1", "")),
            param="name",
            remedy="换一个自定义文件名再上传；默认方案的变更应当走仓库里的正式提交。")


def _parse_scheme_content(content):
    """把上传内容解析成对象；不是合法 JSON 或顶层不是对象时，报配置非法并指出坏在哪一步。"""
    try:
        payload = json.loads(content)
    except ValueError as exc:
        raise StructuredError(
            CODE_CONFIG_INVALID,
            "上传内容不是合法 JSON（%s）：没有写入任何文件，请修正后再传" % exc,
            details=["内容不是合法 JSON：%s" % exc],
            step="parse")
    if not isinstance(payload, dict):
        raise StructuredError(
            CODE_CONFIG_INVALID,
            "上传内容的顶层必须是对象（收到 %s）：没有写入任何文件"
            % type(payload).__name__,
            details=["顶层类型应为对象，实际为 %s" % type(payload).__name__],
            step="parse")
    return payload


def _resolve_scheme_type(payload, declared):
    """判定方案类型：内容里的类型字段为准，调用方显式给出的类型必须与之一致。

    两边都判不出来就不能落盘 —— 落一份"不知道是什么"的文件，之后既列不准也校验不了。
    """
    content_type = payload.get("config_type")
    if content_type is not None and content_type not in SCHEME_TYPES:
        raise StructuredError(
            CODE_CONFIG_INVALID,
            "内容里的类型字段是 %r，只接受 %s；没有写入任何文件"
            % (content_type, " 或 ".join(SCHEME_TYPES)),
            details=["config_type 取值非法：%r" % (content_type,)],
            step="type")
    if declared and content_type and declared != content_type:
        raise StructuredError(
            CODE_USAGE,
            "参数里给的类型是 %s，但内容里声明的是 %s，两者不一致：没有写入任何文件；"
            "请修正参数，或修正内容里的类型字段" % (declared, content_type),
            param="type", declared_type=declared, content_type=content_type)
    scheme_type = declared or content_type
    if not scheme_type:
        raise StructuredError(
            CODE_CONFIG_INVALID,
            "无法判定方案类型：内容里没有 config_type 字段，调用时也没有给出 type；"
            "没有写入任何文件",
            details=["缺少 config_type，且未在参数里给出 type"],
            step="type")
    return scheme_type


def _scheme_entry(payload, scheme_type, name):
    """对外条目：字段与「列方案」的条目对齐，便于前端直接刷新列表。"""
    return {
        "scheme_id": payload.get("scheme_id", ""),
        "type": scheme_type,
        "version": payload.get("version", ""),
        "status": payload.get("status", ""),
        "path": _scheme_relative(name),
        "description": payload.get("description", ""),
    }


def handle_upload_scheme(args, ctx):
    """上传一份自定义方案：**先校验，通过才落盘**；失败路径不留任何半成品。

    顺序是刻意的（先判参数与内容，再判目标位置，最后才是写文件）：
    任何一步不满足都在"还没碰文件系统"或"只碰了隐藏临时文件"的阶段停下，
    所以失败时方案目录里的既有文件一个字节都不会变。

    落盘方式：先写成方案目录里的隐藏临时文件 → 用与「校验方案」同一条通路成对校验 →
    通过后原子替换到正式名。因此并发读者要么看到旧内容、要么看到完整新内容。
    """
    name = args.get("name")
    content = args.get("content")
    declared_type = args.get("type")
    overwrite = bool(args.get("overwrite"))

    # 形状校验在分派层统一做（单层 .json 文件名）；这里只做防御性再判，
    # 因为这个函数会把 name 拼进路径，不能指望所有调用路径都经过分派层。
    raw = content.encode("utf-8") if isinstance(content, str) else b""
    if not isinstance(name, str) or os.path.basename(name) != name \
            or not name.endswith(".json") or name in (".", ".."):
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "目标文件名不合法：%r（必须是单层的 .json 文件名，不含任何路径分隔符）"
            % (name,), param="name")

    # 1) 大小上限
    if len(raw) > MAX_SCHEME_BYTES:
        raise StructuredError(
            CODE_CONTENT_TOO_LARGE,
            "上传内容过大：%s 字节，上限是 %s 字节；没有写入任何文件"
            % (len(raw), MAX_SCHEME_BYTES),
            size_bytes=len(raw), limit_bytes=MAX_SCHEME_BYTES)

    # 2) 内容必须是合法 JSON 对象
    payload = _parse_scheme_content(content)

    # 3) 类型判定（内容与参数不一致就拒绝）
    scheme_type = _resolve_scheme_type(payload, declared_type)

    # 4) 身份字段：没有 scheme_id 的文件列不出来，等于"传了但看不见"，不如当场说清
    scheme_id = payload.get("scheme_id")
    if not isinstance(scheme_id, str) or not scheme_id.strip():
        raise StructuredError(
            CODE_CONFIG_INVALID,
            "方案缺少 scheme_id：这样的文件即便写入也无法出现在方案列表里；"
            "没有写入任何文件",
            details=["缺少 scheme_id"], step="identity")

    # 5) 默认方案护栏（放在同名冲突判断之前：默认名一律拒，与是否要求覆盖无关）
    _reject_default_scheme(name)

    # 6) 目标位置必须在方案目录之内（防御性）
    directory = _scheme_directory(ctx)
    target = os.path.join(directory, name)
    real_directory = os.path.realpath(directory)
    real_target = os.path.realpath(target)
    if os.path.dirname(real_target) != real_directory:
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "写入目标越出了方案目录：%r" % (name,), param="name")

    # 7) 同名冲突：缺省拒绝，显式允许覆盖才继续（覆盖同样是"先校验再替换"）
    exists = os.path.exists(real_target)
    if exists and not overwrite:
        raise StructuredError(
            CODE_SCHEME_EXISTS,
            "方案 %s 已存在：没有写入任何文件。可以换个名字，或显式允许覆盖后再传"
            % name,
            name=name, path=_scheme_relative(name),
            remedy="换一个文件名，或在参数里显式要求覆盖（覆盖前同样会先校验）。")

    # 8) 写隐藏临时文件 → 成对校验 → 原子替换
    if not os.path.isdir(real_directory):
        os.makedirs(real_directory)
    temp_name = "%s%s" % (SCHEME_TEMP_PREFIX, os.urandom(6).hex())
    temp_path = os.path.join(real_directory, temp_name)
    try:
        with io.open(temp_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        result = _validate_uploaded_pair(_scheme_relative(temp_name), scheme_type, ctx)
        if not result.ok:
            err = result.error()
            raise bridge.structured_error(
                result,
                message=_join_message(
                    err.get("message") or "方案校验未通过",
                    "校验不通过的方案不会写入；原文件（如果存在）保持原样。"),
                remedy="按 details 里的每一项修正内容后重新上传。",
                step="validate")
        os.replace(temp_path, real_target)
    finally:
        # 无论成功失败，临时文件都不许留下（替换成功后它已经不存在，删它是空操作）
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    validated = result.payload
    warnings = list(validated.get("warnings") or [])
    # 提示而不是拦截：校验器认可 `status=registered_default`，但对**上传件**来说这个标记
    # 会让人以为它是随仓库交付的默认方案（列表里还会出现同名的默认标记）。内容归调用方
    # 所有，我们不擅自改写，只如实提醒 —— 拦下来反而会把"想复制一份默认方案来改"的正常
    # 用法堵死。
    if payload.get("status") == "registered_default":
        warnings.append(
            "内容里声明 status=registered_default，但它是上传文件、不是随仓库交付的默认方案："
            "方案列表里会因此出现一个默认标记，建议把 status 改成 custom 以免混淆。")
    return {
        "scheme": _scheme_entry(payload, scheme_type, name),
        "validation": {
            "passed": True,
            "errors": validated.get("errors", []),
            "warnings": warnings,
        },
        "overwritten": bool(exists),
        "size_bytes": len(raw),
    }


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------

def handle_validate_config(args, ctx):
    """校验两份方案：不读数据、不改数据，只回答「这两份方案能不能用」。"""
    result = bridge.run_driver(bridge.CMD_VALIDATE, _scheme_options(args), ctx)
    if not result.ok:
        err = result.error()
        if err.get("code") == CODE_CONFIG_INVALID:
            message = _join_message(
                err.get("message") or "方案校验未通过",
                "请按 details 里的每一项修正方案后重试；校验不通过的方案不会被用来执行任务。")
            raise bridge.structured_error(
                result, message=message,
                remedy="逐条修正 details 里的问题后重新校验。")
        raise bridge.structured_error(result)

    payload = result.payload
    # 原样回：driver 给什么就回什么，缺哪个字段就不给哪个字段（不填占位）
    return {key: payload[key] for key in ("errors", "warnings", "versions")
            if key in payload}


# ---------------------------------------------------------------------------
# list_schemes
# ---------------------------------------------------------------------------

def handle_list_schemes(args, ctx):
    """列已登记的方案：默认方案 + 自定义方案文件，内容原样回。"""
    result = bridge.run_driver(bridge.CMD_SCHEMES, None, ctx)
    payload = bridge.require_ok(result)
    schemes = payload.get("schemes")
    if schemes is None:
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 的 schemes 成功信封里没有方案列表，无法回答「有哪些方案」",
            subcommand=bridge.CMD_SCHEMES)
    return {"schemes": schemes}


# ---------------------------------------------------------------------------
# start_cleaning_task / start_clean_task
# ---------------------------------------------------------------------------

def _start(args, ctx, task_type, tool_name):
    """发起类能力的公共实现：两种任务类型只在 `--task-type` 上分叉。"""
    # 范围取分派层归一后的值（已补默认、已校验），这是本次真正发给执行侧的取值。
    scope = args.get("scope") or "full"
    options = {
        "scope": scope,
        "task-type": task_type,
        "exec": EXEC_MODE_CLUSTER,          # 只接受集群执行，不开放本机本地执行
    }
    if args.get("data_version"):
        # 交给 driver 判：与方案声明不一致时由它拒绝（本端不另判一遍）
        options["data-version"] = args["data_version"]
    if args.get("tag"):
        options["tag"] = args["tag"]
    options.update(_scheme_options(args))
    if args.get("force"):
        options["force"] = True

    result = bridge.run_driver(bridge.CMD_START, options, ctx)
    if not result.ok:
        err = result.error()
        code = err.get("code")
        if code == CODE_TASK_ALREADY_RUNNING:
            raise _busy_failure(err, force=bool(args.get("force")), supports_force=True)
        if code == CODE_VERSION_CONFLICT:
            raise _version_conflict_failure(err, args)
        # 其余（含「只清洗 + 非集群执行」这类 driver 直接拒绝的用法错误）一律如实透传
        raise bridge.structured_error(result)

    task_id = result.payload.get("task_id")
    # 真实生效的取向：先放归一后的请求值，再用执行侧状态文件回传的值覆盖
    # —— 用户没写数据版本时，真正用的是方案声明的那一版，只有执行侧知道。
    effective = {"scope": scope,
                 "data_version": args.get("data_version"),
                 "task_type": task_type,
                 "force": bool(args.get("force"))}
    effective.update(_effective_from_status(task_id, ctx))

    payload = _accepted(result.payload, task_type, effective, tool_name)
    # 受理成功之后才留凭据：没被受理的任务不该有「本服务提交」的标记。
    # 写失败只记日志，不影响这次的受理结论（见 write_submit_marker 的说明）。
    write_submit_marker(
        payload["task_id"], ctx, tool=tool_name,
        orientation={"task_type": task_type, "scope": effective.get("scope")})
    return payload


def handle_start_cleaning_task(args, ctx):
    """发起全流程：清洗 + 前后评分 + 发布。受理即返回，不等作业跑完。"""
    return _start(args, ctx, TASK_TYPE_FULL, "start_cleaning_task")


def handle_start_clean_task(args, ctx):
    """发起只清洗：不评分、不发布。

    driver 对「只清洗 + 非集群执行」会直接拒绝（该链路需要集群中间流）；本端固定
    按集群执行传参，若 driver 仍拒绝，说明是它那边的判定，原样透传不做改写。
    """
    return _start(args, ctx, TASK_TYPE_CLEAN, "start_clean_task")


# ---------------------------------------------------------------------------
# score_task
# ---------------------------------------------------------------------------

#: 评分来源取值（与 driver 的 `--source` 枚举一致）。
SOURCE_RAW = "raw"
SOURCE_PUBLISHED = "published"
SOURCE_TASK = "task"

#: 清洗产物的三份表文件名。**必须与 driver 的 `TABLE_FILES` 一致**
#: （`hadoop/engine/pipeline.py`）。这里只写文件名、不导入执行链，是因为本段不引用
#: driver/engine；两边是否漂移由测试逐项比对（`test_cleaned_file_names_match_driver`）。
CLEANED_TABLE_FILES = (
    ("users", "users.dat"),
    ("movies", "movies.dat"),
    ("ratings", "ratings.dat"),
)


def _read_json_file(path):
    """读一个 JSON 文件；读不到或内容不是合法 JSON 时返回 None（调用方按「不知道」处理）。"""
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return None


def check_task_dependency(task_id, ctx):
    """提交前同步判定「这个任务能不能被评分」；能就返回 None，不能就返回待抛的错误。

    三条依次看：任务在不在（状态文件）、它成功没有、清洗产物齐不齐。任何一条不满足
    都当场给出结论 —— 依赖不满足本来就该是请求当时的答复，而不是先受理、几秒后再
    报失败：那会让对端先对用户说「已提交」，再补一句「其实失败了」。

    **只读**：不创建目录、不写状态、不改任何产物；driver 那边的异步检查一律保留，
    这条只是把本可以当场回答的问题提前回答，不是替代它（两者同时成立时才可能出现
    竞态：检查通过后任务恰好被清理，此时仍由 driver 如实报错）。

    形状校验说明：任务编号的**形状校验统一在分派层做**（`dispatch._check_pathish_params`
    对 `task_id` / `from_task` 判"单层编号"）。这里再判一次是**防御性**的 —— 本函数会把
    任务编号拼进文件路径，万一将来有别的调用路径绕过分派层，也不能让它拼出任务根之外
    的路径；判据用的是契约层同一个 `valid_task_id`，不另立一套规则。
    """
    task_id = task_id or ""
    if not task_id:
        return StructuredError(
            CODE_DEPENDENCY_MISSING,
            "没有给出来源任务编号，无法评分；评分请求没有提交",
            source=SOURCE_TASK, missing="task",
            remedy="给出来源任务编号，或先发起一个清洗任务。")
    if not valid_task_id(task_id):
        return StructuredError(
            CODE_FORBIDDEN_PATH,
            "来源任务编号的形状不合法：%r。任务编号只能是单层编号，"
            "不接受分隔符、上级跳转或绝对路径；本次评分请求没有提交" % (task_id,),
            task_id=task_id or None, source=SOURCE_TASK,
            missing="task_id_shape",
            remedy="核对来源任务编号（例如用列任务的能力查现有编号）。")

    base = _task_dir(task_id, ctx)
    status_path = os.path.join(base, "status.json")

    if not os.path.isfile(status_path):
        return StructuredError(
            CODE_DEPENDENCY_MISSING,
            "目标任务不存在：%s；评分请求没有提交，请核对任务编号后再试"
            "（可以用列任务的能力查现有编号）" % (task_id or "（空）"),
            task_id=task_id or None, source=SOURCE_TASK,
            missing="task", remedy="核对来源任务编号，或先发起一个清洗任务。")

    status = _read_json_file(status_path)
    if not isinstance(status, dict):
        return StructuredError(
            CODE_DEPENDENCY_MISSING,
            "目标任务 %s 的状态文件存在但读不出来，无法确认它是否已成功；"
            "为避免把不确定的情况当成可以评分，这次请求没有提交" % task_id,
            task_id=task_id, source=SOURCE_TASK,
            missing="task_status_unreadable",
            remedy="人工确认该任务目录的状态文件是否损坏，或换一个来源任务。")

    current = status.get("status")
    if current != STATUS_SUCCEEDED:
        label = STATUS_LABELS.get(current, "")
        shown = ("%s（%s）" % (label, current)) if label else str(current)
        return StructuredError(
            CODE_DEPENDENCY_MISSING,
            "目标任务 %s 尚未成功（当前状态：%s），不能对它评分；评分请求没有提交，"
            "请等它跑到成功，或换一个已成功的来源任务" % (task_id, shown),
            task_id=task_id, source=SOURCE_TASK,
            missing="task_not_succeeded", status=current,
            remedy="等该任务到达成功状态后再评分；它失败的话先看它的失败阶段与作业日志。")

    version = status.get("data_version") or ""
    # 数据版本同样会被拼进路径（`cleaned/<版本>/…`）：它来自任务的状态文件而不是用户输入，
    # 但只要它不是单层名字（例如被手工改成了带跳转的字符串），拼接就会跑出任务目录。
    # 判据沿用同一个形状规则，不另立一套。
    if not valid_task_id(version):
        return StructuredError(
            CODE_DEPENDENCY_MISSING,
            "目标任务 %s 记录的数据版本不是合法名字（%r），无法安全定位它的清洗产物；"
            "本次评分请求没有提交" % (task_id, version),
            task_id=task_id, source=SOURCE_TASK,
            missing="data_version_shape", data_version=version,
            remedy="人工检查该任务的状态文件；或换一个任务作为来源。")
    cleaned_dir = os.path.join(base, "cleaned", version)
    missing_files = [filename for _table, filename in CLEANED_TABLE_FILES
                     if not os.path.isfile(os.path.join(cleaned_dir, filename))]
    if missing_files:
        return StructuredError(
            CODE_DEPENDENCY_MISSING,
            "目标任务 %s 的清洗产物不齐（缺 %s），不能对它评分；评分请求没有提交，"
            "请重新跑一次该任务，或换一个清洗产物齐全的来源任务"
            % (task_id, "、".join(missing_files)),
            task_id=task_id, source=SOURCE_TASK,
            missing="cleaned_products", missing_files=missing_files,
            relative_dir=os.path.join("cleaned", version),
            remedy="重跑该任务的清洗，或改用清洗产物齐全的来源任务。")
    return None


def handle_score_task(args, ctx):
    """独立评分：对原始数据、某个任务的清洗产物、或已发布数据评分，不发布数据。

    `source` 三选一隐式决定评分侧（driver 的既有口径，无单独的「之前/之后」参数）。

    三条来源里**只有「指定任务」在提交前做同步检查**（见下），因为它是本机文件读取，
    结论可靠；另外两条故意不做，理由写在下面那处注释里。
    """
    # 为什么只对「指定任务」提前判定，另外两条不做 —— **不是漏写**：
    #   * 指定任务：只看本机产物目录（状态文件 + 清洗三表），读得到就一定准，
    #     所以能当场给结论；让它晚一步报错纯属白白让用户多等一轮。
    #   * 已发布 / 原始数据：要问集群（发布区在分布式存储上、原始区也一样）。集群
    #     没起来时，「连不上集群」会被误报成「依赖缺失」，把排障方向直接带偏 ——
    #     那比晚一步说更坏。这两条交给 driver 在后台如实报告，它分得清两类原因。
    if args.get("source") == SOURCE_TASK:
        blocked = check_task_dependency(args.get("from_task") or "", ctx)
        if blocked is not None:
            raise blocked

    options = {"source": args.get("source")}
    if args.get("from_task"):
        options["from-task"] = args["from_task"]
    options.update(_scheme_options(args))
    if args.get("tag"):
        options["tag"] = args["tag"]
    # 不传 force：工具体里已经没有这个参数（执行侧也没有对应选项），分派层会对传 force
    # 的请求直接回 USAGE。driver 侧的依赖检查（以及它的异步失败上报）全部保留，作为兜底。

    result = bridge.run_driver(bridge.CMD_SCORE, options, ctx)
    if not result.ok:
        err = result.error()
        code = err.get("code")
        if code == CODE_TASK_ALREADY_RUNNING:
            raise _busy_failure(err, force=False, supports_force=False)
        if code == CODE_DEPENDENCY_MISSING:
            raise _dependency_failure(err, args)
        raise bridge.structured_error(result)

    source = args.get("source")
    payload = result.payload
    out = {}
    for key in ("task_id", "status", "started_at"):
        if payload.get(key) is not None:
            out[key] = payload[key]
    if not out.get("task_id"):
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 回了成功信封，但里面没有任务编号，无法跟进这次评分；请重试",
            subcommand=bridge.CMD_SCORE, source=source)

    # 取向以**本次真实请求**为准（分派层已按枚举校验），不照抄执行侧的回传值：
    # 回传值只是执行侧的复述，一旦两边不一致，照抄就会把「请求了已发布、返回里却写原始」
    # 这种错位当成事实包装出去。不一致时如实提示，让人去核对这次评分的归属。
    out["source"] = source
    out["task_type"] = TASK_TYPE_SCORE
    echoed = payload.get("source")
    if echoed is not None and echoed != source:
        out.setdefault("warnings", []).append(
            "执行侧回传的评分来源（%s）与本次请求（%s）不一致，请核对这次评分结果的归属"
            % (echoed, source))
    echoed_type = payload.get("task_type")
    if echoed_type is not None and echoed_type != TASK_TYPE_SCORE:
        out.setdefault("warnings", []).append(
            "执行侧回传的任务类型（%s）与本次请求（评分）不一致，请核对这次结果" % (echoed_type,))
    if args.get("from_task"):
        out["from_task"] = args["from_task"]

    estimate = _estimated_seconds("score_task")
    if estimate is not None:
        out["estimated_seconds"] = estimate
        out["estimated_seconds_note"] = ESTIMATE_NOTE

    # 受理成功之后留「经本服务提交」的凭据（评分任务同样占用执行位，也需要它）
    write_submit_marker(
        out["task_id"], ctx, tool="score_task",
        orientation={"task_type": TASK_TYPE_SCORE,
                     "source": source,
                     "from_task": args.get("from_task")})
    return out


# ---------------------------------------------------------------------------
# 处理器注册表（分派层按名字取）
# ---------------------------------------------------------------------------

HANDLERS = {
    "validate_config": handle_validate_config,
    "list_schemes": handle_list_schemes,
    "start_cleaning_task": handle_start_cleaning_task,
    "start_clean_task": handle_start_clean_task,
    "score_task": handle_score_task,
    "upload_scheme": handle_upload_scheme,
}
