# -*- coding: utf-8 -*-
"""mcpserver/readers.py —— 取证与产物读取的十个只读处理器。

对齐材料：
  * `docs/hadoop/迭代一重构_MCP_实现方案.md` §3.2（取证类能力）、§5（返回形状）、
    §6（错误语义：语义无损、码名沿用对端）、§10（只读资源）、§11（安全约束）；
  * `docs/agent/Agent_接口文档.md` §3.11.4 / §3.11.5（文件清单的 `kind` 取值、
    预览的既有错误码名称、路径白名单口径）；
  * `docs/hadoop/agent-interface.md` §4.4~§4.8 与 §5（`status` / `result` /
    `samples` / `report` / `tasks` 的真实字段，`status.json` 与 `metadata.json`
    的字段来源）。

分工（与 `dispatch.py` 的约定一致）：
  * 处理器签名统一 `handler(args, ctx) -> dict`；成功只回**业务字段**，信封由
    分派层包；失败抛 `contract.StructuredError`，绝不自己拼 `ok` 字段。
  * **借现有命令的五个一律经统一调用通道**：任务状态、任务结果、样本、报告、
    任务列表都走 `driver_bridge`，由它负责起进程、装环境、套超时、分失败类别 ——
    超时与传输层失败的处理全仓库只有那一处实现。driver 的结论（含
    `TASK_NOT_FOUND` / `TASK_NOT_FINISHED` / `TASK_FAILED` 这些自产码）原样上抛，
    一个都不改写、也不替它另算一份。
  * **自己读文件的只有取证五项**：作业计数、作业日志、产物清单、读产物、
    已发布版本。这些都是"看一眼文件"，不是执行通道的事，所以不经 `driver_bridge`。

对外形状的三条约定：
  * 产物位置只给"任务编号 + 相对路径"：任务目录内的绝对位置一律去掉或转成
    相对路径；分布式文件系统上的位置（发布区那一类）不是某台机器的本地位置，
    原样保留。
  * 版本与时间边界优先取任务目录里的元数据文件；集群任务不写这份元数据，
    于是允许从**成功任务**的结果文件里取同样的四类版本与时间边界 ——
    但一定要在返回里标出处（`versions_source`），让对端和用户都看得出数据从哪来。
    两处都没有时这些字段就不出现，绝不推算、绝不借用别的任务的数字。
  * 状态里补上任务类型、数据范围与标签（状态文件本来就有，只是既有命令没回），
    取不到就不给，不填默认值冒充。
  * 产物清单与读产物都把**不该对外的东西**挡在外面：服务控制文件（中止标记、
    提交标记）、以点开头的隐藏文件、校验和附属文件（`.xxx.raw.crc` 这类）。
    它们是文件系统层面的附属物，不是任务产物；规则集中在模块顶部那组常量里。
  * 取结果的返回里带**产物清单**（`artifacts`），与「列任务产物文件」共用同一份
    遍历与同一套排除规则，条数与顺序一致，对端「结果/文件」板块可直接渲染。
  * 取报告时对结构化报告多做一次可读性预检，但**只改变报错归类、不改变业务结论**：
    执行侧给出的结论一律不动，报告文件不在也不自己判定。

  * 查状态与取结果都带上**中文状态名**（`status_label` 与 `status_cn` 两个同值字段，
    原因见 `_status_decorations`）与**结束时间**；取不到就不给，不推算。
  * 对**已中止**的任务取结果单独归一类（`ABORTED`），不再报"任务未完成" ——
    否则对端会一直重试，等一个永远不会来的结果。
  * 报告与预览读取都有**大小上限**；超限如实报"内容过大"并给出实际大小，
    绝不截断一段内容冒充完整内容。
  * 打开文件之前先确认它是**普通文件**：管道、设备、目录一律拒绝。管道读起来
    不是报错而是**挂住**，会把处理线程永久卡死，必须提前挡住。
  * 错误说明与细节里**不带机器上的绝对路径**：要么改成相对路径，要么遮蔽成
    `<仓库根>` / `<产物目录>` / `<原始数据区>` / `<本机路径>`；子进程报错原文同样处理。
  * 不属于产物的东西（服务控制文件、隐藏文件、校验和附属物、流程中途残留）一律
    `FORBIDDEN_PATH`，并在错误细节里用 **`reason_kind`** 标明细类：
    `control` / `sidecar` / `residue`。同一个字段还用于"不是一份文件"的两种情形：
    `directory`（是目录）与 `not_regular_file`（管道、设备等，读它会挂住）。
    **整个服务只有这一个细类字段、这一套取值**，读取层与下载路由共用，对端归类一次
    即可、不必判断两个字段。
  * 原始区（`ML_RAW_DIR`）只读列举：**"没有原始区"与"原始区是空的"要分开说**，
    与发布区那条同理 —— 空白列表冒充"没有数据"会让人以为数据丢了。

三条纪律：
  1. **只读**：本模块不写文件、不建目录、不加锁，十个能力可并行调用；
  2. **不产生任何业务数字**：读不到就如实报错，绝不填空值、示例值或占位值；
  3. **路径白名单**：凡按路径读的能力，先解析真实路径再校验是否落在白名单根内，
     上级跳转、绝对路径、符号链接逃逸一律拒绝。
"""

import io
import json
import os
import re
import stat
import subprocess
import time

from . import driver_bridge as bridge
from . import tools as tool_table
from .contract import (
    CODE_ABORTED,
    CODE_CONTENT_TOO_LARGE,
    CODE_DEPENDENCY_MISSING,
    CODE_DRIVER_TIMEOUT,
    CODE_DRIVER_UNREACHABLE,
    CODE_FILE_NOT_FOUND,
    CODE_FORBIDDEN_PATH,
    CODE_PRODUCT_INVALID,
    CODE_TASK_FAILED,
    CODE_TASK_NOT_FOUND,
    CODE_USAGE,
    CONTROL_FILE_CANCEL,
    CONTROL_FILE_SUBMIT,
    STATUS_ABORTED,
    STATUS_FAILED,
    STATUS_LABELS,
    STATUS_SUCCEEDED,
    StructuredError,
)

# ---------------------------------------------------------------------------
# 本段用到的五个只读子命令（发起类命令由别的处理器负责登记）
# ---------------------------------------------------------------------------

CMD_STATUS = "status"
CMD_RESULT = "result"
CMD_SAMPLES = "samples"
CMD_REPORT = "report"
CMD_TASKS = "tasks"

#: 读发布区时的超时（秒）。集群未启动时通常几秒内就报连不上，不需要长等。
HDFS_TIMEOUT_SECONDS = 120

# ---------------------------------------------------------------------------
# 与工具表、driver 观测口径一致的常量（测试里逐项比对，防止两边漂移）
# ---------------------------------------------------------------------------

#: 清洗三表的落盘文件名。镜像 `engine.pipeline.TABLE_FILES`：这里写字面量是为了
#: 让本模块不依赖引擎，测试里会与引擎常量逐项比对。
TABLE_FILES = {"users": "users.dat", "movies": "movies.dat", "ratings": "ratings.dat"}

#: 表名展示顺序（维表先于评分表，与作业链一致）。
TABLE_ORDER = ("users", "movies", "ratings")

#: 各计数分组的中文说法（工具表与接口文档 §4.5 的五个键）。
COUNT_GROUPS = (
    ("input", "输入"),
    ("output", "输出"),
    ("quarantine", "隔离"),
    ("dedupe", "去重"),
    ("fix", "修复"),
)

#: 可预览的类型（§3.11.4）：结构化数据、Markdown、纯文本；其余一律"不可预览"。
PREVIEWABLE_KINDS = ("json", "md", "text")

#: 读产物文件的默认与硬上限（字节），与工具表 `read_task_file.max_bytes` 一致。
DEFAULT_READ_BYTES = 262144
MAX_READ_BYTES = 1048576

#: 报告的大小上限：报告本身是给人看的文本或结构化数据，超过这一档说明产物异常，
#: 或者该改用下载取走（`CONTENT_TOO_LARGE` 会提示这一点）。与单文件预览同一档。
REPORT_MAX_BYTES = MAX_READ_BYTES

#: 作业日志默认与最多回多少行，与工具表 `get_job_logs.lines` 一致。
DEFAULT_LOG_LINES = 100
MAX_LOG_LINES = 2000

#: 执行日志文件名（driver 后台进程的 stdout/stderr 落盘于此）。
DRIVER_LOG_NAME = "driver.log"
#: 各作业日志所在子目录。
JOB_LOG_DIRNAME = "logs"

# ---------------------------------------------------------------------------
# 不当作任务产物对外的东西（产物清单不列、按路径也读不到）
# ---------------------------------------------------------------------------
# 三类，**集中在这一处登记**，将来加规则只改这里：
#
#   1. 服务自己的控制文件（`CONTROL_FILE_NAMES`）——
#      一个是中止标记（契约层的 `CONTROL_FILE_CANCEL`），服务据它判断"该任务已被
#      要求停下"；一个是提交凭据（契约层的 `CONTROL_FILE_SUBMIT`），服务据它判断
#      "该任务的执行进程是否还在"。两者由服务侧写入、供服务侧判断用，既不是这次
#      任务算出来的数据，也不带任何对外含义；混进清单会让人以为是任务产物，
#      点开还会看到内部字段。
#
#   2. 以点开头的隐藏文件（`HIDDEN_FILE_PREFIX`）——
#   3. 校验和附属文件（`CHECKSUM_FILE_SUFFIXES`），形如 `.ratings.dat.raw.crc` ——
#   4. 流程中途的临时残留（`TEMP_RESIDUE_SUFFIXES`），形如 `logs/published_hashes.tmp` ——
#      发布环节"准备上传但尚未上传"的中间物。
#
#   第 2、3 类是**文件系统层面的附属物**：它们只说明文件是怎么被上传、取回、落盘的
#   （集群侧自带的完整性校验尾巴）；第 4 类是流程跑过留下的半成品。三者都跟
#   "这次任务算出了什么"没有关系。一次全量任务跑下来，这类东西能有十几个，
#   混在清单里只会让人以为任务产出了奇怪的东西。
#
# 判断一律按**文件名**（任务目录里任意层级都算）。
# **明确不排除**：`driver.log`、`logs/` 下各作业日志、`report.md`、`cleaned/` 与
# `quarantine/` 下的数据文件、`metrics/` 下的评分文件 —— 那些都是正当产物。
#
# 控制文件的名字**来自契约层**（`contract.CONTROL_FILE_NAMES`），这里只做转发：
# 改名时只改契约层那一处，写入方、判定方、排除方三边一起跟着变。**别在这里写字面量**
# —— 那样改名之后会出现"路径跟着变了、回报里还叫旧名字"的半截改动。
CONTROL_FILE_NAMES = (CONTROL_FILE_CANCEL, CONTROL_FILE_SUBMIT)
HIDDEN_FILE_PREFIX = "."
CHECKSUM_FILE_SUFFIXES = (".crc", ".crc64", ".chk", ".md5", ".sha1", ".sha256")
TEMP_RESIDUE_SUFFIXES = (".tmp", ".temp", ".part", ".partial")

