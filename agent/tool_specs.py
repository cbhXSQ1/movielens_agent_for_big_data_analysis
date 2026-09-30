# -*- coding: utf-8 -*-
"""第 5 节 Agent v2：工具表 —— 现有工具统一注册为 JSON Schema（阶段 1）。

服务对象：tool_policy（阶段 2 的策略校验）与 llm_loop（阶段 3 的 function
calling）共用这一份表；形状与 `tools.py` 的实参逐项对齐（有单测锁定，见
`agent/tests/test_tool_specs.py`），**参数边界不得超出 driver 契约**
（docs/hadoop/agent-interface.md v1.0）与现有实现。

约束（与 llm_parse 的白名单同一精神）：
  * 只声明参数，不声明任何业务数字；
  * 枚举与 tools.py 常量同源（SCOPES / SAMPLE_TYPES / SAMPLE_TABLES）；
  * 新增工具（v1.1 的 start_clean_task / score_task）等 driver 落地后在此追加，
    追加即生效 —— llm_loop / tool_policy 不需要再改。
"""

from . import tools

# ---------------------------------------------------------------------------
# 工具表
# ---------------------------------------------------------------------------
# 每条：name（供模型选工具）/ description（用途与何时用）/
#        parameters（JSON Schema 的 object 形态，properties 与 tools.* 形参对齐）

