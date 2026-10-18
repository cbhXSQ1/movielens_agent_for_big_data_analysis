# -*- coding: utf-8 -*-
"""mcpserver/tools.py —— 对外能力清单（二十项）与能力自描述。

这份清单是**对端工具表的权威来源**：对端据 `describe_capabilities` 刷新自己的
工具表，不需要两端各维护一份。因此每条都必须写全：名称、中文标题、一句话说明、
参数（类型/必填/默认值/取值范围）、来源、只读还是写入、是否长耗时、预计耗时。

清单与 `docs/hadoop/迭代一重构_MCP_实现方案.md` §3 一一对应：
  * §3.1 十项：与现有 driver 动作一一对应；
  * §3.2 六项：取证与自描述；
  * §3.3 四项：等待、取消、占用状态、健康检查（要求文档点名要、但没单列成工具）。

参数取值的枚举一律与 driver 真实实现对齐（`values` 即 driver 接受的字面量），
测试里会与 driver 常量逐项比对，防止两边漂移。

本模块只用标准库，不导入官方协议包 —— 契约表要能在裸解释器里被列出与断言。
"""

from .contract import (
    INTERFACE_VERSION,
    DRIVER_INTERFACE_VERSION,
    STATUSES,
    STATUS_LABELS,
    STRUCTURED_ERROR_CODES,
)

# ---------------------------------------------------------------------------
# 参数构造小工具（把重复的字段说明收敛，避免二十条表里各写一遍）
# ---------------------------------------------------------------------------

#: 参数类型：string / int / bool / enum
PARAM_STRING = "string"
PARAM_INT = "int"
PARAM_BOOL = "bool"
PARAM_ENUM = "enum"


def _p(name, type_, description, required=False, default=None,
       values=None, minimum=None, maximum=None):
    """一条参数说明。`values` 仅对枚举有意义；`minimum`/`maximum` 仅对整数有意义。"""
    item = {
        "name": name,
        "type": type_,
        "description": description,
        "required": bool(required),
        "default": default,
    }
    if values is not None:
        item["values"] = tuple(values)
    if minimum is not None:
        item["minimum"] = minimum
    if maximum is not None:
        item["maximum"] = maximum
    return item


TASK_ID = lambda desc="任务编号。": _p("task_id", PARAM_STRING, desc, required=True)  # noqa: E731

SCOPE = _p(
    "scope", PARAM_ENUM,
    "数据范围。全部：完整数据，正式用途；小样本：仅联调，且不会进入发布区。",
    default="full", values=("full", "sample"),
)

RULES = _p(
    "rules", PARAM_STRING,
    "清洗方案文件路径。缺省用已登记的默认方案；自定义方案请先校验再执行。",
)

SCORING = _p(
    "scoring", PARAM_STRING,
    "评分方案文件路径。缺省用已登记的默认方案；自定义方案请先校验再执行。",
)

DATA_VERSION = _p(
    "data_version", PARAM_STRING,
    "数据版本号。缺省取方案里声明的版本；与方案声明不一致会被拒绝。",
)

TAG = _p("tag", PARAM_STRING, "任务标签，便于人辨认，不影响执行结果。")

FORCE = _p(
    "force", PARAM_BOOL,
    "忽略「已有任务在运行」的冲突。默认关闭；开启后返回里会提示风险。",
    default=False,
)

# ---------------------------------------------------------------------------
# 二十项能力
# ---------------------------------------------------------------------------

