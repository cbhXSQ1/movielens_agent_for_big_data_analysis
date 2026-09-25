# -*- coding: utf-8 -*-
"""第 5 节 Agent：工具层（课程要求的「Agent 工具」）。

9 个工具 = 接口文档 §7 的 8 个契约工具 + 1 个附加工具 quick_clean_demo。
每个工具都只是「组织参数 → 调 driver → 原样返回信封」，
成功给真实数字，失败给真实原因，绝不编造。

返回值统一是 driver 的 JSON 信封 dict：
    成功：{"ok": true, ...}
    失败：{"ok": false, "error": {"code": "...", "message": "..."}}
"""

import time

from . import driver_client as dc

# 默认配置路径（相对仓库根，driver 会自己解析成绝对路径）
DEFAULT_RULES = "config/cleaning_rules.v1.json"
DEFAULT_SCORING = "config/scoring_scheme.v1.json"

# samples 允许的取值（driver 会再校验一次，这里提前挡一下，报错更友好）
SAMPLE_TYPES = ("cleaned", "quarantine")
SAMPLE_TABLES = ("users", "movies", "ratings")


# ---------------------------------------------------------------------------
# 1) validate_config —— 用户自定义配置时先校验
# ---------------------------------------------------------------------------
def validate_config(rules=None, scoring=None):
    """校验清洗方案 / 评分方案是否合法。不改任何数据。"""
    return dc.call_driver("validate", {
        "rules": rules or DEFAULT_RULES,
        "scoring": scoring or DEFAULT_SCORING,
    })


# ---------------------------------------------------------------------------
# 2) list_schemes —— 列出已登记方案
# ---------------------------------------------------------------------------
def list_schemes():
    """列出 config/ 下已登记的方案（默认方案 + 自定义方案）。"""
    return dc.call_driver("schemes")


# ---------------------------------------------------------------------------
# 3) start_cleaning_task —— 发起清洗+评分任务
# ---------------------------------------------------------------------------
def start_cleaning_task(rules=None, scoring=None, data_version=None, tag=None,
                        foreground=False, force=False, exec_mode=None):
    """发起一次「清洗 + 五维评分 + 发布」任务。

    默认异步：立刻返回 task_id，Agent 应先告诉用户「任务已提交」，
    再用 get_task_status 轮询进度。

    exec_mode: None(=cluster，走真实 Hadoop Streaming) / "local"（本地引擎，快）
    foreground: True 时阻塞到跑完（调试用）
    force: True 时忽略「已有运行中任务」冲突（不推荐）
    """
    return dc.call_driver("start", {
        "rules": rules or DEFAULT_RULES,
        "scoring": scoring or DEFAULT_SCORING,
        "data-version": data_version,
        "tag": tag,
        "foreground": foreground,
        "force": force,
        "exec": exec_mode,
    })


# ---------------------------------------------------------------------------
# 4) get_task_status —— 查执行情况
# ---------------------------------------------------------------------------
def get_task_status(task_id):
    """查任务状态与进度（status/stage/progress_percent/errors）。"""
    if not task_id:
        return {"ok": False, "error": {"code": "USAGE", "message": "缺少 task_id"}}
    return dc.call_driver("status", {"task-id": task_id})


# ---------------------------------------------------------------------------
# 5) get_task_result —— 取权威结果（只有成功任务才给）
# ---------------------------------------------------------------------------
def get_task_result(task_id):
    """取任务的权威结果：五维对比、数据量变化、隔离统计、limitations。

    任务失败/未完成时 driver 会拒绝返回（退出码 3/5），
    此时本函数原样把错误信封带回，Agent 必须如实转述，不能拿旧数据顶替。
    """
    if not task_id:
        return {"ok": False, "error": {"code": "USAGE", "message": "缺少 task_id"}}
    return dc.call_driver("result", {"task-id": task_id})


