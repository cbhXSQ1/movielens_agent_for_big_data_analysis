# -*- coding: utf-8 -*-
"""远端能力服务的工具表（source=mcp）—— Agent 侧工具形状的权威来源。

参数名 / 默认值 / 枚举 / 取值范围以远端能力自描述为准
（`hadoop/mcpserver/tools.py` 的 `TOOLS`）。本表是「对端」（Agent）据以刷新
自己工具表的权威形状；运行时可用能力自描述校验一致性。

只有「快速演示清洗」(`quick_clean_demo`) 留在本机，见 `LOCAL_ONLY_TOOLS`。
"""

SOURCE_MCP = "mcp"

#: 参数类型（与远端 `hadoop/mcpserver/tools.py` 的取值对齐）。
PARAM_STRING = "string"
PARAM_INT = "int"
PARAM_BOOL = "bool"
PARAM_ENUM = "enum"

#: 远端参数类型 → JSON Schema 类型。
_JSON_TYPE = {
    PARAM_STRING: "string",
    PARAM_INT: "integer",
    PARAM_BOOL: "boolean",
    PARAM_ENUM: "string",
}


def _param(name, type_, description, required=False, default=None,
           values=None, minimum=None, maximum=None):
    """一条远端参数说明（照 apparat 侧 `_p` 的形状，供 `_schema` 转 JSON Schema）。"""
    return {
        "name": name,
        "type": type_,
        "description": description,
        "required": bool(required),
        "default": default,
        "values": tuple(values) if values is not None else None,
        "minimum": minimum,
        "maximum": maximum,
    }


def _schema(params):
    """把远端参数列表转成 JSON Schema 的 object 形态（properties + required）。"""
    props = {}
    required = []
    for param in params:
        entry = {"type": _JSON_TYPE.get(param["type"], param["type"])}
        if param["description"]:
            entry["description"] = param["description"]
        if param["values"] is not None:
            entry["enum"] = list(param["values"])
        if param["default"] is not None:
            entry["default"] = param["default"]
        if param["minimum"] is not None:
            entry["minimum"] = param["minimum"]
        if param["maximum"] is not None:
            entry["maximum"] = param["maximum"]
        props[param["name"]] = entry
        if param["required"]:
            required.append(param["name"])
    return {"type": "object", "properties": props, "required": required}


def _tool(name, description, params, long_running, estimated_seconds):
    return {
        "name": name,
        "source": SOURCE_MCP,
        "long_running": long_running,
        "estimated_seconds": estimated_seconds,
        "description": description,
        "parameters": _schema(params),
    }


# ---------------------------------------------------------------------------
# 复用参数说明（与远端同名同默认值）
# ---------------------------------------------------------------------------

TASK_ID = _param("task_id", PARAM_STRING, "任务编号。", required=True)

SCOPE = _param(
    "scope", PARAM_ENUM,
    "数据范围。全部：完整数据，正式用途；小样本：仅联调，且不会进入发布区。",
    default="full", values=("full", "sample"))

RULES = _param(
    "rules", PARAM_STRING,
    "清洗方案文件路径。缺省用已登记的默认方案；自定义方案请先校验再执行。")

SCORING = _param(
    "scoring", PARAM_STRING,
    "评分方案文件路径。缺省用已登记的默认方案；自定义方案请先校验再执行。")

DATA_VERSION = _param(
    "data_version", PARAM_STRING,
    "数据版本号。缺省取方案里声明的版本；与方案声明不一致会被拒绝。")

TAG = _param("tag", PARAM_STRING, "任务标签，便于人辨认，不影响执行结果。")

FORCE = _param(
    "force", PARAM_BOOL,
    "忽略「已有任务在运行」的冲突。默认关闭；开启后返回里会提示风险。",
    default=False)


# ---------------------------------------------------------------------------
# 23 项远端能力（逐项照 `hadoop/mcpserver/tools.py` 转写）
# ---------------------------------------------------------------------------

