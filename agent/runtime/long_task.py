# -*- coding: utf-8 -*-
"""阶段 4（ag-longtask）· T4.1 / T4.2 / T4.3：长任务挂起与完成唤醒续跑。

「中断」不是用户取消，而是 **长任务完成后向循环层发信号**，让模型知道结果并继续
（接口文档 §2「中断」/ §5.5）：
  * 长任务发起后**不阻塞对话**：立即登记一个长任务，把运行状态经 `task_status`
    推给该对话的流，用户可继续说话（T4.1）；
  * 长任务到达终态时，把「已完成 + 结果摘要」注入循环，令模型**续写**，续写内容
    继续推到**同一条流**（`task_resumed` / `result_payload` / `message_delta`）（T4.2）；
  * Agent 重启后对残留长任务**去执行侧核对真实状态**，据结果进终态并释放占用，
    **不以本地记录自证**（T4.3）。

落点（与阶段 3 的约束一致）：
  * 长任务登记挂在对话上下文的 `context_json["long_tasks"]`（接口文档 §3.8 字段），
    进程重启后仍在同一 SQLite 里；
  * 续写复用 Runtime 的图与检查点：向同一 `thread_id` 注入一条「中断」消息，
    模型据对话历史续写 —— 这正是阶段 0 §6.5 建议的「内置 interrupt 语义」在
    本轮的可控近似（真实 Hadoop 侧未接入，完成/核对用可注入的假执行器模拟）。

双编号：`agent_task_id`（Agent 自生成，对话内引用）与 `hadoop_task_id`
（执行侧回传，供查询）。真实未接入时后者可能为空。
"""

import threading
import time

from . import stream as _stream
from .stream import RESULT_PAYLOAD, TASK_RESUMED, TASK_STATUS

# ---------------------------------------------------------------------------
# 状态机（接口文档 §3.8「状态」：排队 / 运行 / 成功 / 失败 / 已中止，可枚举）
# ---------------------------------------------------------------------------

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
ABORTED = "aborted"

#: 终态集合（进入后不再推进；登记/核对据此释放占用）
TERMINAL = (SUCCEEDED, FAILED, ABORTED)

STATUS_CN = {
    QUEUED: "排队",
    RUNNING: "运行",
    SUCCEEDED: "成功",
    FAILED: "失败",
    ABORTED: "已中止",
}

#: 工具 → 给人看的任务名（接口文档 §3.8「名称」：Agent 依据任务内容生成）
TOOL_TASK_NAMES = {
    "start_cleaning_task": "数据清洗与评分",
    "start_clean_task": "数据清洗",
    "score_task": "独立评分",
}


def is_long_running(tool_name):
    """是否长耗时工具（真值来源统一在 stream.LONG_RUNNING_TOOLS / 工具表）。"""
    return _stream.is_long_running(tool_name)


def estimated_seconds_for(tool_name):
    return _stream.estimated_seconds_for(tool_name)


def describe_task(tool_name, args):
    """由工具名与参数生成 (名称, 概括描述) —— 接口文档 §3.8 两类人话字段。"""
    args = dict(args or {})
    name = TOOL_TASK_NAMES.get(tool_name, tool_name)
    bits = []
    if tool_name == "score_task" and args.get("source"):
        bits.append("来源=%s" % args["source"])
    if args.get("exec_mode"):
        bits.append("模式=%s" % args["exec_mode"])
    if args.get("scope"):
        bits.append("范围=%s" % args["scope"])
    if args.get("tag"):
        bits.append("标签=%s" % args["tag"])
    if args.get("data_version"):
        bits.append("版本=%s" % args["data_version"])
    summary = name + ("（" + "，".join(bits) + "）" if bits else "")
    return name, summary


def new_record(agent_task_id, conversation_id, tool_name, args, envelope):
    """造一条长任务记录（挂对话上下文用；纯 dict，可 JSON 化）。"""
    name, summary = describe_task(tool_name, args)
    now = time.time()
    return {
        "agent_task_id": agent_task_id,
        "hadoop_task_id": (envelope or {}).get("task_id"),
        "conversation_id": conversation_id,
        "tool": tool_name,
        "args": dict(args or {}),
        "name": name,
        "summary": summary,
        "status": RUNNING,
        "stage": STATUS_CN[RUNNING],
        "progress_percent": 0,
        "estimated_seconds": estimated_seconds_for(tool_name),
        "created_at": now,
        "updated_at": now,
        "result": None,
        "released": False,
        "note": None,
    }