#: 排除原因的三句话（给错误说明用，免得各处各写一套）。
REASON_CONTROL_FILE = "服务内部的控制文件"
REASON_FILESYSTEM_SIDECAR = "文件系统层面的附属物（隐藏文件或校验和附属文件）"
REASON_TEMP_RESIDUE = "流程中途的残留（临时文件），不是产物"

#: 拒绝原因的**细类**（写进错误细节的 `reason_kind` 字段）。
#:
#: 整个服务只有**一个**细类字段、一套取值 —— 读取层（读产物）与下载路由共用，
#: 对端据此归类一次即可，不必判断两个字段：
#:
#:   `control`          服务内部的控制文件（中止标记、提交凭据）
#:   `sidecar`          文件系统层面的附属物（隐藏文件、校验和附属文件）
#:   `residue`          流程中途的残留（临时文件、半成品）
#:   `directory`        这个路径是目录，不是可读/可下载的文件
#:   `not_regular_file` 不是普通文件（命名管道、设备、套接字）—— 读它会挂住
#:
#: 前三个对应"不是任务产物"，后两个对应"不是一份文件"。两者适用的错误码不同
#: （前者 `FORBIDDEN_PATH`，后者按各自能力的既有结论），但细类字段与取值共用一套。
EXCLUSION_KIND_CONTROL = "control"
EXCLUSION_KIND_SIDECAR = "sidecar"
EXCLUSION_KIND_RESIDUE = "residue"

#: 不是一份文件时的两种细类（与上面共用同一个字段）。
REASON_KIND_DIRECTORY = "directory"
REASON_KIND_NOT_REGULAR = "not_regular_file"

#: 全部细类取值（测试据此锁定"只有一套取值"）。
REASON_KINDS = (EXCLUSION_KIND_CONTROL, EXCLUSION_KIND_SIDECAR, EXCLUSION_KIND_RESIDUE,
                REASON_KIND_DIRECTORY, REASON_KIND_NOT_REGULAR)

#: 排除原因 → 细类。新增原因时这里同步登记（测试会断言覆盖完整）。
EXCLUSION_KINDS = {
    REASON_CONTROL_FILE: EXCLUSION_KIND_CONTROL,
    REASON_FILESYSTEM_SIDECAR: EXCLUSION_KIND_SIDECAR,
    REASON_TEMP_RESIDUE: EXCLUSION_KIND_RESIDUE,
}

#: 发布区在数据区下的相对位置（接口文档 §3.11.2：`/data/published/`）。
PUBLISHED_SUBDIR = "published"

# ---------------------------------------------------------------------------
# 产物类别（给人看的，按路径前缀判；判不出来就归"其它"）
# ---------------------------------------------------------------------------

CATEGORY_REPORT = "报告"
CATEGORY_DATA = "数据"
CATEGORY_QUARANTINE = "隔离"
CATEGORY_METRICS = "指标"
CATEGORY_LOG = "日志"
CATEGORY_METADATA = "元数据"
CATEGORY_OTHER = "其它"

#: 目录前缀 → 类别（只看路径的第一段）。
_ARTIFACT_DIR_CATEGORIES = (
    ("cleaned", CATEGORY_DATA),
    ("quarantine", CATEGORY_QUARANTINE),
    ("metrics", CATEGORY_METRICS),
    ("logs", CATEGORY_LOG),
)

#: 文件名 → 类别（只看文件名，用于根目录下的产物）。
#: 计数、统计、结果、状态这些**恰恰是用户最想看的**，单独归"元数据"——
#: 否则只看"数据/报告"的界面会把它们一起藏掉。
_ARTIFACT_NAME_CATEGORIES = {
    "report.md": CATEGORY_REPORT,
    "report.json": CATEGORY_REPORT,
    DRIVER_LOG_NAME: CATEGORY_LOG,
    "counts.json": CATEGORY_METADATA,
    "stats.json": CATEGORY_METADATA,
    "result.json": CATEGORY_METADATA,
    "status.json": CATEGORY_METADATA,
    "metadata.json": CATEGORY_METADATA,
    "published_hashes.json": CATEGORY_METADATA,
}

#: 常见产物的中文标题；没登记的一律用文件名本身，不臆造。
_ARTIFACT_TITLES = {
    "result.json": "运行结果",
    "metadata.json": "任务元数据",
    "counts.json": "数据量统计",
    "stats.json": "统计指标",
    "status.json": "任务状态",
    "published_hashes.json": "发布校验清单",
    "report.md": "评估报告",
    "report.json": "评估报告（结构化）",
    "quarantine_summary.json": "隔离汇总",
}

#: 指标目录下三份评分文件的标题。
_METRIC_TITLES = {
    "before.json": "清洗前评分",
    "after.json": "清洗后评分",
    "composite.json": "综合评分",
}

#: 版本与时间边界在 `metadata.json` 里的字段名（agent-interface.md §5）。
_VERSION_FIELDS = (
    ("data", "data_version"),
    ("rule", "rule_version"),
    ("scoring", "scoring_scheme_version"),
    ("policy", "policy_version"),
)
_HASH_FIELDS = (("rule", "rule_hash"), ("scoring", "scoring_hash"))
_TIME_FIELDS = ("T1", "T2")

#: 版本与时间边界的两个出处。集群任务不写元数据文件，于是允许从成功任务的结果
#: 文件里取同一批字段 —— 但返回里必须带 `versions_source`，谁都不许猜。
VERSIONS_SOURCE_METADATA = "metadata"
VERSIONS_SOURCE_RESULT = "result"
METADATA_NAME = "metadata.json"
RESULT_NAME = "result.json"
#: 结构化报告文件名（全文版是 `report.md`，由既有命令按文本读，不做可读性预检）。
REPORT_JSON_NAME = "report.json"
#: 全文报告文件名（只用于取报告前的**大小**检查，不用于读内容）。
REPORT_MD_NAME = "report.md"

#: 从状态文件补进来的三项（既有命令不回，但状态文件里真实存在）。
#: 从状态文件里补充给对端的附加字段。`algorithm` 是迭代二机器学习任务才有的：
#: 训练任务在状态里写了算法名，补出来之后"查状态"就能看到"在训朴素贝叶斯"，
#: 而不只是"在跑一个分类任务"。清洗任务没有这个键，取不到就不给（不填默认值）。
#: 从状态文件里补充给对端的附加字段。`algorithm` 是机器学习任务才有的；
#: `data_version` 让"查状态"就能看出用的是哪份数据（机器学习任务在取数后回写实际版本）。
_STATUS_EXTRA_FIELDS = ("task_type", "scope", "tag", "algorithm", "data_version")


# ---------------------------------------------------------------------------
# 目录与环境：一律经统一通道取，不各处自己算
# ---------------------------------------------------------------------------

def repo_root(ctx):
    """仓库根（由统一通道判定：上下文优先，否则按包位置推断）。"""
    return bridge.repo_root_of(ctx)


def var_dir(ctx):
    """driver 的产物根目录（`ML_VAR_DIR`；同样经统一通道解析，含环境脚本兜底）。

    与 driver 子进程实际拿到的取值来自同一处解析，因此不会出现"本端查 A 目录、
    driver 写 B 目录"的错位。
    """
    return bridge.env_value("ML_VAR_DIR", ctx) or os.path.join(repo_root(ctx), "var")


def tasks_root(ctx):
    """任务产物根目录（`<ML_VAR_DIR>/tasks`）。"""
    return os.path.join(var_dir(ctx), "tasks")


def config_root(ctx):
    """方案目录（仓库 `config/`）。"""
    return os.path.join(repo_root(ctx), "config")


def hdfs_base(ctx):
    """数据区根（`HDFS_BASE`，缺省 `/data`）。"""
    return bridge.env_value("HDFS_BASE", ctx) or "/data"


def published_dir(ctx):
    """发布区目录（`<HDFS_BASE>/published`）。"""
    return "%s/%s" % (hdfs_base(ctx).rstrip("/"), PUBLISHED_SUBDIR)


def allowed_roots(ctx):
    """读取类能力的白名单根（§11.2）：任务产物目录与方案目录。

    原始区与发布区在 HDFS 上，由 `list_published_versions` 单独走集群读取，
    不参与本地路径解析；原始区在本机的那份文件由 `list_raw_datasets` 只读列举。
    """
    return [tasks_root(ctx), config_root(ctx)]


def raw_dir(ctx):
    """原始数据区（`ML_RAW_DIR`，经统一通道解析；与执行侧看到的取值同源）。

    解析不出来时返回空串 —— 调用方据此把"没有配置原始区"与"目录不存在"分开说。
    """
    return bridge.env_value("ML_RAW_DIR", ctx) or ""


# ---------------------------------------------------------------------------
# 参数默认值：一律从工具表取，别在处理器里另写一份
# ---------------------------------------------------------------------------

def param_default(tool_name, param_name):
    """工具表里这个参数的默认值；取不到返回 None。

    为什么从工具表取而不在这里写死：默认值是**对外的**约定（对端从"列工具"看到
    的就是它）。处理器里再抄一份，两份迟早会分叉 —— 同一句话经本服务调用与直接
    调执行侧就会得到不同结果。
    """
    tool = tool_table.get_tool(tool_name) or {}
    for param in tool.get("params") or ():
        if param.get("name") == param_name:
            return param.get("default")
    return None


def arg_or_default(tool_name, args, param_name):
    """取参数值；没给就用工具表里的默认值（保持"总是显式传给执行侧"）。"""
    value = (args or {}).get(param_name)
    if value is None or (isinstance(value, str) and not value.strip()):
        return param_default(tool_name, param_name)
    return value


# ---------------------------------------------------------------------------
# 文本遮蔽：错误说明里不带机器上的绝对路径
# ---------------------------------------------------------------------------