TOOL_SPECS = {
    "validate_config": {
        "name": "validate_config",
        "description": "校验清洗方案/评分方案配置文件是否合法（用户自定义配置时先跑）。"
                       "不改任何数据，成败都返回校验明细。",
        "parameters": {
            "type": "object",
            "properties": {
                "rules": {"type": "string",
                          "description": "清洗方案 JSON 路径；缺省用默认方案"},
                "scoring": {"type": "string",
                            "description": "评分方案 JSON 路径；缺省用默认方案"},
            },
            "required": [],
        },
    },
    "list_schemes": {
        "name": "list_schemes",
        "description": "列出 config/ 下已登记的清洗/评分方案（默认 + 自定义）。"
                       "用户问「有哪些规则/方案」时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "start_cleaning_task": {
        "name": "start_cleaning_task",
        "description": "发起一次「清洗 + 五维评分 + 发布」的全流程任务（异步，"
                       "立即返回 task_id，之后用 get_task_status 轮询）。"
                       "用户要清洗/评估/打分/治理数据时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "rules": {"type": "string",
                          "description": "清洗方案 JSON 路径；缺省用默认方案"},
                "scoring": {"type": "string",
                            "description": "评分方案 JSON 路径；缺省用默认方案"},
                "data_version": {"type": "string",
                                 "description": "数据版本 id；与配置声明不一致会报版本冲突"},
                "tag": {"type": "string", "description": "任务备注标签"},
                "foreground": {"type": "boolean",
                               "description": "True 时阻塞到跑完（调试用）；默认 False 异步"},
                "force": {"type": "boolean",
                          "description": "True 时忽略「已有运行中任务」冲突（不推荐）"},
                "exec_mode": {"type": "string", "enum": ["cluster", "local"],
                              "description": "cluster=真实 Hadoop（默认）；local=本地引擎快速演示"},
                "scope": {"type": "string", "enum": list(tools.SCOPES),
                          "description": "full=全量正式口径（默认）；sample=前 2000 行仅联调"},
            },
            "required": [],
        },
    },
    "get_task_status": {
        "name": "get_task_status",
        "description": "查任务状态与进度（status/stage/progress_percent/errors）。"
                       "用户问「跑到哪一步/完成没」时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string",
                            "description": "任务标识，形如 20260925-184303-379a4b"},
            },
            "required": ["task_id"],
        },
    },
    "get_task_result": {
        "name": "get_task_result",
        "description": "取任务的权威结果：五维对比、数据量变化、隔离统计、局限性。"
                       "任务失败/未完成时如实返回错误，不拿旧数据顶替。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务标识"},
            },
            "required": ["task_id"],
        },
    },
    "get_samples": {
        "name": "get_samples",
        "description": "取样本：清洗后留下的（cleaned）或被隔离的（quarantine，"
                       "带规则与原因）。用户要「看几条异常/隔离记录」时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务标识"},
                "sample_type": {"type": "string", "enum": list(tools.SAMPLE_TYPES),
                                "description": "cleaned=清洗后；quarantine=隔离区（默认）"},
                "table": {"type": "string", "enum": list(tools.SAMPLE_TABLES),
                          "description": "users / movies / ratings"},
                "n": {"type": "integer", "minimum": 1, "maximum": 200,
                      "description": "取几条（默认 5）"},
            },
            "required": ["task_id"],
        },
    },
    "get_report": {
        "name": "get_report",
        "description": "取完整评估报告：md=全文 / json=结构化。"
                       "用户要「完整报告/为什么这么评分」时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务标识"},
                "fmt": {"type": "string", "enum": ["md", "json"],
                        "description": "md=全文（默认）；json=结构化"},
            },
            "required": ["task_id"],
        },
    },
    "list_tasks": {
        "name": "list_tasks",
        "description": "列出最近 50 个任务（task_id/status/started_at/data_version）。"
                       "用户问「之前跑过哪些任务」时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "start_clean_task": {
        "name": "start_clean_task",
        "description": "v1.1：只跑清洗链（不经评分与发布），产物含隔离统计。"
                       "用户说「先只清洗/别评分」时用；之后可用 score_task 单独评分。"
                       "仅集群模式。",
        "parameters": {
            "type": "object",
            "properties": {
                "rules": {"type": "string", "description": "清洗方案 JSON 路径"},
                "scoring": {"type": "string", "description": "评分方案 JSON 路径"},
                "data_version": {"type": "string", "description": "数据版本 id"},
                "tag": {"type": "string", "description": "任务备注标签"},
                "exec_mode": {"type": "string", "enum": ["cluster"],
                              "description": "clean 仅支持 cluster"},
                "scope": {"type": "string", "enum": list(tools.SCOPES),
                          "description": "full（默认）/ sample（联调）"},
            },
            "required": [],
        },
    },
    "score_task": {
        "name": "score_task",
        "description": "v1.1：独立评分。source=raw 对原始数据评分；source=task 对"
                       "指定任务（from_task）的清洗产物评分；source=published 对"
                       "发布区数据评分。前置依赖缺失时如实返回 DEPENDENCY_MISSING。",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["raw", "published", "task"],
                           "description": "评分来源（必填）"},
                "from_task": {"type": "string", "description": "source=task 时的目标任务 id"},
                "scoring": {"type": "string", "description": "评分方案 JSON 路径"},
                "tag": {"type": "string", "description": "任务备注标签"},
                "foreground": {"type": "boolean", "description": "阻塞到完成（调试）"},
            },
            "required": ["source"],
        },
    },
    "quick_clean_demo": {
        "name": "quick_clean_demo",
        "description": "秒级快速演示：不经 Hadoop 跑同引擎清洗与五维评分。"
                       "只用于演示/预览，不能作为正式汇报口径（正式结果用"
                       "start_cleaning_task + get_task_result）。",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_dir": {"type": "string", "description": "原始数据目录；缺省用环境配置"},
                "sample": {"type": "integer", "minimum": 1,
                           "description": "抽样行数（如 2000）"},
                "out": {"type": "string", "description": "输出目录；缺省用临时目录"},
                "quiet": {"type": "boolean", "description": "静默模式（默认 True）"},
            },
            "required": [],
        },
    },
}


#: 工具调用名 → tools.py 实现函数名（单测据此锁定 schema 与实现参数一致）
SPEC_TO_FUNCTION = {
    "validate_config": "validate_config",
    "list_schemes": "list_schemes",
    "start_cleaning_task": "start_cleaning_task",
    "get_task_status": "get_task_status",
    "get_task_result": "get_task_result",
    "get_samples": "get_samples",
    "get_report": "get_report",
    "list_tasks": "list_tasks",
    "quick_clean_demo": "quick_clean_demo",
    "start_clean_task": "start_clean_task",
    "score_task": "score_task",
}

#: v1.1 预留位（当前已空：start_clean_task / score_task 已于 2026-09-30 转正）
#: 未来新增工具在此追加，llm_loop / tool_policy 无需再改。
PENDING_V11 = ()


def tool_names():
    """全部已注册工具名（供 llm_loop 的工具表与 policy 校验用）。"""
    return tuple(TOOL_SPECS.keys())


def schema_for(name):
    """拿一份工具 schema；未注册返回 None（policy / loop 据此拒绝）。"""
    return TOOL_SPECS.get(name)


def tools_module():
    """工具实现（tools.py），供 policy / loop 按名取函数执行。"""
    return tools