TOOLS = (
    # ---- §3.1 与现有动作一一对应（十项）----
    {
        "name": "validate_config",
        "title": "校验方案",
        "description": "校验清洗方案与评分方案是否合法，不读取也不改动任何数据。自定义方案必须先过这一步。",
        "params": (RULES, SCORING),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 3,
    },
    {
        "name": "list_schemes",
        "title": "列方案",
        "description": "列出已登记的清洗方案与评分方案（含默认方案与已上传的自定义方案）。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "start_cleaning_task",
        "title": "发起全流程",
        "description": "发起完整流程：清洗、清洗前后评分、发布。异步受理，立即返回任务编号。",
        "params": (SCOPE, DATA_VERSION, TAG, FORCE, RULES, SCORING),
        "read_only": False,
        "long_running": True,
        "estimated_seconds": 1500,
    },
    {
        "name": "start_clean_task",
        "title": "只清洗",
        "description": "只跑清洗链，不评分、不发布。适合先看清洗结果再决定是否评分。",
        "params": (SCOPE, DATA_VERSION, TAG, FORCE, RULES, SCORING),
        "read_only": False,
        "long_running": True,
        "estimated_seconds": 900,
    },
    {
        "name": "score_task",
        "title": "独立评分",
        "description": "单独评分：可对原始数据、某个任务的清洗产物、或已发布数据评分。不发布任何数据。",
        "params": (
            _p("source", PARAM_ENUM,
               "评分对象。原始：对原始三表评分；已发布：对发布区某个数据版本评分；指定任务：对某个任务的清洗产物评分。",
               required=True, values=("raw", "published", "task")),
            _p("from_task", PARAM_STRING,
               "来源任务编号。评分对象为「指定任务」时必填，且该任务必须是成功状态、清洗产物存在。"),
            SCORING, TAG,
        ),
        "read_only": False,
        "long_running": True,
        "estimated_seconds": 300,
    },
    {
        "name": "get_task_status",
        "title": "查任务状态",
        "description": "查询任务的当前状态、所处阶段与进度百分比；有版本与时间边界时一并返回。",
        "params": (TASK_ID(),),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "get_task_result",
        "title": "取任务结果",
        "description": "取一个已成功任务的权威结果：评分对比、数据量变化、隔离统计、版本与时间边界。未完成时会明确拒绝。",
        "params": (TASK_ID(),),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "get_samples",
        "title": "取样例",
        "description": "取清洗后的样本，或隔离区的样本（带命中规则与原因）。",
        "params": (
            TASK_ID(),
            # 默认值与执行侧保持一致（缺省取"清洗后"的"电影表"），避免同一句话
            # 经本服务调用与直接调执行侧得到不同的结果。
            _p("sample_type", PARAM_ENUM, "样本类型。清洗后：清洗产物里的记录；隔离区：被隔离的记录。",
               default="cleaned", values=("cleaned", "quarantine")),
            _p("table", PARAM_ENUM, "表名。", default="movies",
               values=("movies", "ratings", "users")),
            # 执行侧对条数只要求非负、没有上限；这里保留一个上限作为保护（一次拉几十万行
            # 会把内存吃满），并在说明里写明，属于刻意的加性收紧。
            _p("limit", PARAM_INT, "返回条数（本端上限 2000，执行侧无上限）。",
               default=5, minimum=0, maximum=2000),
        ),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "get_report",
        "title": "取报告",
        "description": "取评估报告：全文版或结构化版。",
        "params": (
            TASK_ID(),
            # 默认值与执行侧一致（缺省给结构化数据）；要 Markdown 全文请显式指定。
            _p("format", PARAM_ENUM, "报告格式。全文：Markdown 全文；结构化：结构化数据。",
               default="json", values=("md", "json")),
        ),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "list_tasks",
        "title": "列任务",
        "description": "列出最近的任务及其状态、开始时间与数据版本。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },

    # ---- §3.2 取证与自描述（六项）----
    {
        "name": "list_published_versions",
        "title": "列已发布版本",
        "description": "列出发布区里的数据版本：版本名、来源任务、三表文件与大小、发布时间。没有发布过就返回空列表。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 2,
    },
    {
        "name": "list_task_files",
        "title": "列任务产物文件",
        "description": "列出某个任务目录下的产物文件：相对路径、大小、类型。",
        "params": (TASK_ID(),),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "read_task_file",
        "title": "读产物文件",
        "description": "读取某个任务产物的内容。只对文本、结构化数据与 Markdown 返回内容，且有大小上限；其余类型只回不可预览。",
        "params": (
            TASK_ID(),
            _p("path", PARAM_STRING, "产物文件的相对路径，取自「列任务产物文件」的结果。", required=True),
            _p("max_bytes", PARAM_INT, "最多读取多少字节。", default=262144, minimum=1, maximum=1048576),
        ),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "get_job_counters",
        "title": "取作业计数",
        "description": "取某个任务里隔离、去重、修复等计数，全部来自该任务真实产物。",
        "params": (TASK_ID(),),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "get_job_logs",
        "title": "取作业日志",
        "description": "取某个任务运行时的日志尾部片段，用于排查失败原因；有行数上限。",
        "params": (
            TASK_ID(),
            _p("kind", PARAM_ENUM, "日志来源。执行日志：驱动整体流程的日志；作业日志：具体作业的日志；全部：两者都取。",
               default="driver", values=("driver", "job", "all")),
            _p("lines", PARAM_INT, "返回尾部多少行。", default=100, minimum=1, maximum=2000),
        ),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "list_raw_datasets",
        "title": "列原始数据",
        "description": "列出原始区里已有的数据集与文件（名称、大小、更新时间）。原始数据只由运维放到这台机器上，本服务不提供上传。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 2,
    },
    {
        "name": "upload_scheme",
        "title": "上传方案",
        "description": ("上传自定义的清洗规则或评分方案：**先校验、再落盘** —— 校验不通过不会写入任何文件；"
                        "通过后写进与已登记方案同一个目录；同名文件已存在时默认拒绝，"
                        "需要显式允许覆盖才会替换。上传成功后可直接用于「校验方案」「发起全流程」与「独立评分」。"),
        "params": (
            _p("name", PARAM_STRING,
               "目标文件名：单层文件名、必须以 .json 结尾（例如 my_rules.v2.json），不得包含路径分隔符。",
               required=True),
            _p("content", PARAM_STRING,
               "方案的 JSON 内容（文本）。内容里的类型字段决定它是清洗规则还是评分方案。",
               required=True),
            _p("type", PARAM_ENUM,
               "方案类型。缺省由内容里的类型字段判定；显式给出时必须与内容一致，否则拒绝。",
               values=("cleaning_rules", "scoring_scheme")),
            _p("overwrite", PARAM_BOOL,
               "同名文件已存在时是否覆盖。缺省拒绝；允许覆盖时同样是「先校验通过才替换」，"
               "校验不过不会破坏原文件。",
               default=False),
        ),
        "read_only": False,
        "long_running": False,
        "estimated_seconds": 3,
    },
    {
        "name": "delete_scheme",
        "title": "删除方案",
        "description": ("删除一个**自定义**方案文件（与「上传方案」成对）。"
                        "两道护栏：**随仓库交付的两份默认方案永远不可删除**；"
                        "**当前有任务在跑时先不删**（避免删掉正在被使用的方案）。"
                        "删除不可恢复，调用前请确认。"),
        "params": (
            _p("name", PARAM_STRING,
               "要删除的方案文件名：单层文件名、以 .json 结尾（取自「列方案」的结果）。",
               required=True),
        ),
        "read_only": False,
        "long_running": False,
        "estimated_seconds": 2,
    },
    {
        "name": "get_scheme_content",
        "title": "取方案内容",
        "description": "取清洗规则或评分方案的完整内容（供「评分依据」展示）。只允许读取已登记的方案文件。",
        "params": (
            _p("name", PARAM_STRING, "方案文件名，取自「列方案」的结果（例如 scoring_scheme.v1.json）。",
               required=True),
        ),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "describe_capabilities",
        "title": "能力自描述",
        "description": "返回本端全部能力、各自参数、读写属性、是否长耗时与预计耗时，以及契约版本。对端据此刷新工具表。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },

    # ---- §3.3 补充的四项 ----
    {
        "name": "wait_for_task",
        "title": "等任务结束",
        "description": ("挂起等待，直到任务进入终态或到达等待上限（单次上限三十分钟）。"
                        "超时如实回仍在运行、不算失败；建议一次等几分钟（例如 120～300 秒），"
                        "超时后再调一次。"),
        "params": (
            TASK_ID(),
            # 上限从两小时收窄到半小时：等待占住一条工作线程，长时间挂起会把服务拖瘦。
            # 超出上限不是拒绝，而是按上限执行并在返回里注明被压缩（timeout_clamped）。
            _p("timeout_seconds", PARAM_INT,
               "最多等待多少秒（本端上限 1800，超出按上限执行并在返回里注明）。",
               default=300, minimum=0, maximum=1800),
        ),
        "read_only": True,
        "long_running": True,
        "estimated_seconds": 300,
    },
    {
        "name": "cancel_task",
        "title": "取消任务",
        "description": "请求中止一个正在排队或运行的任务。会如实回报是否真的停住，并把任务状态落成已中止。",
        "params": (TASK_ID(),),
        "read_only": False,
        "long_running": False,
        "estimated_seconds": 5,
    },
    {
        "name": "get_scheduler_state",
        "title": "查占用状态",
        "description": "当前有没有任务在占用执行位、占用者是谁、如何解除。用于向用户解释「现在为什么发不出去」。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
    {
        "name": "health_check",
        "title": "健康检查",
        "description": "探活：返回服务是否正常、契约版本与启动时间。不触发任何任务。",
        "params": (),
        "read_only": True,
        "long_running": False,
        "estimated_seconds": 1,
    },
)

#: 名称 → 条目，便于按名称取参数定义。
TOOL_INDEX = {tool["name"]: tool for tool in TOOLS}

#: 每条工具都必须有的字段（测试据此逐条断言）。
REQUIRED_TOOL_FIELDS = (
    "name", "title", "description", "params", "read_only",
    "long_running", "estimated_seconds",
)


def tool_names():
    """按清单顺序返回全部工具名称。"""
    return tuple(tool["name"] for tool in TOOLS)


def get_tool(name):
    """按名称取工具定义；不存在返回 None（调用方负责回 UNKNOWN_TOOL 类错误）。"""
    return TOOL_INDEX.get(name)


def as_public_entry(tool):
    """把一条工具定义转成对外条目（补齐对端工具表需要的字段）。

    `source` 固定为 `mcp`：这些能力都由远端（本端）执行，与本机直调的工具区分开。
    """
    entry = {
        "name": tool["name"],
        "title": tool["title"],
        "description": tool["description"],
        "source": "mcp",
        "read_only": tool["read_only"],
        "long_running": tool["long_running"],
        "estimated_seconds": tool["estimated_seconds"],
        "params": [
            {k: v for k, v in param.items() if v is not None}
            for param in tool["params"]
        ],
    }
    return entry


def describe_capabilities(server_info=None):
    """能力自描述 —— 对端工具表的权威来源。

    `server_info` 由服务层传入（名称、版本、启动时间等）；契约层不编造这些值，
    没传就不给这个字段，绝不用占位数据冒充。
    """
    payload = {
        "ok": True,
        "interface_version": INTERFACE_VERSION,
        "driver_interface_version": DRIVER_INTERFACE_VERSION,
        "tools": [as_public_entry(tool) for tool in TOOLS],
        "read_only_tools": [t["name"] for t in TOOLS if t["read_only"]],
        "write_tools": [t["name"] for t in TOOLS if not t["read_only"]],
        "long_running_tools": [t["name"] for t in TOOLS if t["long_running"]],
        "statuses": [
            {"value": status, "label": STATUS_LABELS[status]} for status in STATUSES
        ],
        "error_codes": list(STRUCTURED_ERROR_CODES),
    }
    if server_info:
        payload["server"] = server_info
    return payload