def task_view(record):
    """接口文档 §3.8「任务进展」的对外结构（双编号 / 名称 / 概括 / 状态 / 进度 / 时长）。"""
    if not record:
        return {}
    status = record.get("status")
    return {
        "agent_task_id": record.get("agent_task_id"),
        "hadoop_task_id": record.get("hadoop_task_id"),
        "name": record.get("name"),
        "summary": record.get("summary"),
        "status": status,
        "status_cn": STATUS_CN.get(status, status),
        "stage": record.get("stage"),
        "progress_percent": record.get("progress_percent", 0),
        "estimated_seconds": record.get("estimated_seconds"),
        "updated_at": record.get("updated_at"),
    }


def result_summary(result):
    """把结果信封压成一句「结果摘要」（中断注入用；数字原样带入，供数字护栏溯源）。"""
    result = dict(result or {})
    base = str(result.get("note")) if result.get("note") else "任务已结束"
    counts = result.get("counts") or {}
    bits = []
    for label, key in (("输入", "input_rows"), ("输出", "output_rows"),
                       ("隔离", "quarantined")):
        if key in counts:
            bits.append("%s %s" % (label, counts[key]))
    if bits:
        base = "%s；%s" % (base, "，".join(bits))
    return base


def interrupt_text(record, summary):
    """构造注入循环的「中断」消息（阶段 0 §6.5：完成即发中断，模型据此续写）。"""
    return (
        "【后台任务完成】你之前发起的长任务「%s」已到达终态：%s。\n"
        "任务编号：%s（Hadoop 编号：%s）。\n"
        "结果摘要：%s\n"
        "请基于以上真实结果，向用户继续说明结论与下一步；"
        "不要重复发起同一个任务，也不要编造工具未返回的数字。"
        % (record.get("name") or "长任务",
           STATUS_CN.get(record.get("status"), record.get("status")),
           record.get("agent_task_id"), record.get("hadoop_task_id") or "无",
           summary))