#: 本机常见目录前缀。命中就整段遮蔽 —— 除了下面这些"已知根"之外，剩下能出现在
#: 报错里的绝对路径基本都出自这些目录（子进程报错原文最常夹的就是它们）。
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w<>:/])/(?:home|root|Users|tmp|opt|srv|mnt|media)/[^\s'\"，。；、)）]*")


def sanitize_text(text, ctx=None):
    """把文本里的机器绝对路径换掉：已知根换成带名字的占位，其余本机路径整段遮蔽。

    为什么要做：错误说明会直接展示给用户与对端。带出 `/home/某人/...` 既没用
    （对端不在这台机器上），又把机器上的目录结构漏了出去。**分布式文件系统上的
    路径不遮蔽** —— 那不是某台机器的本地位置，对端需要它。
    """
    if not isinstance(text, str) or not text:
        return text
    out = text
    known = []
    try:
        known = [(raw_dir(ctx), "<原始数据区>"),
                 (var_dir(ctx), "<产物目录>"),
                 (config_root(ctx), "<方案目录>"),
                 (repo_root(ctx), "<仓库根>")]
    except Exception:                      # noqa: BLE001 —— 遮蔽失败不该让能力整体失败
        known = []
    for needle, label in sorted(known, key=lambda item: -len(item[0] or "")):
        if needle:
            out = out.replace(needle.rstrip("/"), label)
            out = out.replace(needle, label)
    return _LOCAL_PATH_RE.sub("<本机路径>", out)


def sanitized_error(exc, ctx=None):
    """把一个结构化错误整体遮蔽一遍（说明与全部字符串细节）。"""
    extra = {}
    for key, value in (exc.extra or {}).items():
        extra[key] = sanitize_text(value, ctx) if isinstance(value, str) else value
    return StructuredError(exc.code, sanitize_text(exc.message, ctx), **extra)


# ---------------------------------------------------------------------------
# 只读普通文件：管道与设备不许 open
# ---------------------------------------------------------------------------

#: 非常规文件的类型说法（给错误说明用）。
_FILE_TYPE_LABELS = (
    (stat.S_ISFIFO, "命名管道"),
    (stat.S_ISCHR, "字符设备"),
    (stat.S_ISBLK, "块设备"),
    (stat.S_ISSOCK, "套接字"),
    (stat.S_ISDIR, "目录"),
)


def _file_type_label(mode):
    """非常规文件的中文类型名；认不出就说"非常规文件"。"""
    for checker, label in _FILE_TYPE_LABELS:
        if checker(mode):
            return label
    return "非常规文件"


def stat_regular_file(path, display):
    """确认是普通文件并返回它的 stat；否则抛结构化错误。

    **为什么必须先判**：对命名管道 `open` 之后 `read` 会一直等写端，表现不是报错
    而是**永久挂住** —— 一个请求就能把处理线程卡死，后面所有请求一起排队。
    设备文件同理（读到无穷无尽的数据）。所以凡是要打开文件的地方，都先过这一关。
    """
    try:
        info = os.stat(path)
    except OSError as exc:
        raise StructuredError(
            CODE_FILE_NOT_FOUND,
            "读取 %s 失败：%s" % (display, getattr(exc, "strerror", None) or exc),
            path=display)
    if not stat.S_ISREG(info.st_mode):
        raise StructuredError(
            CODE_PRODUCT_INVALID,
            "%s 是%s，不是普通文件，不能作为产物读取" % (display, _file_type_label(info.st_mode)),
            path=display, reason_kind=REASON_KIND_NOT_REGULAR)
    return info


# ---------------------------------------------------------------------------
# 路径白名单（这一段的红线）
# ---------------------------------------------------------------------------

def _is_absolute_path(text):
    """绝对路径判定：Unix 绝对路径、反斜杠开头、Windows 盘符都算。"""
    text = str(text)
    if os.path.isabs(text):
        return True
    if text.startswith("\\") or text.startswith("/"):
        return True
    if len(text) >= 2 and text[1] == ":":
        return True
    return False


def _is_within(real_path, real_root):
    """真实路径是否落在根内（等于根本身或在根之下）。"""
    if real_path == real_root:
        return True
    return real_path.startswith(real_root + os.sep)


def resolve_under(root, relpath, must_exist=True):
    """把相对路径解析到 `root` 之下，并校验真实路径没有逃逸。

    拒绝：绝对路径、上级跳转（`..`）、指向根外的符号链接 —— 一律 `FORBIDDEN_PATH`。
    `must_exist=True` 时，解析完成后文件不存在报 `FILE_NOT_FOUND`；传 `False` 则只做
    路径形状校验，把"在不在"留给调用方去判（顺序上要先把越界与"文件不在"分开的，
    都靠这个开关）。
    """
    if relpath is None or str(relpath).strip() == "":
        raise StructuredError(CODE_USAGE, "缺少参数 path（产物文件的相对路径）", param="path")
    text = str(relpath).strip()
    if "\x00" in text:
        raise StructuredError(CODE_FORBIDDEN_PATH, "路径含非法字符：%r" % relpath, path=str(relpath))
    if _is_absolute_path(text):
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "不允许绝对路径，请给出相对路径：%s" % text, path=text)
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, text.replace("\\", "/")))
    if not _is_within(target, root_real):
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "路径越界（不在白名单内）：%s" % text, path=text)
    if must_exist and not os.path.exists(target):
        raise StructuredError(CODE_FILE_NOT_FOUND, "文件不存在：%s" % text, path=text)
    return target


def resolve_allowed_path(ctx, relpath, must_exist=True):
    """在**白名单根**（任务产物目录、方案目录）之内解析一个相对路径。

    供资源类读取复用：`config/xx.json` 相对仓库根解析，`tasks/<任务>/xx` 相对
    产物根（`ML_VAR_DIR`）解析 —— 两个基准都试，只有落在白名单根之内才算数。
    越界一律 `FORBIDDEN_PATH`，根内但不存在的报 `FILE_NOT_FOUND`，
    绝不把文件系统上的任意位置暴露出去。
    """
    if relpath is None or str(relpath).strip() == "":
        raise StructuredError(CODE_USAGE, "缺少参数 path", param="path")
    text = str(relpath).strip()
    if _is_absolute_path(text) or "\x00" in text:
        raise StructuredError(CODE_FORBIDDEN_PATH, "不允许绝对路径：%s" % text, path=text)
    cleaned = text.replace("\\", "/")
    roots = [os.path.realpath(root) for root in allowed_roots(ctx)]
    candidates = []
    for base in (repo_root(ctx), var_dir(ctx)):
        probe = os.path.realpath(os.path.join(os.path.realpath(base), cleaned))
        if any(_is_within(probe, root) for root in roots):
            candidates.append(probe)
    if not candidates:
        raise StructuredError(CODE_FORBIDDEN_PATH, "路径越界（不在白名单内）：%s" % text, path=text)
    for probe in candidates:
        if os.path.exists(probe):
            return probe
    if must_exist:
        raise StructuredError(CODE_FILE_NOT_FOUND, "文件不存在：%s" % text, path=text)
    return candidates[0]


def task_dir(ctx, task_id, must_exist=True):
    """任务产物目录（白名单校验：必须是单层任务编号，且真实路径在任务根之下）。

    **主闸门在分派层**：`dispatch._check_pathish_params` 已对 `task_id` / `from_task`
    统一做过形状校验（挡住分隔符、上级跳转、绝对路径、空字节与非合法形状），
    二十几项能力一个不漏。这里保留一套同样的判断是**防御性再判**：本模块的函数
    也可能被资源读取、下载路由单独调用，不经过分派层。**不要在这里另立新规则**
    （比如额外限制长度或字符集）—— 规则只应有一份，两处不一致会变成"分派层放行、
    读取层拒绝"这种自相矛盾。
    """
    text = str(task_id if task_id is not None else "").strip()
    if not text:
        raise StructuredError(CODE_USAGE, "缺少任务编号", param="task_id")
    if (_is_absolute_path(text) or "/" in text or "\\" in text
            or text in (".", "..") or "\x00" in text):
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "任务编号只能是单层目录名，不能带路径分隔符或上级跳转：%r" % task_id,
            task_id=str(task_id))
    root_real = os.path.realpath(tasks_root(ctx))
    target = os.path.realpath(os.path.join(root_real, text))
    if not _is_within(target, root_real):
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "任务目录越界（不在任务产物根之内）：%s" % text, task_id=text)
    if not os.path.isdir(target):
        if must_exist:
            raise StructuredError(CODE_TASK_NOT_FOUND, "任务不存在：%s" % text, task_id=text)
        return None
    return target


def _optional_task_dir(ctx, task_id):
    """尽力取任务目录：编号非法或目录不存在都返回 None（用于加性增强，不改变主结论）。"""
    try:
        return task_dir(ctx, task_id, must_exist=True)
    except StructuredError:
        return None


# ---------------------------------------------------------------------------
# 文件类型、小工具
# ---------------------------------------------------------------------------

#: 扩展名 → kind（§3.11.4 的五个取值）。与对端 `agent/resources.kind_of` 同口径。
_KIND_BY_EXT = {
    ".json": "json",
    ".md": "md",
    ".dat": "dat",
    ".txt": "text",
    ".log": "text",
    ".csv": "text",
    ".tsv": "text",
    ".jsonl": "text",
    ".yaml": "text",
    ".yml": "text",
    ".ini": "text",
    ".cfg": "text",
    ".conf": "text",
}


def kind_of(name):
    """按扩展名粗分文件类型：json / md / dat / text / other。"""
    ext = os.path.splitext(str(name))[1].lower()
    return _KIND_BY_EXT.get(ext, "other")


def exclusion_reason(path_or_name):
    """该文件是否**不当作任务产物对外**；返回原因（给说明用），不排除则返回 None。

    三类规则全部取自本模块顶部那组常量：服务控制文件、以点开头的隐藏文件、
    校验和附属文件。按文件名判断，任务目录里任意层级、以及经由符号链接指向的
    这些文件都算 —— 加新规则只改常量那一处。
    """
    if not path_or_name:
        return None
    text = str(path_or_name).replace("\\", "/").rstrip("/")
    name = text.rsplit("/", 1)[-1]
    if name in CONTROL_FILE_NAMES:
        return REASON_CONTROL_FILE
    if name.startswith(HIDDEN_FILE_PREFIX):
        return REASON_FILESYSTEM_SIDECAR
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in CHECKSUM_FILE_SUFFIXES):
        return REASON_FILESYSTEM_SIDECAR
    if any(lowered.endswith(suffix) for suffix in TEMP_RESIDUE_SUFFIXES):
        return REASON_TEMP_RESIDUE
    return None


def is_control_file(path_or_name):
    """该文件是不是**服务自己的控制文件**（对外不存在）。"""
    return exclusion_reason(path_or_name) == REASON_CONTROL_FILE


def category_of(relpath):
    """产物的类别（给人看的）：报告 / 数据 / 隔离 / 指标 / 日志 / 元数据 / 其它。

    先看路径第一段目录，再看文件名，最后归"其它"——判不出来就如实归"其它"，
    不猜。
    """
    text = str(relpath or "").replace("\\", "/")
    head = text.split("/", 1)[0]
    for prefix, category in _ARTIFACT_DIR_CATEGORIES:
        if head == prefix:
            return category
    return _ARTIFACT_NAME_CATEGORIES.get(text.rsplit("/", 1)[-1], CATEGORY_OTHER)


