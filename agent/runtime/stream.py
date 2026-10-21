# -*- coding: utf-8 -*-
"""阶段 3b（ag-runtime）· T3.4 / T3.5 / T3.6：每对话一条推送流。

为什么单独一层：
  * 接口文档 §1：前端与 Agent 之间「每段对话一条持续推送流」。本模块就是这条
    流的中枢 —— **传输层无关**：进程内可以直接 `for event in sub`；将来的
    HTTP / SSE 端点（阶段 6）把同一个 `StreamSubscription` 包一层即可，无需改这里。
  * 事件类型严格按接口文档 §3.6（只做加性扩展）；工具表快照按 §3.7 的字段。

每条事件是一个可 JSON 化的普通 dict：
    {
      "event": "message_delta",     # §3.6 的事件名
      "conversation_id": "c0001",   # 只属于这段对话
      "seq": 7,                     # 本对话内单调递增（锁内分配，不重不乱）
      "ts": 1729000000.0,           # 产生时刻（time.time()）
      "data": {"text": "…", "node_id": 12}   # 事件各自的字段
    }

隔离与并发（T3.5）：
  * 每个对话一个**独立频道**（各自的订阅者列表与 seq 计数器）—— 一条流的事件
    永远不会串到别的对话；
  * 同一对话内，模型流式 `message_delta` 与后台 `task_status` 等事件共用同一条
    有序队列，**只追加、不覆盖**（后台事件在阶段 4 由唤醒逻辑 publish）。
  * 订阅对象同时提供**同步**（`get` / `for`）与**异步**（`aget` / `async for`）
    两种消费方式：前者给进程内调用，后者给 asyncio 运行时与 HTTP/SSE 复用。

工具表（T3.6）：
  * 订阅时先收到一条完整 `tools_snapshot`（含 来源 / 是否长耗时 / 预计时长）；
  * 工具集合变化时调用 `publish_tools()` 再推一条。
"""

import asyncio
import inspect
import queue
import threading
import time

from .. import tool_specs

# ---------------------------------------------------------------------------
# 事件类型（接口文档 §3.6，名字即契约；只做加性扩展）
# ---------------------------------------------------------------------------

TOOLS_SNAPSHOT = "tools_snapshot"
MESSAGE_DELTA = "message_delta"
MESSAGE_DONE = "message_done"
TASK_STATUS = "task_status"
TASK_RESUMED = "task_resumed"
RESULT_PAYLOAD = "result_payload"
NODE_ADDED = "node_added"
ARTIFACT_ADDED = "artifact_added"
ERROR = "error"

#: 当前定义的**全部**事件类型（长任务相关事件阶段 4 才真正产生，此处先定义齐全）。
EVENT_TYPES = (
    TOOLS_SNAPSHOT, MESSAGE_DELTA, MESSAGE_DONE, TASK_STATUS, TASK_RESUMED,
    RESULT_PAYLOAD, NODE_ADDED, ARTIFACT_ADDED, ERROR,
)

#: 订阅队列的关流哨兵（只在本模块内部流转，不推给消费方）。
_CLOSE = object()

# ---------------------------------------------------------------------------
# 工具表（接口文档 §3.7）—— 由 Agent 主动推送
# ---------------------------------------------------------------------------

#: 长耗时工具 → 预计时长（秒）。**兜底**：远端工具表的规格里已自带
#: `long_running` / `estimated_seconds`（见 `agent/mcp_tools.py`），优先用规格值；
#: 这份映射只用于没有规格或规格未声明这两个字段的（本机）工具。
#: 取值依据：全流程「清洗 + 五维评分 + 发布」耗时最长；只清洗次之；独立评分最短。
LONG_RUNNING_TOOLS = {
    "start_cleaning_task": 1500,
    "start_clean_task": 900,
    "score_task": 300,
}


def _spec_of(tool_name):
    """取工具规格（无则空 dict），供长耗时字段回退判定用。"""
    return tool_specs.schema_for(tool_name) or {}


def is_long_running(tool_name):
    """该工具是否会长时间运行（工具表 §3.7「长耗时」字段）。

    规格里声明了 `long_running` 就以此为准（远端工具表自带），否则回退到本地映射。
    """
    spec = _spec_of(tool_name)
    if "long_running" in spec:
        return bool(spec.get("long_running"))
    return tool_name in LONG_RUNNING_TOOLS


