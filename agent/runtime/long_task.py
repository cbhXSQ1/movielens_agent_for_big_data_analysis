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

from ..constants import DIM_CN, DIMS
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

#: 后台进度轮询周期（秒）——集中配置，便于按部署环境调整。
POLL_INTERVAL_SECONDS = 2.0

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
    view = {
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
    # 加性字段：执行侧给出阶段计数时一并带出（缺失不臆造）。
    if record.get("stage_index") is not None:
        view["stage_index"] = record.get("stage_index")
    if record.get("stage_total") is not None:
        view["stage_total"] = record.get("stage_total")
    return view


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


# ---------------------------------------------------------------------------
# 无模型时的确定性完成说明（长任务完成但拿不到可用模型时优雅收尾用）
# ---------------------------------------------------------------------------

def _num_text(value):
    """把数字格式化成给人看的样子；非数字返回 None（取不到就不写）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else "%.2f" % value
    return None


def _sum_numbers(mapping):
    """把一层字典里的数字求和（取不到任何数字返回 None）。"""
    if not isinstance(mapping, dict):
        return None
    total = 0
    found = False
    for value in mapping.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            total += value
            found = True
    return total if found else None


def _volume(counts):
    """从结果的 `counts` 里取 (输入, 输出, 隔离) —— 兼容两种真实形状，取不到为 None。"""
    if not isinstance(counts, dict):
        return (None, None, None)
    if any(key in counts for key in ("input_rows", "output_rows", "quarantined")):
        return (counts.get("input_rows"), counts.get("output_rows"),
                counts.get("quarantined"))
    quarantine = counts.get("quarantine")
    total = quarantine.get("total") if isinstance(quarantine, dict) else None
    return (_sum_numbers(counts.get("input")), _sum_numbers(counts.get("output")),
            total)


def _score_text(scores):
    """把结果里的 `scores.after` 拼成「评分：准确性 …、综合 …」；取不到返回 None。"""
    if not isinstance(scores, dict):
        return None
    after = scores.get("after")
    if not isinstance(after, dict):
        return None
    bits = []
    for dim in DIMS:
        text = _num_text(after.get(dim))
        if text is not None:
            bits.append("%s %s" % (DIM_CN.get(dim, dim), text))
    composite = _num_text(after.get("composite"))
    if composite is not None:
        bits.append("综合 %s" % composite)
    if not bits:
        return None
    return "评分：" + "、".join(bits)


def completion_message(view, result=None):
    """无模型时的**确定性完成说明**：只用长任务视图与 `result.json` 的真实字段。

    - 名称 / 状态取自任务视图（`name` / `status_cn` / `status`）；
    - 数据量 / 五维分取自 `result`（即任务目录下的 `result.json`）；
    - 取不到的字段一律**不写**，绝不编造（沿用结果解释层铁律）。
    """
    view = view or {}
    result = result if isinstance(result, dict) else None
    name = view.get("name") or "长任务"
    status = view.get("status")
    status_cn = (view.get("status_cn") or STATUS_CN.get(status) or status
                 or "已结束")
    prefix = "后台任务已完成" if status == SUCCEEDED else "后台任务已结束"
    parts = ["%s：%s，状态%s" % (prefix, name, status_cn)]
    if result:
        inp, out, quar = _volume(result.get("counts"))
        bits = []
        for label, value in (("输入", inp), ("输出", out), ("隔离", quar)):
            text = _num_text(value)
            if text is not None:
                bits.append("%s %s 行" % (label, text))
        if bits:
            parts.append("数据量：" + "，".join(bits))
        score = _score_text(result.get("scores"))
        if score:
            parts.append(score)
        note = result.get("note")
        if note:
            parts.append(str(note))
    return "；".join(parts) + "。"


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

    def __init__(self, store, streams, runtime=None, probe=None,
                 poll_interval=POLL_INTERVAL_SECONDS):
        self._store = store
        self._streams = streams
        self._runtime = runtime
        self._lock = threading.RLock()
        self._counter = self._max_existing_seq()
        # 后台进度轮询：探针可注入（真实运行用 DriverTaskProbe；测试用假探针）。
        self._poller = TaskPoller(self, probe=probe, interval=poll_interval)

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
        # 登记成功后确保后台轮询在跑（拿到 hadoop_task_id 才有意义）。
        if record.get("hadoop_task_id"):
            self._poller.ensure_running()
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

    def sync_progress(self, conversation_id, agent_task_id, seen):
        """把执行侧探测到的状态**有变化才**映射进记录并推 `task_status`。

        `seen` 形如 `{"status","stage","progress_percent","stage_index",
        "stage_total",...}`（来自探针）；只写真实返回的字段，未返回的保持原值。
        已终态 / 不存在的记录直接忽略（幂等）。返回更新后的视图或 None。
        """
        seen = dict(seen or {})
        with self._lock:
            record = self._load_one(conversation_id, agent_task_id)
            if record is None or record.get("status") in TERMINAL:
                return None
            changed = False
            status = seen.get("status")
            if status and record.get("status") != status:
                record["status"] = status
                changed = True
            stage = seen.get("stage")
            if stage is not None and record.get("stage") != stage:
                record["stage"] = stage
                changed = True
            progress = seen.get("progress_percent")
            if progress is not None and record.get("progress_percent") != progress:
                record["progress_percent"] = progress
                changed = True
            for key in ("stage_index", "stage_total"):
                value = seen.get(key)
                if value is not None and record.get(key) != value:
                    record[key] = value
                    changed = True
            if not changed:
                return task_view(record)
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
        # 到达终态后再收集一次产物：此刻报告 / 指标 / 清洗文件等已落盘，
        # 新增的推 `artifact_added` 并持久化（续写路径会再收集一次，只补新增）。
        self._collect_artifacts(conversation_id)
        return task_view(record)

    def _collect_artifacts(self, conversation_id):
        """到达终态时收集产物（复用 Runtime 的回合收尾逻辑；无运行时则跳过）。"""
        runtime = self._runtime
        if runtime is None:
            return

        def publish(event_type, **data):
            return self._streams.emit(conversation_id, event_type, data)

        try:
            runtime._finalize_turn(conversation_id, {}, publish)
        except Exception:
            pass

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
        """周期巡检：同步活跃任务进度；执行侧已到终态的就地完成并唤醒续写（T4.2）。

        对**活跃**任务：先用探针取执行侧真实状态；
          * 到终态（成功/失败/已中止）→ 走既有完成路径 `complete()`（finalize +
            续写 + `result_payload`），并把完成结果收进返回列表；
          * 仍在运行 → `sync_progress()` 映射状态/阶段/进度，**有变化才**推
            `task_status`（无变化不打扰该对话）。
        探针异常 / 查不到（None）一律跳过，绝不让单条任务拖垮巡检。
        """
        finished = []
        for conv in self._store.list_conversations():
            cid = conv.get("id")
            for record in self._active_records(cid):
                seen = self._probe_one(probe, record)
                if not seen:
                    continue
                status = seen.get("status")
                if status in TERMINAL:
                    finished.append(self.complete(
                        cid, record.get("agent_task_id"),
                        result=seen.get("result"), status=status,
                        provider=provider, facts=facts))
                else:
                    self.sync_progress(cid, record.get("agent_task_id"), seen)
        return finished

    def _probe_one(self, probe, record):
        """安全调用探针：任何异常都吞掉返回 None（单条失败不影响整体巡检）。"""
        try:
            return probe.probe(record)
        except Exception:
            return None

    def has_active_tasks(self):
        """全部对话里是否还有未终态的长任务（后台轮询的停止条件）。"""
        try:
            for conv in self._store.list_conversations():
                if self.active_tasks(conv.get("id")):
                    return True
        except Exception:
            return False
        return False

    # -- 后台轮询（进度同步）---------------------------------------------
    def configure_polling(self, probe=None, interval=None, provider=None,
                          facts=None):
        """配置后台轮询探针（真实运行用 `default_probe`；测试注入假探针）。"""
        self._poller.configure(probe, interval=interval, provider=provider,
                               facts=facts)
        if probe is not None:
            self._poller.ensure_running()
        return self._poller

    def stop_polling(self):
        """停止后台轮询（进程收尾用）。"""
        self._poller.stop()

    @property
    def poller(self):
        return self._poller

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


# ---------------------------------------------------------------------------
# 真实探针 + 后台进度轮询（问题：长任务进度一直 0% / 终态不反映）
# ---------------------------------------------------------------------------

class DriverTaskProbe(object):
    """真实探针：用 `driver` 的 `status`（必要时补 `result`）查执行侧状态。

    与 `FakeTaskExecutor.probe` 同形状（`probe(record) -> dict | None`），故可在
    `LongTaskManager.poll(probe)` / 后台轮询里无缝替换。只做**只读查询**：
      * 调 `agent.tools.get_task_status(hadoop_task_id)`；
      * 成功任务再调 `get_task_result(hadoop_task_id)` 取权威结果，供完成路径
        走 `result_payload`（失败/未完成时 result 为 None，如实反映）；
      * 传输异常 / 任务不存在（信封 `ok=false`）一律返回 None，绝不抛。
    """

    def probe(self, record):
        hadoop_id = (record or {}).get("hadoop_task_id")
        if not hadoop_id:
            return None
        try:
            from .. import tools
            envelope = tools.get_task_status(hadoop_id)
        except Exception:
            return None
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            return None
        seen = {
            "status": envelope.get("status"),
            "stage": envelope.get("stage"),
            "progress_percent": envelope.get("progress_percent"),
            "stage_index": envelope.get("stage_index"),
            "stage_total": envelope.get("stage_total"),
            "message": envelope.get("message"),
            "errors": envelope.get("errors"),
        }
        if seen["status"] == SUCCEEDED:
            seen["result"] = self._result(hadoop_id)
        return seen

    def _result(self, hadoop_id):
        """成功任务取权威结果信封；拿不到就返回 None（不臆造）。"""
        try:
            from .. import tools
            envelope = tools.get_task_result(hadoop_id)
        except Exception:
            return None
        if isinstance(envelope, dict) and envelope.get("ok"):
            return envelope
        return None


#: 默认探针实例：真实运行用（生产 `Runtime` 经 `_get_runtime` 注入它）。
default_probe = DriverTaskProbe()


class TaskPoller(object):
    """后台进度轮询：周期把活跃长任务的执行侧状态同步进记录并推 `task_status`。

    分工：
      * **真实运行** —— 探针是 `default_probe`（driver），终态经 `complete()`
        走既有完成路径（finalize / 续写 / `result_payload`）；
      * **测试** —— 注入假探针（与 `FakeTaskExecutor.probe` 同形状），不触网。

    线程安全：记录更新在 `LongTaskManager` 锁内；事件走线程安全的 `StreamHub`。
    停止条件：无活跃任务即退出；`stop()` 可主动停止。异常全部兜住，绝不因轮询
    把服务搞崩。
    """

    def __init__(self, manager, probe=None, interval=POLL_INTERVAL_SECONDS,
                 provider=None, facts=None):
        self._manager = manager
        self._probe = probe
        self._interval = max(0.0, float(interval))
        self._provider = provider
        self._facts = facts
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def configure(self, probe=None, interval=None, provider=None, facts=None):
        """更新探针 / 周期 / 续写参数（None 表示维持原值）。"""
        with self._lock:
            if probe is not None:
                self._probe = probe
            if interval is not None:
                self._interval = max(0.0, float(interval))
            if provider is not None:
                self._provider = provider
            if facts is not None:
                self._facts = facts

    @property
    def running(self):
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    @property
    def interval(self):
        return self._interval

    def ensure_running(self):
        """确保后台轮询在跑（有探针且有活跃任务才起）；返回是否**新起**了线程。"""
        if self._probe is None:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if not self._manager.has_active_tasks():
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="long-task-poller", daemon=True)
            self._thread.start()
            return True

    def tick(self):
        """跑一轮巡检（同步；供测试与立即探测），返回本轮完成的任务列表。"""
        if self._probe is None:
            return []
        return self._manager.poll(self._probe, provider=self._provider,
                                  facts=self._facts)

    def _loop(self):
        """轮询主体：立即探测一次，再按周期巡检；无活跃任务 / 被停止即退出。"""
        while not self._stop.is_set():
            try:
                if not self._manager.has_active_tasks():
                    break
                self.tick()
            except Exception:                              # pragma: no cover
                pass
            if not self._manager.has_active_tasks():
                break
            if self._stop.wait(self._interval):
                break

    def stop(self):
        """请求停止轮询（不阻塞等待线程结束；守护线程随进程退出）。"""
        self._stop.set()