REMOTE_TOOL_SPECS = {
    # ---- 与现有动作一一对应 ----
    "validate_config": _tool(
        "validate_config",
        "校验清洗方案与评分方案是否合法，不读取也不改动任何数据。自定义方案必须先过这一步。",
        (RULES, SCORING), False, 3),
    "list_schemes": _tool(
        "list_schemes",
        "列出已登记的清洗方案与评分方案（含默认方案与已上传的自定义方案）。",
        (), False, 1),
    "start_cleaning_task": _tool(
        "start_cleaning_task",
        "发起完整流程：清洗、清洗前后评分、发布。异步受理，立即返回任务编号。",
        (SCOPE, DATA_VERSION, TAG, FORCE, RULES, SCORING), True, 1500),
    "start_clean_task": _tool(
        "start_clean_task",
        "只跑清洗链，不评分、不发布。适合先看清洗结果再决定是否评分。",
        (SCOPE, DATA_VERSION, TAG, FORCE, RULES, SCORING), True, 900),
    "score_task": _tool(
        "score_task",
        "单独评分：可对原始数据、某个任务的清洗产物、或已发布数据评分。不发布任何数据。",
        (
            _param("source", PARAM_ENUM,
                   "评分对象。原始：对原始三表评分；已发布：对发布区某个数据版本评分；"
                   "指定任务：对某个任务的清洗产物评分。",
                   required=True, values=("raw", "published", "task")),
            _param("from_task", PARAM_STRING,
                   "来源任务编号。评分对象为「指定任务」时必填，且该任务必须是成功状态、"
                   "清洗产物存在。"),
            SCORING, TAG,
        ), True, 300),
    "get_task_status": _tool(
        "get_task_status",
        "查询任务的当前状态、所处阶段与进度百分比；有版本与时间边界时一并返回。",
        (TASK_ID,), False, 1),
    "get_task_result": _tool(
        "get_task_result",
        "取一个已成功任务的权威结果：评分对比、数据量变化、隔离统计、版本与时间边界。"
        "未完成时会明确拒绝。",
        (TASK_ID,), False, 1),
    "get_samples": _tool(
        "get_samples",
        "取清洗后的样本，或隔离区的样本（带命中规则与原因）。",
        (
            TASK_ID,
            _param("sample_type", PARAM_ENUM,
                   "样本类型。清洗后：清洗产物里的记录；隔离区：被隔离的记录。",
                   default="cleaned", values=("cleaned", "quarantine")),
            _param("table", PARAM_ENUM, "表名。", default="movies",
                   values=("movies", "ratings", "users")),
            _param("limit", PARAM_INT, "返回条数（本端上限 2000，执行侧无上限）。",
                   default=5, minimum=0, maximum=2000),
        ), False, 1),
    "get_report": _tool(
        "get_report",
        "取评估报告：全文版或结构化版。",
        (
            TASK_ID,
            _param("format", PARAM_ENUM,
                   "报告格式。全文：Markdown 全文；结构化：结构化数据。",
                   default="json", values=("md", "json")),
        ), False, 1),
    "list_tasks": _tool(
        "list_tasks",
        "列出最近的任务及其状态、开始时间与数据版本。",
        (), False, 1),

    # ---- 取证与自描述 ----
    "list_published_versions": _tool(
        "list_published_versions",
        "列出发布区里的数据版本：版本名、来源任务、三表文件与大小、发布时间。"
        "没有发布过就返回空列表。",
        (), False, 2),
    "list_task_files": _tool(
        "list_task_files",
        "列出某个任务目录下的产物文件：相对路径、大小、类型。",
        (TASK_ID,), False, 1),
    "read_task_file": _tool(
        "read_task_file",
        "读取某个任务产物的内容。只对文本、结构化数据与 Markdown 返回内容，且有大小上限；"
        "其余类型只回不可预览。",
        (
            TASK_ID,
            _param("path", PARAM_STRING,
                   "产物文件的相对路径，取自「列任务产物文件」的结果。", required=True),
            _param("max_bytes", PARAM_INT, "最多读取多少字节。",
                   default=262144, minimum=1, maximum=1048576),
        ), False, 1),
    "get_job_counters": _tool(
        "get_job_counters",
        "取某个任务里隔离、去重、修复等计数，全部来自该任务真实产物。",
        (TASK_ID,), False, 1),
    "get_job_logs": _tool(
        "get_job_logs",
        "取某个任务运行时的日志尾部片段，用于排查失败原因；有行数上限。",
        (
            TASK_ID,
            _param("kind", PARAM_ENUM,
                   "日志来源。执行日志：驱动整体流程的日志；作业日志：具体作业的日志；"
                   "全部：两者都取。",
                   default="driver", values=("driver", "job", "all")),
            _param("lines", PARAM_INT, "返回尾部多少行。",
                   default=100, minimum=1, maximum=2000),
        ), False, 1),
    "list_raw_datasets": _tool(
        "list_raw_datasets",
        "列出原始区里已有的数据集与文件（名称、大小、更新时间）。"
        "原始数据只由运维放到这台机器上，本服务不提供上传。",
        (), False, 2),
    "get_scheme_content": _tool(
        "get_scheme_content",
        "取清洗规则或评分方案的完整内容（供「评分依据」展示）。只允许读取已登记的方案文件。",
        (
            _param("name", PARAM_STRING,
                   "方案文件名，取自「列方案」的结果（例如 scoring_scheme.v1.json）。",
                   required=True),
        ), False, 1),
    "upload_scheme": _tool(
        "upload_scheme",
        "上传一份自定义清洗规则或评分方案并登记落盘，随后可用于校验与发起任务。"
        "同名方案默认不覆盖；默认方案不允许被上传覆盖。",
        (
            _param("name", PARAM_STRING,
                   "方案文件名（单层，必须以 .json 结尾，例如 custom_rules.json）。",
                   required=True),
            _param("content", PARAM_STRING,
                   "方案 JSON 文本内容。", required=True),
            _param("type", PARAM_ENUM,
                   "方案类型。清洗规则：清洗方案；评分方案：评分方案。缺省按内容判定。",
                   values=("cleaning_rules", "scoring_scheme")),
            _param("overwrite", PARAM_BOOL,
                   "是否允许覆盖同名方案。默认关闭；默认方案即使开启也不允许覆盖。",
                   default=False),
        ), False, 3),
    "describe_capabilities": _tool(
        "describe_capabilities",
        "返回本端全部能力、各自参数、读写属性、是否长耗时与预计耗时，以及契约版本。"
        "对端据此刷新工具表。",
        (), False, 1),

    # ---- 补充：等待、取消、占用状态、健康检查 ----
    "wait_for_task": _tool(
        "wait_for_task",
        "挂起等待，直到任务进入终态或到达等待上限（单次上限三十分钟）。"
        "超时如实回仍在运行、不算失败；建议一次等几分钟（例如 120～300 秒），超时后再调一次。",
        (
            TASK_ID,
            _param("timeout_seconds", PARAM_INT,
                   "最多等待多少秒（本端上限 1800，超出按上限执行并在返回里注明）。",
                   default=300, minimum=0, maximum=1800),
        ), True, 300),
    "cancel_task": _tool(
        "cancel_task",
        "请求中止一个正在排队或运行的任务。会如实回报是否真的停住，并把任务状态落成已中止。",
        (TASK_ID,), False, 5),
    "get_scheduler_state": _tool(
        "get_scheduler_state",
        "当前有没有任务在占用执行位、占用者是谁、如何解除。"
        "用于向用户解释「现在为什么发不出去」。",
        (), False, 1),
    "health_check": _tool(
        "health_check",
        "探活：返回服务是否正常、契约版本与启动时间。不触发任何任务。",
        (), False, 1),
}

#: 本机专属能力（不来自远端）：只有快速演示清洗。
LOCAL_ONLY_TOOLS = ("quick_clean_demo",)


def remote_tool_names():
    """全部远端工具名（按清单顺序）。"""
    return tuple(REMOTE_TOOL_SPECS.keys())


def spec_for(name):
    """按名取远端工具定义；不存在（含本机专属）返回 None。"""
    return REMOTE_TOOL_SPECS.get(name)