def estimated_seconds_for(tool_name):
    """长耗时工具的预计秒数；非长耗时或未登记返回 None。

    规格里声明了 `long_running`：长耗时取规格的 `estimated_seconds`，非长耗时返回 None。
    未声明则回退到本地映射。
    """
    spec = _spec_of(tool_name)
    if "long_running" in spec:
        if not spec.get("long_running"):
            return None
        return spec.get("estimated_seconds")
    return LONG_RUNNING_TOOLS.get(tool_name)


def _signature_defaults(fn):
    """从工具实现的签名里取各参数的默认值（供 §3.7「参数与默认值」）。"""
    if fn is None:
        return {}
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}
    out = {}
    for name, param in sig.parameters.items():
        if param.default is not inspect.Parameter.empty:
            out[name] = param.default
    return out


def _param_entry(name, prop, required, defaults):
    """把一个 JSON Schema 属性整理成 §3.7 要的「参数」条目。

    默认值优先取 schema 里声明的（远端工具表的权威默认值），
    其次回退到本机实现的签名默认值（本机专属工具）。
    """
    entry = {"name": name, "type": prop.get("type", "string"),
             "required": name in required}
    if "default" in prop:
        entry["default"] = prop["default"]
    elif name in defaults:
        entry["default"] = defaults[name]
    elif not entry["required"]:
        entry["default"] = None
    if "enum" in prop:
        entry["enum"] = list(prop["enum"])
    for key in ("minimum", "maximum"):
        if key in prop:
            entry[key] = prop[key]
    if prop.get("description"):
        entry["description"] = prop["description"]
    return entry


def tool_table():
    """构建完整工具表（接口文档 §3.7 字段：名称 / 说明 / 参数与默认值 / 来源 /
    是否长耗时 / 预计时长）。"""
    impl = tool_specs.tools_module()
    rows = []
    for name in tool_specs.tool_names():
        spec = tool_specs.schema_for(name) or {}
        params = spec.get("parameters") or {}
        required = set(params.get("required") or [])
        fn = getattr(impl, tool_specs.SPEC_TO_FUNCTION.get(name, ""), None)
        defaults = _signature_defaults(fn)
        entries = [_param_entry(pname, prop or {}, required, defaults)
                   for pname, prop in (params.get("properties") or {}).items()]
        rows.append({
            "name": name,
            "description": spec.get("description", ""),
            "parameters": entries,
            "source": tool_specs.source_for(name),
            "long_running": is_long_running(name),
            "estimated_seconds": estimated_seconds_for(name),
        })
    return rows


def tool_table_fingerprint():
    """工具表的轻量指纹（工具集合变化检测用；不变则相同）。"""
    return tuple((row["name"], row["source"]) for row in tool_table())


# ---------------------------------------------------------------------------
# 事件信封
# ---------------------------------------------------------------------------

def make_event(event_type, conversation_id, seq, data=None, ts=None):
    """造一条事件信封（纯 dict，可 JSON 化）。"""
    return {
        "event": event_type,
        "conversation_id": conversation_id,
        "seq": seq,
        "ts": time.time() if ts is None else ts,
        "data": dict(data or {}),
    }


# ---------------------------------------------------------------------------
# 订阅对象
# ---------------------------------------------------------------------------

class StreamSubscription(object):
    """某段对话推送流上的一个消费者（传输层无关）。

    同步用法：
        sub = hub.subscribe(cid)
        event = sub.get(timeout=1.0)      # 阻塞取一条（超时返回 None）
        for event in sub:                 # 迭代到 close()
            ...

    异步用法（asyncio 运行时 / 将来 SSE 端点）：
        async for event in sub.aiter():
            ...
    """

    def __init__(self, conversation_id):
        self.conversation_id = conversation_id
        self._queue = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    # -- 生产侧（仅 StreamHub 调用）--------------------------------------
    def put(self, event):
        with self._lock:
            if self._closed:
                return
            self._queue.put(event)

    # -- 同步消费侧 -------------------------------------------------------
    def get(self, timeout=None):
        """阻塞取一条事件；超时或已关流返回 None。"""
        try:
            if timeout is None:
                event = self._queue.get()
            else:
                event = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        return None if event is _CLOSE else event

    def poll(self):
        """非阻塞取一条；当前没有返回 None。"""
        try:
            event = self._queue.get_nowait()
        except queue.Empty:
            return None
        return None if event is _CLOSE else event

    def drain(self):
        """把当前已到达的事件全部取出（测试与批量消费用）。"""
        out = []
        while True:
            event = self.poll()
            if event is None:
                return out
            out.append(event)

    def __iter__(self):
        while True:
            event = self.get()
            if event is None:
                return
            yield event

    # -- 异步消费侧 -------------------------------------------------------
    async def aget(self, timeout=None):
        """异步版 get：把阻塞读放到线程池，不卡事件循环。"""
        try:
            event = await asyncio.to_thread(self._queue.get, True, timeout)
        except queue.Empty:
            return None
        return None if event is _CLOSE else event

    async def aiter(self):
        """异步迭代：收到关流哨兵时结束。"""
        while True:
            event = await self.aget()
            if event is None:
                return
            yield event

    # -- 关闭 -------------------------------------------------------------
    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(_CLOSE)

    @property
    def closed(self):
        return self._closed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# 推送中枢
