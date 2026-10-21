# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.2：策略校验事实收集（与传输无关的纯函数）。

修复计划 §5 记录的死点：策略表里的「原始数据可读」（requires_raw）在**真实
路径从不触发** —— 因为运行期事实 facts 里从没有人真的去设置 raw_readable，
默认缺省又按「可读」处理，于是这条规则形同虚设。

本模块把事实收集独立出来，明确两件事：
  1. facts 是**观测结果**，不是猜测：running_task_id 来自任务列表，
     raw_readable 来自对原始数据目录的真实探测；
  2. 探测可注入（exists 参数），因此可离线单测，且**不依赖任何传输方式**
     —— 未来换 MCP 时，只要换掉探测来源即可，策略层 check_call 不变。

三态语义（关键）：
  raw_readable = True   已确认可读           → requires_raw 放行
  raw_readable = False  已确认不可读         → requires_raw 拒绝
  raw_readable = None   无法确定（未知）     → 不在策略层拦截，交由 driver/MCP
                                              返回结构化错误（DEPENDENCY_MISSING）
  这样「宁拦勿放」只建立在**真实观测**之上，绝不用未知冒充已读。
"""

import os

#: 原始数据目录的环境变量（与 Hadoop 侧 hadoop/scripts/env.sh 同源）
RAW_DIR_ENV = "ML_RAW_DIR"

#: 运行中任务的状态（与 driver 状态枚举一致）
RUNNING_STATES = ("queued", "running")


def default_raw_dir():
    """原始数据目录：优先环境变量；拿不到返回 None（= 未知，不拦）。"""
    return os.environ.get(RAW_DIR_ENV) or None


def pick_running_task(tasks):
    """从任务列表信封里挑出第一个运行中任务的 task_id（没有返回 None）。"""
    if isinstance(tasks, dict):
        items = tasks.get("tasks") or []
    elif isinstance(tasks, (list, tuple)):
        items = tasks
    else:
        items = []
    for task in items:
        if isinstance(task, dict) and task.get("status") in RUNNING_STATES:
            return task.get("task_id")
    return None


def probe_raw_readable(raw_dir, exists=None):
    """探测原始数据目录是否可读，返回 (raw_readable, resolved_dir)。

    raw_dir 为空 → (None, None)：无法确定，交由 driver 判定。
    """
    if not raw_dir:
        return None, None
    check = exists or os.path.isdir
    return bool(check(raw_dir)), raw_dir


def collect(tasks=None, raw_dir=None, exists=None):
    """收集策略校验事实。

    参数（全部可注入，便于测试与换后端）：
        tasks:   任务列表信封（tools.list_tasks() 的返回）；缺省表示未收集。
        raw_dir: 原始数据目录；缺省取 ML_RAW_DIR 环境变量。
        exists:  目录存在性判断函数；缺省 os.path.isdir。

    返回：
        {"running_task_id": "..."/None,
         "raw_readable": True/False/None,
         "raw_dir": "..." / None}
    """
    facts = {"running_task_id": pick_running_task(tasks)}
    readable, resolved = probe_raw_readable(
        raw_dir if raw_dir is not None else default_raw_dir(), exists)
    facts["raw_readable"] = readable
    facts["raw_dir"] = resolved
    return facts