def title_of(relpath):
    """产物的中文标题；没登记的一律用文件名本身，不臆造。"""
    text = str(relpath or "").replace("\\", "/")
    name = text.rsplit("/", 1)[-1]
    if name in _ARTIFACT_TITLES:
        return _ARTIFACT_TITLES[name]
    if text.split("/", 1)[0] == "metrics" and name in _METRIC_TITLES:
        return _METRIC_TITLES[name]
    return name


def _relpath(base, path):
    """相对路径统一用 `/` 分隔（对外只给相对位置，不给机器上的绝对位置）。"""
    return os.path.relpath(path, base).replace(os.sep, "/")


def _read_json_file(path, display=None):
    """读一个 JSON 产物；文件在但内容读不出来/不合法 → `PRODUCT_INVALID`。

    与"文件不存在"分开：前者是产物坏了（要按坏产物修），后者是没有这份产物
    （要按缺产物补）。错误说明里只用调用方给的对外名字（`display`），
    不把机器上的绝对位置漏出去；打开之前先确认是普通文件（管道会挂住）。
    """
    label = display or os.path.basename(path)
    stat_regular_file(path, label)
    try:
        with io.open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError) as exc:
        raise StructuredError(
            CODE_FILE_NOT_FOUND,
            "读取 %s 失败：%s" % (label, getattr(exc, "strerror", None) or exc), path=label)
    except ValueError as exc:
        raise StructuredError(
            CODE_PRODUCT_INVALID,
            "%s 的内容不是合法的 JSON，读不出来：%s；这份产物需要重新生成" % (label, exc),
            path=label)


def _tail_lines(path, want):
    """从文件尾部取最后 `want` 行；返回 (行列表, 是否只回了尾部片段, 文件大小)。

    由尾部往前按块读取，**不把整个文件读进内存** —— 作业日志可能是几十兆，
    而这里只需要最后若干行（§11.6：日志只按需回尾部片段）。

    先确认是普通文件：日志位置上若出现命名管道，`open` 之后会一直等写端，
    这个请求就永远回不去了。
    """
    info = stat_regular_file(path, display=os.path.basename(path))
    size = info.st_size
    chunk = 8192
    want = max(1, int(want))
    lines = []
    start = 0
    while True:
        start = max(0, size - chunk)
        with io.open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        if start > 0 and lines:
            lines = lines[1:]          # 首行可能被块边界截断，整行丢掉
        if start == 0 or len(lines) >= want:
            break
        chunk *= 4
    # 只要还有更早的内容没回，就标成"只给了尾部片段"：既包括没读到文件开头，
    # 也包括读到了开头但行数比要求的多
    return lines[-want:], (start > 0 or len(lines) > want), size


# ---------------------------------------------------------------------------
# 读集群发布区（不是执行通道的事，所以不经统一调用通道）
# ---------------------------------------------------------------------------

def hdfs_command(ctx, argv):
    """拼出**一次集群命令**的调用方式：返回 `(command, env, cwd)`。

    **这是全服务唯一的实现**：读取层按普通请求/响应调用它，服务层的下载需要自己
    流式读输出，也调用它取同一份命令与环境。此前下载那边自己复制了一份（连固定英文
    报错都抄了），两条路会各自漂移 —— 拿不同的 `PATH`、不同的默认值，排障时分不清
    是集群的问题还是我们带错了环境。

    命令形态是 `bash -c 'source <env.sh>; exec "$@"'`：脚本路径与命令、参数都作为
    **位置参数**传入，不拼进 shell 字符串。

    固定英文报错那两行不是多余的设置：这一层要**靠报错文案**区分两种完全不同的局面
    ——「发布区目录还没建」（等于还没发布过，可以回空列表）与「连不上集群」（必须报错，
    绝不能拿空列表冒充"没有已发布数据"）。集群命令的报错文案随系统语言变化，不锁语言
    的话同一句判断会在换环境后失效，把"集群未启动"误判成"没有已发布数据"。
    """
    root = repo_root(ctx)
    script = os.path.join(root, "hadoop", "scripts", "env.sh")
    if not os.path.isfile(script):
        raise StructuredError(
            CODE_DRIVER_UNREACHABLE,
            "找不到集群环境脚本（仓库内 hadoop/scripts/env.sh），无法读取发布区",
            script="hadoop/scripts/env.sh")
    env = bridge.build_env(ctx)
    env["LC_ALL"] = "C"
    env.pop("LANGUAGE", None)
    command = ["bash", "-c", 'source "$0" >/dev/null 2>&1; exec "$@"',
               script, "hdfs"] + [str(item) for item in argv]
    return command, env, root