# ---------------------------------------------------------------------------

class StreamHub(object):
    """每对话一条推送流的中枢：频道隔离 + 有序追加 + 工具表主动推送。"""

    def __init__(self, tools_builder=tool_table):
        self._lock = threading.RLock()
        self._channels = {}            # conversation_id -> [StreamSubscription, ...]
        self._seq = {}                 # conversation_id -> 已分配的最大序号
        self._tools_builder = tools_builder or tool_table

    # -- 订阅 / 退订 ------------------------------------------------------
    def subscribe(self, conversation_id, snapshot=True):
        """为某段对话建立一条流；snapshot=True 时先投一条完整工具表。"""
        with self._lock:
            subscribers = self._channels.setdefault(conversation_id, [])
            sub = StreamSubscription(conversation_id)
            if snapshot:
                seq = self._next_seq(conversation_id)
                sub.put(make_event(TOOLS_SNAPSHOT, conversation_id, seq,
                                   {"tools": self._tools_builder()}))
            subscribers.append(sub)
            return sub

    def unsubscribe(self, subscription):
        """退订；该对话没有订阅者时回收频道（seq 计数器保留，保持单调）。"""
        with self._lock:
            cid = getattr(subscription, "conversation_id", None)
            subscribers = self._channels.get(cid)
            if subscribers and subscription in subscribers:
                subscribers.remove(subscription)
                if not subscribers:
                    self._channels.pop(cid, None)
        subscription.close()

    # -- 发布 -------------------------------------------------------------
    def emit(self, conversation_id, event_type, data=None):
        """向某段对话推一条事件。没有订阅者也照样生成（供回调消费），返回该事件。

        seq 在锁内分配；订阅者是该对话专属列表 —— 并发下事件**只追加不覆盖**，
        且**不会串到别的对话**。
        """
        with self._lock:
            seq = self._next_seq(conversation_id)
            event = make_event(event_type, conversation_id, seq, data)
            for sub in list(self._channels.get(conversation_id) or []):
                sub.put(event)
            return event

    def publish_tools(self, conversation_id=None):
        """工具表变化时主动再推一次完整工具表。

        conversation_id 为 None 时推给所有已建立的流；否则只推给该对话。
        """
        with self._lock:
            targets = ([conversation_id] if conversation_id is not None
                       else list(self._channels))
            events = []
            for cid in targets:
                subscribers = self._channels.get(cid)
                if not subscribers:
                    continue
                seq = self._next_seq(cid)
                event = make_event(TOOLS_SNAPSHOT, cid, seq,
                                   {"tools": self._tools_builder()})
                for sub in list(subscribers):
                    sub.put(event)
                events.append(event)
            return events

    # -- 生命周期 ---------------------------------------------------------
    def close(self, conversation_id=None):
        """关闭某段（或全部）对话的流，并唤醒所有阻塞中的消费者。"""
        with self._lock:
            if conversation_id is None:
                targets = list(self._channels)
            else:
                targets = [conversation_id]
            for cid in targets:
                for sub in self._channels.pop(cid, []) or []:
                    sub.close()

    def has_subscribers(self, conversation_id):
        with self._lock:
            return bool(self._channels.get(conversation_id))

    # -- 内部 -------------------------------------------------------------
    def _next_seq(self, conversation_id):
        seq = self._seq.get(conversation_id, 0) + 1
        self._seq[conversation_id] = seq
        return seq