def result_payload(record, result):
    """接口文档 §3.9「结构化结果」：结果板块数据（不设优先级、一次给全）。"""
    result = dict(result or {})
    blocks = {"task_status": task_view(record)}
    mapping = (
        ("score_comparison", ("scores", "score_comparison")),
        ("data_volume", ("counts", "data_volume")),
        ("quarantine_samples", ("quarantine_samples", "isolated_samples")),
        ("report", ("report",)),
        ("limitations", ("limitations", "limitations_and_scope")),
    )
    for block, keys in mapping:
        for key in keys:
            if key in result:
                blocks[block] = result[key]
                break
    return {
        "ok": record.get("status") == SUCCEEDED,
        "task": task_view(record),
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# 长任务登记与唤醒（T4.1 / T4.2）
# ---------------------------------------------------------------------------

class LongTaskManager(object):
    """长任务登记、状态推进、完成唤醒与重启核对的中枢。

    * 登记：工具执行成功且属长耗时时调用 `register()`，记录写进对话上下文，
      并推一条 `task_status`。**不阻塞当轮**。
    * 完成：`complete()` 把任务置终态、推 `task_status` + `result_payload` +
      `task_resumed`，再令模型**续写**（`runtime.run_continuation`）。
    * 核对：`reconcile()` / `poll()` 用执行侧 `probe` 的**真实状态**决定终态，
      绝不拿本地记录自证。
    """

    def __init__(self, store, streams, runtime=None):
        self._store = store
        self._streams = streams
        self._runtime = runtime
        self._lock = threading.RLock()
        self._counter = self._max_existing_seq()

    def bind(self, runtime):
        """绑定 Runtime（续写要回调它跑一轮模型）。"""
        self._runtime = runtime

    def is_long_running(self, tool_name):
        """该工具是否长耗时（供执行出口装饰器判定）。"""
        return is_long_running(tool_name)

    # -- 登记（T4.1）------------------------------------------------------
    def register(self, conversation_id, tool_name, args, envelope):
        """登记一个长任务，返回任务句柄（接口文档 §3.8 视图）。立即返回、不阻塞。"""
        with self._lock:
            self._counter += 1
            agent_task_id = "LT-%04d" % self._counter
            record = new_record(agent_task_id, conversation_id, tool_name,
                                args, envelope)
            self._save(conversation_id, record)
        self._emit_status(conversation_id, record)
        return task_view(record)

    # -- 完成与唤醒（T4.2）------------------------------------------------
    def complete(self, conversation_id, agent_task_id, result=None,
                 status=SUCCEEDED, provider=None, facts=None):
        """把任务置终态并**唤醒续写**：中断注入 → 模型续写 → 同一条流。

        幂等：已终态的任务直接返回，不重复唤醒。
        """
        record = self._load_one(conversation_id, agent_task_id)
        if record is None:
            return None
        self.finalize(conversation_id, agent_task_id, status=status, result=result)
        record = self._load_one(conversation_id, agent_task_id)
        if self._runtime is None:
            return {"record": task_view(record), "resumed": False}
        self._streams.emit(conversation_id, TASK_RESUMED, {
            "agent_task_id": record.get("agent_task_id"),
            "hadoop_task_id": record.get("hadoop_task_id"),
            "status": record.get("status"),
            "result_summary": result_summary(result),
        })
        text = interrupt_text(record, result_summary(result))
        turn = self._runtime.run_continuation(
            conversation_id, text, meta=task_view(record),
            provider=provider, facts=facts)
        return {"record": task_view(record), "resumed": True, "turn": turn}

    # -- 进展更新 ---------------------------------------------------------
    def update_progress(self, conversation_id, agent_task_id, stage=None,
                        progress_percent=None, status=None):
        """推进阶段 / 进度 / 状态，并推一条 `task_status`（长任务运行期用）。"""
        with self._lock:
            record = self._load_one(conversation_id, agent_task_id)
            if record is None:
                return None
            if status:
                record["status"] = status
            if stage is not None:
                record["stage"] = stage
            elif status:
                record["stage"] = STATUS_CN.get(status, record.get("stage"))
            if progress_percent is not None:
                record["progress_percent"] = progress_percent
            record["updated_at"] = time.time()
            self._save(conversation_id, record)
        self._emit_status(conversation_id, record)
        return task_view(record)

    def finalize(self, conversation_id, agent_task_id, status, result=None,
                 stage=None, note=None):
        """进入终态：写入状态、释放占用、推 `task_status`（有结果再推 `result_payload`）。"""
        with self._lock:
            record = self._load_one(conversation_id, agent_task_id)
            if record is None:
                return None
            if record.get("status") in TERMINAL:
                return task_view(record)
            record["status"] = status
            record["stage"] = stage or STATUS_CN.get(status, status)
            record["progress_percent"] = (
                100 if status == SUCCEEDED else record.get("progress_percent", 0))
            record["result"] = result
            record["note"] = note
            record["released"] = True
            record["updated_at"] = time.time()
            self._save(conversation_id, record)
        self._emit_status(conversation_id, record)
        if result is not None:
            self._streams.emit(conversation_id, RESULT_PAYLOAD,
                               result_payload(record, result))
        return task_view(record)

    # -- 重启核对（T4.3）--------------------------------------------------
    def reconcile(self, probe):
        """重启后按**执行侧真实状态**核对残留长任务，据结果进明确终态并释放占用。

        probe(record) -> None（执行侧已无此任务）或
                         {"status": 排队/运行/成功/失败/已中止, "result": ..., ...}

        返回核对报告（每条给出 agent_task_id 与 action：succeeded / failed /
        aborted / follow）。
        """
        report = []
        for conv in self._store.list_conversations():
            cid = conv.get("id")
            for record in self._active_records(cid):
                aid = record.get("agent_task_id")
                seen = probe.probe(record)
                if seen is None:
                    self.finalize(cid, aid, status=ABORTED,
                                  stage="已中止",
                                  note="执行侧已无此任务（重启核对）")
                    report.append({"agent_task_id": aid, "action": ABORTED,
                                   "reason": "执行侧已无此任务"})
                    continue
                status = seen.get("status")
                if status in TERMINAL:
                    self.finalize(cid, aid, status=status,
                                  result=seen.get("result"),
                                  note="重启核对：执行侧回报 %s" % status)
                    report.append({"agent_task_id": aid, "action": status,
                                   "reason": "执行侧回报终态"})
                else:
                    self.update_progress(
                        cid, aid, status=status or RUNNING,
                        progress_percent=seen.get("progress_percent"),
                        stage=seen.get("stage"))
                    report.append({"agent_task_id": aid, "action": "follow",
                                   "reason": "执行侧仍在运行，恢复跟进"})
        return report

    def poll(self, probe, provider=None, facts=None):
        """周期巡检：执行侧已到终态的残留任务，就地完成并唤醒续写（T4.2 检测）。"""
        finished = []
        for conv in self._store.list_conversations():
            cid = conv.get("id")
            for record in self._active_records(cid):
                seen = probe.probe(record)
                if seen and seen.get("status") in TERMINAL:
                    finished.append(self.complete(
                        cid, record.get("agent_task_id"),
                        result=seen.get("result"), status=seen["status"],
                        provider=provider, facts=facts))
        return finished

    # -- 查询 -------------------------------------------------------------
    def tasks_of(self, conversation_id):
        return list(self._load_all(conversation_id).values())

    def get(self, conversation_id, agent_task_id):
        return self._load_one(conversation_id, agent_task_id)

    def active_tasks(self, conversation_id):
        """尚未终态（仍占用）的长任务。"""
        return [t for t in self.tasks_of(conversation_id)
                if t.get("status") not in TERMINAL]

    # -- 内部：持久化 -----------------------------------------------------
    def _load_all(self, conversation_id):
        conv = self._store.get_conversation(conversation_id)
        if not conv:
            return {}
        return dict((conv.get("context") or {}).get("long_tasks") or {})

    def _load_one(self, conversation_id, agent_task_id):
        return self._load_all(conversation_id).get(agent_task_id)

    def _save(self, conversation_id, record):
        with self._lock:
            tasks = self._load_all(conversation_id)
            tasks[record["agent_task_id"]] = record
            self._store.update_context(conversation_id, {"long_tasks": tasks})

    def _active_records(self, conversation_id):
        return self.active_tasks(conversation_id)

    def _emit_status(self, conversation_id, record):
        return self._streams.emit(conversation_id, TASK_STATUS, task_view(record))

    def _max_existing_seq(self):
        top = 0
        try:
            conversations = self._store.list_conversations()
        except Exception:
            return 0
        for conv in conversations:
            tasks = (conv.get("context") or {}).get("long_tasks") or {}
            for record in tasks.values():
                aid = str(record.get("agent_task_id") or "")
                if aid.startswith("LT-"):
                    try:
                        top = max(top, int(aid[3:]))
                    except ValueError:
                        continue
        return top


# ---------------------------------------------------------------------------
# 可控假执行器（模拟执行侧 = Hadoop / MCP；本阶段真实侧未接入）
# ---------------------------------------------------------------------------

class FakeTaskExecutor(object):
    """可控假执行器：模拟执行侧的任务状态，并实现核对接口 `probe(record)`。

    用法（测试里控制完成时序）：
        ex = FakeTaskExecutor()
        ex.start(record)                       # 执行侧开始跑
        ex.complete(record, result={...})      # 执行侧跑到成功
        ex.fail(record, "…")                    # 执行侧跑到失败
        ex.vanish(record)                       # 执行侧已无此任务
        ex.probe(record) -> 真实状态 dict 或 None
    """

    def __init__(self):
        self._state = {}
        self._lock = threading.RLock()

    def _key(self, record):
        return (record or {}).get("hadoop_task_id") or (record or {}).get("agent_task_id")

    def start(self, record):
        with self._lock:
            self._state[self._key(record)] = {
                "status": RUNNING, "progress_percent": 0,
                "stage": STATUS_CN[RUNNING], "result": None}
        return self._key(record)

    def complete(self, record, result=None, progress_percent=100):
        with self._lock:
            self._state[self._key(record)] = {
                "status": SUCCEEDED, "progress_percent": progress_percent,
                "stage": STATUS_CN[SUCCEEDED], "result": result}
        return self._key(record)

    def fail(self, record, message=None, result=None):
        with self._lock:
            self._state[self._key(record)] = {
                "status": FAILED, "progress_percent": 100,
                "stage": STATUS_CN[FAILED], "result": result,
                "message": message}
        return self._key(record)

    def vanish(self, record):
        with self._lock:
            self._state.pop(self._key(record), None)

    def probe(self, record):
        """执行侧核对：返回真实状态；查询不到（已消失）返回 None。"""
        with self._lock:
            state = self._state.get(self._key(record))
            return dict(state) if state else None