def run_hdfs(argv, ctx, timeout=None):
    """调用集群命令（参数列表传入，路径不拼进 shell 字符串）。

    环境沿用统一通道的解析结果（进程环境 < 上下文 < 环境脚本兜底），与 driver
    子进程看到的取值同源；`PATH` 由环境脚本在被 source 时补上，所以这里是
    `bash -c 'source <脚本>; exec "$@"'` 的形态 —— 命令与路径作为位置参数传入，
    不会被拼进 shell 字符串。
    """
    command, env, root = hdfs_command(ctx, argv)
    try:
        proc = subprocess.Popen(command, cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise StructuredError(
            CODE_DRIVER_UNREACHABLE,
            "无法启动集群命令：%s" % (getattr(exc, "strerror", None) or type(exc).__name__))
    try:
        out, err = proc.communicate(timeout=timeout or HDFS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise StructuredError(
            CODE_DRIVER_TIMEOUT,
            "集群命令在 %s 秒内没有返回" % (timeout or HDFS_TIMEOUT_SECONDS),
            command=" ".join(argv))
    return (proc.returncode,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# 1~5 借现有命令的五个能力（一律经统一调用通道）
# ---------------------------------------------------------------------------

def _call(subcommand, options, ctx):
    """经统一调用通道跑一个只读子命令，返回业务字段。

    失败形态由 `driver_bridge` 负责分类（超时 / 无输出 / 输出不是信封 / 起不来），
    失败信封里的结构化码与细节字段由 `require_ok` 原样转成异常 —— 我这里既不换算
    错误码，也不丢 `stage` / `job` / `exit_code` / `details` 这些定位信息。
    """
    try:
        return bridge.require_ok(bridge.run_driver(subcommand, options, ctx))
    except StructuredError as exc:
        # 通道层把子进程报错原文拼进了说明，原文里常带本机路径：统一遮蔽后再上抛
        raise sanitized_error(exc, ctx)


def get_task_status(args, ctx=None):
    """查任务状态：`status` 的信封字段回传，另补两处真实信息。

    补的第一处是**任务类型、数据范围与标签**：状态文件里本来就存着，既有命令
    只是没回。补上之后对端才能区分全流程、只清洗、独立评分，以及是不是小样本。
    取不到就不给，不填默认值冒充。

    补的第二处是**版本与时间边界**：优先取任务目录里的元数据文件；集群任务不写
    这份元数据，于是允许从成功任务的结果文件里取同一批字段，并在 `versions_source`
    里标出处。两处都没有时这些字段就不出现。

    补的第三处是**中文状态名与结束时间**（见 `_status_decorations`）：对端的任务
    视图要显示"成功 / 失败 / 已中止"，不该让它自己维护一张对照表。
    """
    task_id = args.get("task_id")
    payload = _call(CMD_STATUS, {"task-id": task_id}, ctx)
    for key, value in _status_extra_fields(ctx, task_id).items():
        payload.setdefault(key, value)
    payload.update(_task_versions(ctx, task_id, payload.get("status")))
    payload.update(_status_decorations(ctx, task_id, payload.get("status")))
    return payload


def _status_decorations(ctx, task_id, status):
    """把状态取值补成**给人看的中文名**，并带上结束时间。

    给两个同值字段 `status_label` 与 `status_cn` 是有意的：
      * `status_cn` 是对端任务视图**已有的**键名（接口文档 §3.8 的任务视图里就叫
        这个名字），对端不用改代码就能直接用；
      * `status_label` 是本端自己的命名习惯，将来若对端换名字，我们这边不用跟着动。
    两个字段值一定相同，写在一处生成，不会出现两个名字两种说法。

    结束时间只从状态文件里取 `finished_at`；没有这个字段就不给 —— 拿"更新时间"
    顶替会让"结束于何时"变成一句猜测。
    """
    out = {}
    if isinstance(status, str) and status:
        label = STATUS_LABELS.get(status)
        if label:
            out["status_label"] = label
            out["status_cn"] = label
    status_file = _read_status_file(ctx, task_id)
    finished_at = status_file.get("finished_at")
    if isinstance(finished_at, str) and finished_at.strip():
        out["finished_at"] = finished_at.strip()
    return out


#: 已中止任务被问到"要结果 / 要报告 / 要样本"时，给用户的去向说明。
#: 按能力各说一句：这三种请求的下一步并不相同（结果得重跑，报告与样本还能先看
#: 已经落下的产物），一句笼统的"请重试"帮不上忙。
_ABORTED_ADVICE = {
    "结果": "如需这次运行得到的产出，请重新发起一次任务；该任务已经产出的文件"
            "可用「列任务产物文件」或下载取用。",
    "报告": "如需报告，请重新发起一次任务；该任务已经落下的产物可用"
            "「列任务产物文件」或下载取用。",
    "样本": "如需样本，请重新发起一次任务；该任务已经产出的清洗或隔离数据可用"
            "「列任务产物文件」或下载取用。",
}


def _reject_if_aborted(ctx, task_id, what="结果"):
    """任务已中止时，取结果 / 取报告 / 取样例都归成**同一类**错误，而不是"任务未完成"。

    为什么必须分开：`TASK_NOT_FINISHED` 的语义是"还没到终态，等等再来"，对端会按
    它的意思反复重试；可已中止是终态，再等一万次也不会出现可取的东西。三项能力
    若各说各话（取结果说已中止、取报告却说未完成），用户与对端就会以为"再等等
    报告就有了"，实际上永远不会有。

    边界（写在这里，别扩大）：
      * **只改"已中止"这一种终态的说法**：任务未完成、失败、不存在、成功这些
        结论一个都不动，仍然由执行侧原样给出；
      * 不看产物在不在、不判断内容好坏 —— 那是各自能力的职责，这里只回答
        "这个任务还在不在往前的路上"；
      * `what` 只影响说明里"要的是什么、接下来该做什么"，不影响错误码与其余字段。
    """
    status_file = _read_status_file(ctx, task_id)
    if status_file.get("status") != STATUS_ABORTED:
        return
    detail = {
        "task_id": str(task_id),
        "status": STATUS_ABORTED,
        "status_label": STATUS_LABELS.get(STATUS_ABORTED, STATUS_ABORTED),
        "status_cn": STATUS_LABELS.get(STATUS_ABORTED, STATUS_ABORTED),
        "requested": what,
    }
    for key in ("finished_at", "aborted_at"):
        value = status_file.get(key)
        if isinstance(value, str) and value.strip():
            detail[key] = value.strip()
    advice = _ABORTED_ADVICE.get(what, _ABORTED_ADVICE["结果"])
    raise StructuredError(
        CODE_ABORTED,
        "任务 %s 已中止：它不会再变化，也不会再有%s可取（已中止属于终态，"
        "重试不会等到它变成别的状态）。%s" % (task_id, what, advice),
        **detail)


#: 失败记录里可能承载结构化错误码的字段名（执行侧写入）。
#: 按"记录里有码就用它"处理：先看这些名字，不把结论硬绑在某一个键上 ——
#: 执行侧把字段改名，这边照样能取到；也允许将来同时出现多个名字（取第一个非空）。
_FAILURE_CODE_KEYS = ("code", "error_code", "errorCode", "failure_code", "reason_code")

#: `code_source` 的取值：说明这个分类是从任务记录里取来的，便于排障时对照。
CODE_SOURCE_TASK_RECORD = "task_record"


def _recorded_failure_code(ctx, task_id):
    """从任务记录里取失败原因的结构化错误码；取不到返回 None。

    读的是任务目录里状态文件的失败记录（`errors` 数组的第一项优先，其次 `error`
    字段，最后退回状态文件本身）。**只取不判**：码是不是我们认识的、该归哪一类，
    由调用方决定 —— 这里只回答"记录里写的是什么"。
    """
    status_file = _read_status_file(ctx, task_id)
    if status_file.get("status") != STATUS_FAILED:
        return None
    candidates = []
    errors = status_file.get("errors")
    if isinstance(errors, list):
        candidates.extend(item for item in errors if isinstance(item, dict))
    if isinstance(status_file.get("error"), dict):
        candidates.append(status_file["error"])
    candidates.append(status_file)
    for record in candidates:
        for key in _FAILURE_CODE_KEYS:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _refine_failure_code(exc, ctx, task_id):
    """任务失败时，优先采用任务记录里带的结构化错误码。

    为什么值得单独做：发布环节被版本保护拦下时，执行侧给出的原话是"任务失败"。
    对端拿到笼统的"任务失败"会把用户引去查作业日志，而真实原因是版本冲突 ——
    那是一条完全不同方向的排查路径。

    边界（都写在这里，别扩大）：
      * **只改分类**：说明文字、失败阶段、作业名、退出码全部原样带出（原话不改写）；
      * 只有**已经判定为任务失败**时才看记录，其它结论（未完成 / 不存在 / 已中止）
        一概不动；
      * 记录里没有码 → 维持"任务失败"，不猜;
      * 记录里的码即使不是我们契约里的名字，也**原样带出**（语义无损优先），
        不做映射、不丢弃；
      * 加 `code_source` 与 `recorded_code` 两个字段标明来源，排障时能看清这个
        分类是"谁是这么说的"。
    """
    if exc.code != CODE_TASK_FAILED:
        return exc
    recorded = _recorded_failure_code(ctx, task_id)
    if not recorded:
        return exc
    extra = dict(exc.extra or {})
    extra["code_source"] = CODE_SOURCE_TASK_RECORD
    extra["recorded_code"] = recorded
    return StructuredError(recorded, exc.message, **extra)


def _read_status_file(ctx, task_id):
    """读任务目录里的状态文件；读不到或读不出来就返回空字典（不改变主结论）。

    主结论（状态、阶段、进度）来自 driver，这里只是补字段：状态文件坏了不该让
    "查状态"整体失败。
    """
    base = _optional_task_dir(ctx, task_id)
    if base is None:
        return {}
    path = os.path.join(base, "status.json")
    if not os.path.isfile(path):
        return {}
    try:
        status = _read_json_file(path, display="status.json")
    except StructuredError:
        return {}
    return status if isinstance(status, dict) else {}


def _status_extra_fields(ctx, task_id):
    """从状态文件补任务类型、数据范围与标签。

    只取这三项：状态文件里还有方案文件的绝对位置（`rules` / `scoring`），
    按"产物位置只给任务编号加相对路径"的约定不外传。
    """
    status = _read_status_file(ctx, task_id)
    extra = {}
    for key in _STATUS_EXTRA_FIELDS:
        value = status.get(key)
        if isinstance(value, str) and value.strip():
            extra[key] = value.strip()
    return extra


def get_task_result(args, ctx=None):
    """取任务结果：`result` 的信封字段回传，产物位置一律转成相对路径。

    未成功、失败、不存在的判定全在 driver 里 —— 它的 `TASK_NOT_FINISHED` /
    `TASK_FAILED` / `TASK_NOT_FOUND` 原样上抛，本模块不另算一份。
    **唯一的例外是"已中止"**：那是终态，driver 只会说"任务未完成"，而那个说法
    的语义是"还没到终态，等等再来"，与事实对不上 —— 所以先按状态文件判一次
    （见 `_reject_if_aborted`），其余结论一律不动。
    """
    task_id = args.get("task_id")
    _reject_if_aborted(ctx, task_id)
    try:
        payload = _call(CMD_RESULT, {"task-id": task_id}, ctx)
    except StructuredError as exc:
        # 失败任务的分类以任务记录里的结构化码为准（见 `_refine_failure_code`）
        raise _refine_failure_code(exc, ctx, task_id)
    payload = _relativize_paths(payload, task_id, ctx)
    # 产物清单的优先级（踩过坑，写清）：**结果文件自己带了清单就以它为准**。
    # 机器学习任务的产物是"模型本体/群体画像/每用户标签/用户坐标/元数据"，
    # 带 `role`（用途）与 `kind`（格式），且路径相对模型目录；若在这里无条件用
    # 任务目录的文件清单覆盖，对端就只能看到"日志、状态、结果"三个内部文件，
    # 真正的模型产物反而消失了（实测发生）。清洗任务的结果里没有这个字段，
    # 于是照旧用任务目录清单（那是它们的产物来源）。
    if not payload.get("artifacts"):
        payload["artifacts"] = _task_artifacts(ctx, task_id)
    payload.update(_status_decorations(ctx, task_id, payload.get("status")))
    return payload


def _task_artifacts(ctx, task_id):
    """该任务的产物清单（供对端「结果/文件」板块直接渲染）。

    内容**完全来自任务目录的真实文件**：与「列任务产物文件」共用同一份遍历逻辑与
    同一套排除规则（控制文件、隐藏文件、校验和附属文件都不进来），排序也一致
    （按相对路径字典序），所以两处的条数与顺序永远对得上。

    每条给：编号、标题、类别、类型、相对路径、大小。类别按路径前缀判，
    判不出来归"其它"；标题没登记过的就用文件名本身 —— 不写死内容、不猜。
    失败或"只清洗"这类没有相应产物的任务，能列多少列多少，不补空壳项。
    """
    base = _optional_task_dir(ctx, task_id)
    if base is None:
        return []
    artifacts = []
    for item in _task_files(base):
        rel = item["path"]
        artifacts.append({
            "id": "task:%s:%s" % (task_id, rel),
            "title": title_of(rel),
            "category": category_of(rel),
            "kind": item["kind"],
            "path": rel,
            "size": item["size"],
        })
    return artifacts


def _relativize_paths(payload, task_id, ctx):
    """把结果里的产物位置改成"任务编号 + 相对任务目录的路径"。

    规则：
      * 任务目录那个绝对位置字段（`task_dir`）直接去掉 —— 外面的调用方不需要
        知道服务跑在哪台机器、哪个目录；
      * 落在任务目录内的路径转成相对任务目录的路径（如 `cleaned/ml1m-clean-v1`）；
      * 分布式文件系统上的位置（发布区那一类）不是某台机器的本地位置，**原样保留**
        （它本身就是全局可解析的地址，相对化反而失去意义）；
      * 空值原样保留：`null` 表示这份产物不存在，是有意义的信息。
    """
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return payload
    base = _optional_task_dir(ctx, task_id)
    base_real = os.path.realpath(base) if base else None
    cleaned = {}
    for key, value in paths.items():
        if key == "task_dir":
            continue
        if value is None or not isinstance(value, str):
            cleaned[key] = value
            continue
        converted = None
        if base_real and os.path.isabs(value):
            real = os.path.realpath(value)
            if real == base_real:
                converted = "."
            elif real.startswith(base_real + os.sep):
                converted = os.path.relpath(real, base_real).replace(os.sep, "/")
        cleaned[key] = converted if converted is not None else value
    out = dict(payload)
    out["paths"] = cleaned
    return out


def get_samples(args, ctx=None):
    """取样例：`samples` 的信封字段回传（选项名照 driver 的写法，不另起一套）。

    **每一项都显式传给执行侧，包括默认值**：默认值只有一个来源（工具表），这样
    "缺省取什么"对端从列工具里看到的就是实际生效的，不会出现"页面写着清洗后、
    实际拿回来的是隔离区"这种两边各说一套的情况。
    """
    task_id = args.get("task_id")
    # 已中止是终态：先说清"不会再有样本"，再交给执行侧（见 `_reject_if_aborted`）
    _reject_if_aborted(ctx, task_id, "样本")
    options = {
        "task-id": task_id,
        "type": str(arg_or_default("get_samples", args, "sample_type")),
        "table": str(arg_or_default("get_samples", args, "table")),
        "n": str(arg_or_default("get_samples", args, "limit")),
    }
    return _call(CMD_SAMPLES, options, ctx)


def get_report(args, ctx=None):
    """取报告：`report` 的信封字段回传（全文版或结构化版）。

    结构化报告先做一次可读性预检。**这条预检只改变报错归类，不改变任何业务结论**，
    边界如下（别把它扩展到别的能力上）：

      * 只在报告文件**确实存在**时才检查；文件不在就直接交给执行侧，
        "任务未完成"这类结论属于执行侧，本模块不抢；
      * 检查只做两件事：读不出、不是预期的对象结构。命中就报"产物内容不合法"，
        说明里写清是哪个任务的哪份报告，并提示其它产物可能仍可用；
      * 为什么值得单独分一类：报告文件坏掉时，执行侧会崩在通道层，对外变成
        "执行通道输出不合法"——这个说法会把人引去查服务本身，而真正坏的是那份文件；
      * 全文格式按文本读，不走这次检查；
      * 唯一的边界情况：任务还在跑、而报告文件已经落下且内容不合法 —— 这时执行侧
        本来也只会在通道层崩掉，并没有业务结论被抢走；
      * 其它产物的可读性由各自的读取能力负责，这里不代办。

    另外两条保护（都只影响"报什么错"，不影响任何业务结论）：
      * **大小上限**：全文与结构化两种格式都在取之前看一眼文件大小，超过上限就
        如实报"内容过大"并给出实际大小与上限，不把几十兆内容一次拉回内存；
      * 格式的默认值取自工具表，并**显式传给执行侧**，不让执行侧再取一遍默认。
    """
    task_id = args.get("task_id")
    # 已中止是终态：先说清"不会再有报告"，再看大小与内容（顺序见 `_reject_if_aborted`）
    _reject_if_aborted(ctx, task_id, "报告")
    fmt = str(arg_or_default("get_report", args, "format"))
    _reject_report_too_large(ctx, task_id, fmt)
    if fmt == "json":
        corrupt = _corrupt_report_error(ctx, task_id)
        if corrupt is not None:
            raise corrupt
    return _call(CMD_REPORT, {"task-id": task_id, "format": fmt}, ctx)


def _reject_report_too_large(ctx, task_id, fmt):
    """报告文件超过上限时提前挡住。

    只看**文件确实存在**的情况：文件不在就交给执行侧回"任务未完成"，那是它的
    结论。这里只回答一个事实——"这份报告大到不该用取内容的方式拿"。
    """
    base = _optional_task_dir(ctx, task_id)
    if base is None:
        return
    name = REPORT_MD_NAME if fmt == "md" else REPORT_JSON_NAME
    path = os.path.join(base, name)
    if not os.path.exists(path):
        return
    # 位置上若是管道/目录，`stat_regular_file` 会直接报"不是普通文件"：
    # 这比"报告尚未生成"诚实，也不会真去打开它
    info = stat_regular_file(path, display=name)
    if info.st_size > REPORT_MAX_BYTES:
        raise StructuredError(
            CODE_CONTENT_TOO_LARGE,
            "任务 %s 的报告（%s）有 %s 字节，超过上限 %s 字节；请改用下载取走完整报告"
            % (task_id, name, info.st_size, REPORT_MAX_BYTES),
            task_id=str(task_id), path=name, size=info.st_size,
            max_bytes=REPORT_MAX_BYTES)


def _corrupt_report_error(ctx, task_id):
    """结构化报告确实存在却读不出来/结构不对时，给出"产物内容不合法"的错误。

    返回 None 表示"不做判定"：文件不在（交给执行侧回"任务未完成"）、任务目录不存在
    （交给执行侧回"任务不存在"），或文件读得出来且结构正常（交给执行侧正常返回）。
    """
    base = _optional_task_dir(ctx, task_id)
    if base is None:
        return None
    path = os.path.join(base, REPORT_JSON_NAME)
    if not os.path.isfile(path):
        return None
    hint = "该任务的其它产物可能仍可用，可先用「列任务产物文件」看看"
    try:
        payload = _read_json_file(path, display=REPORT_JSON_NAME)
    except StructuredError as exc:
        return StructuredError(
            CODE_PRODUCT_INVALID,
            "任务 %s 的结构化报告（%s）内容不合法：%s。%s"
            % (task_id, REPORT_JSON_NAME, exc.message, hint),
            task_id=str(task_id), path=REPORT_JSON_NAME)
    if not isinstance(payload, dict):
        return StructuredError(
            CODE_PRODUCT_INVALID,
            "任务 %s 的结构化报告（%s）不是预期的对象结构（收到 %s），内容不合法；%s"
            % (task_id, REPORT_JSON_NAME, type(payload).__name__, hint),
            task_id=str(task_id), path=REPORT_JSON_NAME)
    return None


def list_tasks(args, ctx=None):
    """列任务：`tasks` 的信封字段回传。"""
    return _call(CMD_TASKS, None, ctx)


# ---------------------------------------------------------------------------
# 版本与时间边界：两处出处，必须标出处
# ---------------------------------------------------------------------------

def _task_versions(ctx, task_id, status):
    """取四类版本与两个时间边界，并标出处。

    优先级：元数据文件 → （仅当任务是成功状态时）结果文件 → 都没有就不给字段。
    绝不推算、绝不借用别的任务的数字；两处都拿不到时连 `versions_source` 都不出现。
    """
    base = _optional_task_dir(ctx, task_id)
    if base is None:
        return {}

    from_metadata, note = _versions_from_metadata(base)
    if from_metadata:
        out = dict(from_metadata)
        out["versions_source"] = VERSIONS_SOURCE_METADATA
        return out

    # 元数据缺席是集群任务的常态，不必专门说明；只有"文件在却取不出来"才要标出来
    if str(status or "") == STATUS_SUCCEEDED:
        from_result = _versions_from_result(base)
        if from_result:
            out = {"metadata_error": note} if note else {}
            out.update(from_result)
            out["versions_source"] = VERSIONS_SOURCE_RESULT
            return out
    return {"metadata_error": note} if note else {}


def _versions_from_metadata(base):
    """从元数据文件取版本与时间边界：返回 (字段字典, 需要说明的备注)。

    「文件根本不在」与「文件在但取不出来」是两件事：前者是集群任务的常态，不给备注；
    后者是产物坏了，要把话说明白，免得使用者以为任务没有版本信息。
    """
    path = os.path.join(base, METADATA_NAME)
    if not os.path.isfile(path):
        return {}, ""
    try:
        metadata = _read_json_file(path, display=METADATA_NAME)
    except StructuredError:
        return {}, "任务目录里的元数据文件读不出来，改从结果文件取版本与时间边界"
    if not isinstance(metadata, dict):
        return {}, "任务目录里的元数据文件不是对象，改从结果文件取版本与时间边界"
    out = {}
    versions = {}
    for label, key in _VERSION_FIELDS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            versions[label] = value.strip()
    if versions:
        out["versions"] = versions
    hashes = {}
    for label, key in _HASH_FIELDS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            hashes[label] = value.strip()
    if hashes:
        out["version_hashes"] = hashes
    boundaries = {}
    for key in _TIME_FIELDS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            boundaries[key] = value.strip()
    if boundaries:
        out["time_boundaries"] = boundaries
    if not out:
        return {}, "任务目录里的元数据文件没有可用的版本字段，改从结果文件取"
    return out, ""


def _versions_from_result(base):
    """从成功任务的结果文件取同一批字段；取不到就返回空字典。

    结果文件的形状见接口文档 §4.5：`versions` 下按"规则 / 评分方案 / 策略"分组，
    每组可能有 `version` 与 `sha256`；时间边界在 `time_boundaries`。只有真实存在的
    取值才带上 —— 结果文件里没有策略版本号时就不给这一项，不拿别的字段顶替。
    """
    path = os.path.join(base, RESULT_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        result = _read_json_file(path, display=RESULT_NAME)
    except StructuredError:
        return {}
    if not isinstance(result, dict):
        return {}
    if isinstance(result.get("status"), str) and result["status"] != STATUS_SUCCEEDED:
        return {}

    out = {}
    versions = {}
    data_version = result.get("data_version")
    if isinstance(data_version, str) and data_version.strip():
        versions["data"] = data_version.strip()
    groups = result.get("versions")
    groups = groups if isinstance(groups, dict) else {}
    hashes = {}
    for label in ("rule", "scoring", "policy"):
        group = groups.get(label)
        if not isinstance(group, dict):
            continue
        version = group.get("version")
        if isinstance(version, str) and version.strip():
            versions[label] = version.strip()
        digest = group.get("sha256")
        if isinstance(digest, str) and digest.strip():
            hashes[label] = digest.strip()
    if versions:
        out["versions"] = versions
    if hashes:
        out["version_hashes"] = hashes
    operator_library = groups.get("operator_library")
    if isinstance(operator_library, str) and operator_library.strip():
        out["operator_library"] = operator_library.strip()
    boundaries = {}
    source_boundaries = result.get("time_boundaries")
    if isinstance(source_boundaries, dict):
        for key in _TIME_FIELDS:
            value = source_boundaries.get(key)
            if isinstance(value, str) and value.strip():
                boundaries[key] = value.strip()
    if boundaries:
        out["time_boundaries"] = boundaries
    return out


# ---------------------------------------------------------------------------
# 6 取作业计数（读任务真实产物）
# ---------------------------------------------------------------------------

def get_job_counters(args, ctx=None):
    """取作业计数：输入、输出、隔离、去重、修复，全部来自该任务的真实产物。

    优先读任务目录的 `counts.json`（driver 结算后落的计数，键名与 `result` 的
    `counts` 完全一致）；该文件不在时退回 `result.json` 里的 `counts` 字段。
    两个都取不到就报 `FILE_NOT_FOUND` 并说清看过哪些文件 —— 不返回零值。
    """
    task_id = args.get("task_id")
    base = task_dir(ctx, task_id, must_exist=True)
    checked = []
    for name, key in (("counts.json", None), ("result.json", "counts")):
        path = os.path.join(base, name)
        checked.append(name)
        if not os.path.exists(path):
            continue
        # 先确认是普通文件：管道会挂住，目录也不是能读的产物
        stat_regular_file(path, display=name)
        data = _read_json_file(path, display=name)
        counts = data if key is None else (data.get(key) if isinstance(data, dict) else None)
        if isinstance(counts, dict) and counts:
            return {
                "task_id": str(task_id),
                "source": name if key is None else "%s#%s" % (name, key),
                "counts": counts,
                "count_groups": [
                    {"key": group, "label": label,
                     "present": isinstance(counts.get(group), (dict, int, float))}
                    for group, label in COUNT_GROUPS
                ],
            }
    raise StructuredError(
        CODE_FILE_NOT_FOUND,
        "该任务目录里没有可用的计数产物（已检查 %s）：取不到输入、输出、隔离、去重、修复计数"
        % "、".join(checked),
        task_id=str(task_id), checked=checked)


# ---------------------------------------------------------------------------
# 7 取作业日志（只回尾部若干行）
# ---------------------------------------------------------------------------

def _log_targets(base, kind):
    """按来源列出相对路径：执行日志（driver.log）与各作业日志（logs/*.log）。"""
    wanted = []
    missing = []
    if kind in ("driver", "all"):
        driver_path = os.path.join(base, DRIVER_LOG_NAME)
        if os.path.exists(driver_path):
            stat_regular_file(driver_path, display=DRIVER_LOG_NAME)
            wanted.append(DRIVER_LOG_NAME)
        else:
            missing.append(DRIVER_LOG_NAME)
    if kind in ("job", "all"):
        log_dir = os.path.join(base, JOB_LOG_DIRNAME)
        names = []
        if os.path.isdir(log_dir):
            for name in sorted(os.listdir(log_dir)):
                if not name.endswith(".log"):
                    continue
                full = os.path.join(log_dir, name)
                if not os.path.exists(full):
                    continue
                # 同上：日志位置上出现管道要立刻报错，而不是"没有作业日志"
                stat_regular_file(full, display="%s/%s" % (JOB_LOG_DIRNAME, name))
                names.append(name)
        if names:
            wanted.extend("%s/%s" % (JOB_LOG_DIRNAME, name) for name in names)
        else:
            missing.append("%s/*.log" % JOB_LOG_DIRNAME)
    return wanted, missing


def get_job_logs(args, ctx=None):
    """取作业日志：执行日志与各作业日志，只回尾部 `lines` 行。

    日志是排查失败原因的唯一途径，所以"没有日志"要明说，不能回空列表让人以为
    任务什么都没写。
    """
    task_id = args.get("task_id")
    # 默认值一律取工具表并显式传给执行侧（不让执行侧再取一遍默认，免得两处不一致）
    kind = str(arg_or_default("get_job_logs", args, "kind"))
    lines = int(arg_or_default("get_job_logs", args, "lines"))
    lines = max(1, min(lines, MAX_LOG_LINES))
    base = task_dir(ctx, task_id, must_exist=True)

    wanted, missing = _log_targets(base, kind)
    if not wanted:
        raise StructuredError(
            CODE_FILE_NOT_FOUND,
            "该任务目录里没有%s日志（缺少 %s）"
            % ({"driver": "执行", "job": "作业", "all": ""}.get(kind, ""),
               "、".join(missing) or "日志文件"),
            task_id=str(task_id), kind=kind, missing=missing)

    entries = []
    for rel in wanted:
        target = resolve_under(base, rel, must_exist=True)
        tail, truncated, size = _tail_lines(target, lines)
        entries.append({
            "path": rel,
            "size": size,
            "returned_lines": len(tail),
            "truncated": truncated,
            "lines": tail,
        })
    payload = {
        "task_id": str(task_id),
        "kind": kind,
        "lines": lines,
        "logs": entries,
    }
    if missing:
        payload["missing"] = missing
    return payload


# ---------------------------------------------------------------------------
# 8 列任务产物文件
# ---------------------------------------------------------------------------

def list_task_files(args, ctx=None):
    """列出任务目录下的产物文件：相对路径、大小、类型。

    三条排除规则：
      * 目录遍历不跟随符号链接；
      * 指向任务目录之外的文件不列出 —— 列出来却读不到，对使用者毫无意义；
      * **不当作产物的东西不列出**（见模块顶部常量块）：服务控制文件、隐藏文件、
        校验和附属文件、流程中途的临时残留 —— 它们要么是服务判断执行过程的凭据，
        要么是文件怎么落盘的痕迹、跑一半留下的半成品，都不是任务算出来的数据。
    执行日志与各作业日志仍照常列出 —— 它们是排查失败原因的正当依据。
    """
    task_id = args.get("task_id")
    files = _task_files(task_dir(ctx, task_id, must_exist=True))
    return {"task_id": str(task_id), "files": files, "count": len(files)}


def _task_files(base):
    """列一个任务目录下的产物文件（相对路径、大小、类型），按相对路径字典序。

    `list_task_files` 与「结果里的产物清单」**共用这一份**，这样两处的排除规则与
    排序永远一致，不会一处多一个文件、一处少一个。排除规则见模块顶部的常量组：
    控制文件、隐藏文件、校验和附属文件。
    """
    files = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full):
                continue                      # 断链符号链接、设备等一律跳过
            real = os.path.realpath(full)
            if not _is_within(real, base):
                continue                      # 指向任务目录之外的符号链接不列
            if exclusion_reason(name) or exclusion_reason(real):
                continue                      # 控制文件 / 隐藏文件 / 校验和附属文件
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files.append({"path": _relpath(base, full), "size": size,
                          "kind": kind_of(name)})
    files.sort(key=lambda item: item["path"])
    return files


# ---------------------------------------------------------------------------
# 9 读产物文件
# ---------------------------------------------------------------------------

def read_task_file(args, ctx=None):
    """读取某个任务产物的内容。

    只对结构化数据、Markdown 与纯文本返回内容；二进制只回"不可预览"，不硬塞乱码
    （与对端既有约定一致，不改成错误码）。超过本次上限不截断而是明确报
    `CONTENT_TOO_LARGE`（截断的内容拿去判断会误导），不存在报 `FILE_NOT_FOUND`，
    越界报 `FORBIDDEN_PATH`。

    **不当作产物的东西**（见模块顶部常量块：控制文件、隐藏文件、校验和附属文件、
    临时残留）不对外提供：它们不是任务产物，里面的字段是实现细节或是半成品。
    这类路径按"不允许访问"拒绝，而不是假装文件不存在 —— 这样使用者与排障的人
    一眼就知道是"这类文件不给看"，不会去猜文件是不是被删了。
    错误细节里额外给 `exclusion_kind`（`control` / `sidecar` / `residue`）：
    对端可以按错误码统一处理，也可以按细类给出不同说法；下载路由用同一套取值。

    打开之前先确认是**普通文件**：命名管道会一直等写端而挂住，设备文件会读出
    无穷无尽的数据，两者都不该让一个请求把处理线程占死。
    """
    task_id = args.get("task_id")
    rel = args.get("path")
    limit = arg_or_default("read_task_file", args, "max_bytes")
    limit = DEFAULT_READ_BYTES if limit is None else int(limit)
    limit = max(1, min(limit, MAX_READ_BYTES))
    base = task_dir(ctx, task_id, must_exist=True)
    rel_text = str(rel).strip()
    # 先判路径形状（绝对路径、上级跳转、符号链接逃逸），再判是不是不对外的东西，
    # 最后才看文件在不在 —— 越界与这类文件都不该因为"文件恰好不在"而改口
    target = resolve_under(base, rel, must_exist=False)
    reason = exclusion_reason(rel_text) or exclusion_reason(target)
    if reason:
        detail = {"exclusion": reason,
                  "reason_kind": EXCLUSION_KINDS.get(reason)}
        if reason == REASON_CONTROL_FILE:
            detail["internal_file"] = True
        raise StructuredError(
            CODE_FORBIDDEN_PATH,
            "这是%s，不作为任务产物对外提供：%s" % (reason, rel_text),
            task_id=str(task_id), path=rel_text, **detail)
    if not os.path.exists(target):
        raise StructuredError(
            CODE_FILE_NOT_FOUND, "文件不存在：%s" % rel_text,
            task_id=str(task_id), path=rel_text)

    if os.path.isdir(target):
        raise StructuredError(
            CODE_FILE_NOT_FOUND,
            "该路径是一个目录，不是可读文件：%s（可用「列任务产物文件」查看下面的文件）" % rel_text,
            task_id=str(task_id), path=rel_text, reason_kind=REASON_KIND_DIRECTORY)

    # 非常规文件（管道、设备、套接字）在读之前就挡住：见 `stat_regular_file`
    info = stat_regular_file(target, display=rel_text)
    size = info.st_size
    kind = kind_of(target)
    payload = {"task_id": str(task_id), "path": rel_text, "kind": kind, "size": size}
    if kind not in PREVIEWABLE_KINDS:
        # 二进制与其它类型：如实标不可预览，不返回内容
        payload["previewable"] = False
        return payload
    if size > limit:
        raise StructuredError(
            CODE_CONTENT_TOO_LARGE,
            "文件 %s 有 %s 字节，超过本次读取上限 %s 字节；调高 max_bytes 或改用下载"
            % (rel_text, size, limit),
            task_id=str(task_id), path=rel_text, size=size, max_bytes=limit)
    with io.open(target, "rb") as handle:
        raw = handle.read(limit)
    payload["previewable"] = True
    payload["content"] = raw.decode("utf-8", "replace")
    return payload


# ---------------------------------------------------------------------------
# 10 列原始数据（只读列举本机的原始区）
# ---------------------------------------------------------------------------

def _raw_files(base):
    """列一个原始区目录下的普通文件（跳过隐藏文件、附属物与残留，按名称排序）。

    返回 (文件项列表, 跳过项列表)。跳过的条目带原因 —— 原始区里出现命名管道之类
    的东西属于异常，不该悄悄消失，但也不该让整个能力失败。
    """
    files, skipped = [], []
    try:
        entries = sorted(os.scandir(base), key=lambda item: item.name)
    except OSError as exc:
        raise StructuredError(
            CODE_FILE_NOT_FOUND,
            "读取原始区目录失败：%s" % (getattr(exc, "strerror", None) or "无法读取"),
            reason="unreadable")
    for entry in entries:
        if entry.name.startswith(HIDDEN_FILE_PREFIX):
            continue
        reason = exclusion_reason(entry.name)
        if reason:
            skipped.append({"name": entry.name, "reason": reason})
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                skipped.append({"name": entry.name,
                                "reason": "不是普通文件（原始数据只认普通文件）"})
                continue
            info = entry.stat()
        except OSError:
            skipped.append({"name": entry.name, "reason": "读取属性失败"})
            continue
        files.append({"name": entry.name, "size": info.st_size,
                      "updated_at": info.st_mtime, "kind": kind_of(entry.name)})
    return files, skipped


def list_raw_datasets(args, ctx=None):
    """列出原始区里已有的数据集与文件。

    **"没有配置原始区" / "原始区目录不存在" / "原始区是空的"是三件不同的事**，
    必须分开说：
      * 没配置（取不到位置）→ 报前置依赖缺失，说明要配什么；
      * 目录不存在 → 报前置依赖缺失，说明"数据还没放到这台机器上"；
      * 目录在、但里面什么都没有 → 正常返回空列表 + 一句明确说明。
    把后两种混成"空列表"，用户会以为数据丢了。

    数据集 = 原始区下的一个子目录（三张表放同一个目录里，如 `ml-1m/`）；直接放在
    原始区根下的文件按"散装文件"单列。两者分开表示，不混在一个数组里。
    对外只给名称与相对表示，**不给机器上的绝对位置**。
    """
    base = raw_dir(ctx)
    if not base:
        raise StructuredError(
            CODE_DEPENDENCY_MISSING,
            "这台机器上没有配置原始数据区的位置（环境变量 ML_RAW_DIR 为空），"
            "无法列出原始数据；请先配置该位置，再由运维把数据放进去",
            reason="unconfigured", env_key="ML_RAW_DIR")
    if not os.path.isdir(base):
        raise StructuredError(
            CODE_DEPENDENCY_MISSING,
            "原始数据区目录不存在：数据还没有放到这台机器上（这与「目录在、"
            "但里面没有数据」是两件事）；请由运维把原始三表放进去后再查",
            reason="missing")

    try:
        entries = sorted(os.scandir(base), key=lambda item: item.name)
    except OSError as exc:
        raise StructuredError(
            CODE_FILE_NOT_FOUND,
            "读取原始区目录失败：%s" % (getattr(exc, "strerror", None) or "无法读取"),
            reason="unreadable")

    datasets, loose_files, skipped = [], [], []
    for entry in entries:
        if entry.name.startswith(HIDDEN_FILE_PREFIX):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            skipped.append({"name": entry.name, "reason": "读取属性失败"})
            continue
        if not is_dir:
            continue                      # 根下的文件由 `_raw_files` 统一处理
        files, inner_skipped = _raw_files(entry.path)
        datasets.append({
            "name": entry.name,
            "path": entry.name,
            "files": files,
            "file_count": len(files),
            "size": sum(item["size"] for item in files),
            "updated_at": max((item["updated_at"] for item in files), default=None),
        })
        for item in inner_skipped:
            skipped.append({"dataset": entry.name,
                            "name": item["name"], "reason": item["reason"]})

    loose_files, loose_skipped = [], []
    for entry in entries:
        if entry.name.startswith(HIDDEN_FILE_PREFIX):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if not entry.is_file(follow_symlinks=False):
            skipped.append({"name": entry.name,
                            "reason": "不是普通文件（原始数据只认普通文件）"})
            continue
        reason = exclusion_reason(entry.name)
        if reason:
            skipped.append({"name": entry.name, "reason": reason})
            continue
        try:
            info = entry.stat()
        except OSError:
            skipped.append({"name": entry.name, "reason": "读取属性失败"})
            continue
        loose_files.append({"name": entry.name, "size": info.st_size,
                            "updated_at": info.st_mtime, "kind": kind_of(entry.name)})

    total_files = sum(item["file_count"] for item in datasets) + len(loose_files)
    payload = {
        "exists": True,
        "datasets": datasets,
        "files": loose_files,
        "count": len(datasets),
        "file_count": total_files,
        "empty": not datasets and not loose_files,
    }
    if payload["empty"]:
        payload["note"] = ("原始区目录存在，但里面还没有数据"
                           "（这与「目录不存在」是两件事）")
    if skipped:
        payload["skipped"] = skipped
    return payload


# ---------------------------------------------------------------------------
# 11 取方案内容（读方案目录里的方案文件）
# ---------------------------------------------------------------------------

def get_scheme_content(args, ctx=None):
    """取清洗规则或评分方案的完整内容（供「评分依据」展示）。

    文件名由分派层做过形状校验（必须是形如 `xxx.json` 的纯文件名）；这里再走一次
    方案目录的白名单解析 —— 换了名字的符号链接把文件指到目录之外同样会被挡下。
    文件不在报 `FILE_NOT_FOUND`；文件在但不是合法结构化数据报 `PRODUCT_INVALID`
    （"产物坏了"与"没有这份产物"的补救办法不同）。
    """
    name = str(args.get("name") or "").strip()
    display = "config/%s" % name
    root = config_root(ctx)
    if not os.path.isdir(root):
        raise StructuredError(
            CODE_DEPENDENCY_MISSING,
            "方案目录不存在（仓库内 config/），无法读取方案内容",
            reason="missing", path="config/")
    target = resolve_under(root, name, must_exist=True)
    info = stat_regular_file(target, display=display)
    if info.st_size > MAX_READ_BYTES:
        raise StructuredError(
            CODE_CONTENT_TOO_LARGE,
            "方案文件 %s 有 %s 字节，超过上限 %s 字节"
            % (display, info.st_size, MAX_READ_BYTES),
            path=display, size=info.st_size, max_bytes=MAX_READ_BYTES)
    content = _read_json_file(target, display=display)
    if not isinstance(content, dict):
        raise StructuredError(
            CODE_PRODUCT_INVALID,
            "%s 的内容不是预期的对象结构（收到 %s）；方案文件应当是顶层为对象的结构"
            % (display, type(content).__name__),
            path=display)
    return {"name": name, "path": display, "size": info.st_size, "content": content}


# ---------------------------------------------------------------------------
# 12 列已发布版本（读集群上的发布区）
# ---------------------------------------------------------------------------

def looks_like_missing(stderr_text):
    """集群命令的报错是否表示"这个目录不存在"（而不是连不上）。

    集群未启动、配置指向不通的地址、权限不足、安全模式等，全都归类为"读不到"，
    由调用方报 `DRIVER_UNREACHABLE`；只有真的确认目录不存在，才当作"还没发布"。
    为保证文案稳定，调用方已把子进程的语言固定成英文。
    """
    text = (stderr_text or "").lower()
    markers = ("no such file or directory",
               "file does not exist",
               "filenotfoundexception",
               "no such file or dir")
    return any(marker in text for marker in markers)

#: 兼容旧名字（本模块内部历史调用与测试可能仍用下划线写法）。
_looks_like_missing = looks_like_missing


def _parse_ls(output):
    """解析 `hdfs dfs -ls -R` 的输出为条目列表。

    每行形如：`drwxr-xr-x - user group 0 2026-09-29 13:41 /data/published/v1`，
    从右往左取（路径 → 时间 → 大小），不依赖用户名/组名的列数。
    """
    items = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Found "):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        perms = parts[0]
        if not perms or perms[0] not in ("d", "-"):
            continue
        try:
            size = int(parts[-4])
        except ValueError:
            size = None
        items.append({
            "is_dir": perms[0] == "d",
            "size": size,
            "date": parts[-3],
            "clock": parts[-2],
            "path": parts[-1].rstrip("/"),
        })
    return items


def _epoch_from_ls(date_text, clock_text):
    """把 `-ls` 的日期与时间转成时间戳（集群报的是本机时区的本地时间）。"""
    try:
        return time.mktime(time.strptime("%s %s" % (date_text, clock_text), "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return None


def _published_version_entry(ctx, item, files):
    """把一个版本目录与它的文件整理成对外条目。"""
    version = item["path"].rsplit("/", 1)[-1]
    tables = []
    found = set()
    others = []
    for entry in files:
        name = entry["path"].rsplit("/", 1)[-1]
        matched = None
        for table in TABLE_ORDER:
            if name == TABLE_FILES[table]:
                matched = table
                break
        if matched:
            found.add(matched)
            tables.append({"name": matched, "file": name, "size": entry["size"]})
        else:
            others.append({"name": name, "size": entry["size"]})
    tables.sort(key=lambda entry: TABLE_ORDER.index(entry["name"]))
    entry = {
        "data_version": version,
        "path": item["path"],
        "tables": tables,
        "other_files": others,
        "published_at_text": "%s %s" % (item["date"], item["clock"]),
    }
    published_at = _epoch_from_ls(item["date"], item["clock"])
    if published_at is not None:
        entry["published_at"] = published_at
    missing = [table for table in TABLE_ORDER if table not in found]
    if missing:
        # 只如实标注缺哪张表，不补零、不推算
        entry["missing_tables"] = missing
    source_task = _published_source_task(ctx, item["path"], others)
    if source_task:
        entry["source_task_id"] = source_task
    return entry


def _published_source_task(ctx, version_path, others):
    """发布目录里若随版本保存了任务元数据，取其中的任务编号；没有就不给这个字段。

    当前发布环节只往版本目录里放三表与内容清单（`.published_hashes.json`），
    并不带来源任务信息 —— 这里只在真有元数据文件时读它，绝不按内容哈希去猜。
    """
    if not any(entry.get("name") == "metadata.json" for entry in others):
        return None
    meta_path = "%s/metadata.json" % version_path
    code, out, _err = run_hdfs(["dfs", "-cat", meta_path], ctx)
    if code != 0:
        return None
    try:
        metadata = json.loads(out)
    except ValueError:
        return None
    task_id = metadata.get("task_id") if isinstance(metadata, dict) else None
    return task_id.strip() if isinstance(task_id, str) and task_id.strip() else None


def list_published_versions(args, ctx=None):
    """列出发布区里的数据版本：版本名、三表文件与大小、发布时间、来源任务。

    这里有一条必须守住的判断：**"读不到发布区"与"发布区是空的"是两件事**。
    集群没启动、连不上、没有权限 —— 一律报 `DRIVER_UNREACHABLE` 并说明情况；
    只有真的读到了发布区、并且它确实是空的，才回空列表。返回空列表冒充
    "没有已发布数据"会让使用者以为数据丢了。
    """
    base_path = published_dir(ctx)
    code, out, err = run_hdfs(["dfs", "-ls", "-R", base_path], ctx)
    if code != 0:
        if looks_like_missing(err):
            return {
                "published_dir": base_path,
                "exists": False,
                "versions": [],
                "note": "发布区目录尚未创建，说明还没有发布过任何数据版本",
            }
        detail = sanitize_text((err or out or "").strip()[-1500:], ctx)
        raise StructuredError(
            CODE_DRIVER_UNREACHABLE,
            "读取发布区失败：连不上集群（%s）。集群可能未启动；请启动集群后重试，"
            "这不是「没有已发布数据」" % base_path,
            published_dir=base_path, exit_code=code, stderr=detail or None)

    items = _parse_ls(out)
    version_dirs = [item for item in items
                    if item["is_dir"] and item["path"].rsplit("/", 1)[0] == base_path]
    versions = []
    for item in version_dirs:
        children = [entry for entry in items
                    if not entry["is_dir"]
                    and entry["path"].rsplit("/", 1)[0] == item["path"]]
        versions.append(_published_version_entry(ctx, item, children))
    versions.sort(key=lambda entry: (-(entry.get("published_at") or 0), entry["data_version"]))
    return {
        "published_dir": base_path,
        "exists": True,
        "versions": versions,
        "count": len(versions),
    }


# ---------------------------------------------------------------------------
# 处理器登记表（本段负责的十二个名字）
# ---------------------------------------------------------------------------

#: 工具名 → 处理器。名称必须与 `tools.py` 的工具表逐字一致（测试会断言）。
HANDLERS = {
    "get_task_status": get_task_status,
    "get_task_result": get_task_result,
    "get_samples": get_samples,
    "get_report": get_report,
    "list_tasks": list_tasks,
    "list_published_versions": list_published_versions,
    "list_task_files": list_task_files,
    "read_task_file": read_task_file,
    "get_job_counters": get_job_counters,
    "get_job_logs": get_job_logs,
    "list_raw_datasets": list_raw_datasets,
    "get_scheme_content": get_scheme_content,
}
