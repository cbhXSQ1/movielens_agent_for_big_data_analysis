# -*- coding: utf-8 -*-
"""第 5 节 Agent：运行口径快照（让「追问」沿用第一轮选定的口径）。

要解决的问题
------------
前端的「追问」路径历史上只回传 `text` 与 `task_id`，执行方式（cluster / local）、
运行口径（full / sample）等全部丢失，driver 于是回落到自己那边的默认值，
出现「页面上选的」和「实际跑的」不一致。

为什么按 task_id 存（方案 A）而不是另搞 session
----------------------------------------------
① 前端每次回传全套参数    ：最干净，但要改前端，且刷新页面后就断；
② 服务端按 session_id 存  ：要给 HTTP 层引入「会话」概念与生命周期；
③ 服务端按 task_id 存（本模块）：追问本来就带 task_id，**零前端改动即可修好**；
   后端无新增概念；进程重启后自然失效并回落到默认值（失效方向是安全的）。

本模块选 ③，并把合并结果通过 `/api/chat` 的 `opts` 字段回显，
前端想做「显式回传」时直接用这个回显值即可，等于 ②① 的能力也顺带具备。

只认 OPTION_KEYS 里的键，其它一律丢弃 —— 防止把脏参数透传给 driver。
"""

import threading

_LOCK = threading.RLock()
_BY_TASK = {}     # task_id -> {opt: value}
_ORDER = []       # 简易 LRU：按写入顺序维护 task_id
_LAST = {}        # 最近一次生效的口径（task_id 未知时的兜底）
_MAX_ITEMS = 64

OPTION_KEYS = ("rules", "scoring", "data_version", "tag", "exec_mode", "scope")


def normalize(opts):
    """只保留认识的键；丢掉 None / 空串；顺手做大小写归一。"""
    out = {}
    if not isinstance(opts, dict):
        return out
    for k in OPTION_KEYS:
        v = opts.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        if k in ("exec_mode", "scope") and isinstance(v, str):
            v = v.lower()
        out[k] = v
    return out


def recall(task_id=None):
    """取回某个任务曾用过的口径；查不到就用「最近一次」；都没有返回 {}。"""
    with _LOCK:
        if task_id and task_id in _BY_TASK:
            return dict(_BY_TASK[task_id])
        return dict(_LAST)


def remember(task_id, opts):
    """记下一轮真实生效的口径。**只对真正发起的任务调用**（接管旧任务不要调）。"""
    opts = normalize(opts)
    if not opts:
        return opts
    with _LOCK:
        if task_id:
            _BY_TASK[task_id] = dict(opts)
            if task_id in _ORDER:
                _ORDER.remove(task_id)
            _ORDER.append(task_id)
            while len(_ORDER) > _MAX_ITEMS:
                _BY_TASK.pop(_ORDER.pop(0), None)
        _LAST.clear()
        _LAST.update(opts)
    return opts


def merge(base, override):
    """优先级：本次请求显式 > 快照 > 默认。base 可为 None。"""
    out = normalize(base)
    out.update(normalize(override))
    return out


def last_task_id():
    """最近一次写入的 task_id（追问没带 task_id 时用来猜"你在说哪个任务"）。"""
    with _LOCK:
        return _ORDER[-1] if _ORDER else None