# ---------------------------------------------------------------------------
# 6) get_samples —— 回答「给我看几条异常记录」
# ---------------------------------------------------------------------------
def get_samples(task_id, sample_type="cleaned", table="movies", n=5):
    """取清洗后样本或隔离区样本。

    sample_type: cleaned（留下来的） / quarantine（被隔离的，带规则与原因）
    table:       users / movies / ratings
    """
    if not task_id:
        return {"ok": False, "error": {"code": "USAGE", "message": "缺少 task_id"}}
    if sample_type not in SAMPLE_TYPES:
        return {"ok": False, "error": {
            "code": "USAGE", "message": "--type 必须是 cleaned 或 quarantine"}}
    if table not in SAMPLE_TABLES:
        return {"ok": False, "error": {
            "code": "USAGE", "message": "--table 必须是 users / movies / ratings"}}
    return dc.call_driver("samples", {
        "task-id": task_id,
        "type": sample_type,
        "table": table,
        "n": int(n),
    })


# ---------------------------------------------------------------------------
# 7) get_report —— 取完整评估报告
# ---------------------------------------------------------------------------
def get_report(task_id, fmt="md"):
    """取评估报告。fmt: md（全文） / json（结构化）。"""
    if not task_id:
        return {"ok": False, "error": {"code": "USAGE", "message": "缺少 task_id"}}
    if fmt not in ("md", "json"):
        return {"ok": False, "error": {
            "code": "USAGE", "message": "--format 必须是 md 或 json"}}
    return dc.call_driver("report", {"task-id": task_id, "format": fmt})


# ---------------------------------------------------------------------------
# 8) list_tasks —— 列出最近任务
# ---------------------------------------------------------------------------
def list_tasks():
    """列出最近 50 个任务（task_id / status / started_at / data_version）。"""
    return dc.call_driver("tasks")


# ---------------------------------------------------------------------------
# 9) quick_clean_demo —— 附加工具：演示性快速清洗（非契约子命令）
# ---------------------------------------------------------------------------
def quick_clean_demo(raw_dir=None, sample=None, out=None, quiet=True):
    """秒级拿到干净数据与五维分数，不经过 Hadoop。

    与集群任务同引擎同数，但不维护任务状态、不做发布与版本保护。
    只用于现场演示 / 前端取数 / 追问前快速预览；
    对外汇报的权威数字必须来自 start_cleaning_task + get_task_result。
    """
    return dc.call_quick_clean({
        "raw": raw_dir,
        "out": out,
        "sample": sample,
        "quiet": quiet,
    })


# ---------------------------------------------------------------------------
# 组合动作：发起后轮询到结束（演示/前端联调用，不是独立工具）
# ---------------------------------------------------------------------------
def run_and_wait(poll_seconds=5, timeout_seconds=1800, on_progress=None, **start_kwargs):
    """start → 反复 status → 结束。返回最后一次 status 信封。

    on_progress(status_envelope) 每轮回调一次，可用于前端打进度。
    超时或失败都如实返回，不伪造成功。
    """
    started = start_cleaning_task(**start_kwargs)
    if not dc.is_ok(started):
        return started

    task_id = started.get("task_id")
    deadline = time.time() + timeout_seconds
    last = started
    while time.time() < deadline:
        last = get_task_status(task_id)
        if not dc.is_ok(last):
            return last
        if callable(on_progress):
            on_progress(last)
        state = last.get("status")
        if state in ("succeeded", "failed"):
            return last
        time.sleep(poll_seconds)

    return {"ok": False, "error": {
        "code": dc.CODE_TIMEOUT,
        "message": "等待任务超时（%.0f 秒），任务仍在后台运行" % timeout_seconds,
        "task_id": task_id,
    }}


# 对外暴露的工具清单（供 CLI / 前端 / 汇报材料引用）
TOOL_NAMES = (
    "validate_config",
    "list_schemes",
    "start_cleaning_task",
    "get_task_status",
    "get_task_result",
    "get_samples",
    "get_report",
    "list_tasks",
    "quick_clean_demo",   # 附加工具，非契约子命令
)
