# -*- coding: utf-8 -*-
"""mcpserver/tasks.py —— 长任务这一段：等待、取消、占用状态、能力自描述、健康检查。

对应实现方案 §3.3 / §7 / §8：要求文档点名要「暴露锁与队列状态」「支持取消」「完成能被唤醒」，
但没有把这几件事单列成工具，本模块把它们落成五个处理器：

  * `wait_for_task`       —— 阻塞等待，挂到终态或等到上限；超时如实回「仍在运行」，不算失败；
  * `cancel_task`         —— 真正按进程组发终止信号，确认停住后把状态落成「已中止」；
  * `get_scheduler_state` —— 现在有没有任务占用执行位、占用者是谁、怎么解除；
  * `describe_capabilities` —— 能力自描述（对端工具表的权威来源）+ 真正可调用的清单；
  * `health_check`        —— 探活与版本，**不触发任何作业链**。

三条必须守住的东西：
  1. **不假装中止成功。** 没找到执行进程、信号发出去了但进程没退、或者任务本来就已经结束，
     都照实说；状态与占用只有在**确认进程确实不在了**之后才改。而这个「确认」只有两条路：
     进程表核实到它已经退出，或者任务目录里有「经本服务提交」的凭据、且按任务编号查不到
     任何执行进程（见 `_submitted_by_service`）。两条都不成立就一律不动。
  2. **不编数字。** 预计耗时、进度、计数一律取自状态文件与状态常量；拿不到就不给这个字段。
  3. **占用判定只有一处实现。** 复用 `driver.run_task.running_task()`，本模块不另写一套。

为什么本模块**会**导入 driver 执行层（而发起类处理器只走子进程桥接）：这里要读/写的是
driver 的**状态文件**，`running_task()` / `read_status()` / `write_status()` 就是它唯一的
实现处；改用子进程要新增子命令（本轮不允许），重写一份又会和 driver 的判定漂移。
所以这里是**复用**，不是把业务逻辑搬进来 —— 清洗、评分、发布照旧全在 driver 里。

处理器签名与约定（见 `dispatch.py` 文件头）：`handler(args, ctx) -> dict`，
成功只回业务字段，失败抛 `contract.StructuredError`。本模块只用标准库。

可调项（`ctx.config` 优先，其次环境变量，越界会夹到安全区间）：
  `wait_poll_seconds`        轮询间隔，默认 0.5 秒；
  `cancel_grace_seconds`     温和终止后等多久转强制，默认 6 秒；
  `cancel_kill_wait_seconds` 强制终止后等多久确认，默认 5 秒；
  `scheduler_recent_limit`   占用状态里附带几条最近任务，默认 5 条。
"""

import datetime
import io
import json
import os
import signal
import sys
import threading
import time

from . import contract as _contract
from .contract import (
    CODE_CANCEL_FAILED,
    CODE_INTERNAL_ERROR,
    CODE_TASK_NOT_FOUND,
    DRIVER_INTERFACE_VERSION,
    INTERFACE_VERSION,
    STATUS_ABORTED,
    STATUS_LABELS,
    STATUS_QUEUED,
    TERMINAL_STATUSES,
    StructuredError,
)
from . import dispatch as _dispatch
from .tools import TOOLS, describe_capabilities as build_capabilities

# ---------------------------------------------------------------------------
# 常量与可调参数
# ---------------------------------------------------------------------------

#: 中止请求标记文件名。**唯一来源是契约层**（`contract.CONTROL_FILE_CANCEL`）：
#: 这里只做别名、不写字面量 —— 执行侧常量、写入方、读取方的排除名单都由同一条来源
#: 派生，改名时只改契约一处；测试里有一道**跨模块断言**锁住三者一致，避免出现
#: 「路径跟着变了、回报里的名字没变」这种改了一半的情况。
CANCEL_MARKER_FILENAME = _contract.CONTROL_FILE_CANCEL

#: 「本次任务由本服务提交」的标记文件名，同样引自契约层（`contract.CONTROL_FILE_SUBMIT`）。
#: 由发起类处理器在受理成功时写入；本模块**只按文件存在与否判断，不读它的任何字段** ——
#: 标记是别人写的，字段名以它实际落地为准，硬依赖某个字段名的话，两边一旦对不上
#: 这条判定就会静默失效。
SUBMIT_MARKER_FILENAME = _contract.CONTROL_FILE_SUBMIT

#: 轮询间隔默认值（秒）。短但要停一下，不能空转把 CPU 吃满。
DEFAULT_WAIT_POLL_SECONDS = 0.5
#: 轮询间隔的允许区间：太短等于空转，太长会拖慢「一进终态就返回」的体验。
MIN_WAIT_POLL_SECONDS = 0.05
MAX_WAIT_POLL_SECONDS = 5.0

#: 温和终止（SIGTERM）之后留多少秒给它自己收尾，再走强制终止。
DEFAULT_CANCEL_GRACE_SECONDS = 6.0
#: 强制终止（SIGKILL）之后再等多少秒确认进程真的没了。
DEFAULT_CANCEL_KILL_WAIT_SECONDS = 5.0
#: 进程是否还在的探测间隔（秒）。
PROCESS_POLL_SECONDS = 0.1

#: **单次等待的时间上限（秒）**：不超过 30 分钟。
#: 为什么收窄：等待是同步处理函数，跑在有限的工作线程池里，一次挂两小时就等于
#: 长期占着一个线程。改成三十分钟既能覆盖正常作业（全量约十七分钟），
#: 又不至于让一次误操作把线程占住半天；超出上限的请求按上限执行并在回答里注明。
MAX_WAIT_SECONDS = 1800.0

#: 等待的**缺省**上限（秒）。必须与工具表为 `wait_for_task.timeout_seconds`
#: 公布的默认值一致 —— 对端看到的是表里的默认值，处理器内部另用一套就是不诚实；
#: 测试里有一条断言直接比对工具表，改一边忘另一边会立刻红。
DEFAULT_WAIT_SECONDS = 300.0

#: **同时进行的等待数上限**。等待期间线程一直被占着，不设上限的话几十个长等待
#: 就能把工作线程吃干净，表现为「探活正常、但其它能力都调不动」。超出的调用
#: 立刻回一个明确的结构化错误，而不是排队等一个线程。
DEFAULT_MAX_CONCURRENT_WAITS = 4

#: 排队/残留分支的「确认没有执行进程」复核窗口（秒）。后台进程被拉起有延迟
#: （机器忙、解释器启动慢），只扫一次就下结论太脆；在一个小窗口里多扫几次。
PROCESS_SETTLE_SECONDS = 1.0

#: 取消请求的原因文本。取消能力对外**没有**「原因」参数（工具表里没有它，
#: 分派层会先把未知参数拒掉），所以这里用固定文案，不做那个永远走不到的分支。
CANCEL_REASON = "调用方请求中止"

#: 等待数超限时用的结构化错误码。**直接引用契约层的登记**，不在本模块自己兜底：
#: 对外公布的错误码集合以契约为准，各写一份迟早会出现「对端拿到一个清单上没有的码」。
CODE_WAIT_LIMIT_REACHED = _contract.CODE_WAIT_LIMIT_REACHED

#: 等待中的调用计数（模块级、跨会话共享）。**只在加减时短暂持锁**，
#: 等待循环本身绝不持锁，否则限流的本意（别把线程占死）反而会变成串行化。
_WAIT_LOCK = threading.Lock()
_WAIT_ACTIVE = [0]

#: 最近任务摘要默认回几条。
DEFAULT_RECENT_LIMIT = 5

#: 服务名默认值（服务层可通过 `ctx.config["name"]` 覆盖）。
DEFAULT_SERVICE_NAME = "hadoop-mcp"

#: /proc 目录；没有它就没法按进程表定位，取消会如实报「找不到」而不是瞎猜。
PROC_ROOT = "/proc"

#: driver 执行层模块缓存（导入一次，之后复用；导入本身也是健康检查的一项探测）。
_driver_module = None


# ---------------------------------------------------------------------------
# 基础设施：仓库根、driver 执行层、可调参数
# ---------------------------------------------------------------------------

def _repo_root(ctx=None):
    """仓库根。优先用服务层给的 `ctx.repo_root`，没有就按本文件位置推算。"""
    root = getattr(ctx, "repo_root", None)
    if root:
        return root
    here = os.path.dirname(os.path.abspath(__file__))      # <仓库>/hadoop/mcpserver
    return os.path.dirname(os.path.dirname(here))


def _driver(ctx=None):
    """按需加载 driver 执行层（`driver.run_task`）。

    为什么延迟导入：`driver.run_task` 会把 `hadoop/` 塞进导入路径并拉起 engine 解析链，
    能力自描述、健康检查这类纯查询不该在进程启动时就被它拖住；同时它也是
    「driver 是否可用」这项探测的真实内容。
    """
    global _driver_module
    if _driver_module is not None:
        return _driver_module
    hadoop_root = os.path.join(_repo_root(ctx), "hadoop")
    if hadoop_root not in sys.path:
        sys.path.insert(0, hadoop_root)
    from driver import run_task                        # 局部导入：见上面的理由
    _driver_module = run_task
    return run_task


def _driver_script(ctx=None):
    """driver 入口脚本的**相对路径**（对外不暴露机器上的绝对位置）。"""
    return os.path.join("hadoop", "driver", "run_task.py")


def _ensure_var_dir(ctx=None):
    """把进程内的任务目录对齐成 driver 子进程看到的那一个。

    driver 的 `var_dir()/task_dir()` 只认**进程环境**里的 `ML_VAR_DIR`。服务层若是从
    `hadoop/scripts/env.sh` 解析出取值放进 `ctx.env`（子进程那边照此设置），而进程
    环境里没有这一项，本模块就会去查另一个目录 —— 「本端查 A、driver 写 B」是最难查
    的一类错位。所以调用 driver 之前先把上下文里的取值补进进程环境：
    **只补空缺、不覆盖外部显式给的取值**（运维改了变量，本端必须跟着改）。
    """
    if os.environ.get("ML_VAR_DIR"):
        return os.environ["ML_VAR_DIR"]
    value = None
    env_map = getattr(ctx, "env", None)
    if isinstance(env_map, dict) and env_map.get("ML_VAR_DIR"):
        value = str(env_map["ML_VAR_DIR"])
    if not value:
        try:
            # 兄弟模块负责解析 env.sh；万一它改了接口，这里也只会回落到 driver 的默认目录
            from .driver_bridge import read_env_sh, repo_root_of
            value = read_env_sh(repo_root_of(ctx)).get("ML_VAR_DIR")
        except Exception:                     # noqa: BLE001 —— 取不到就用 driver 自己的默认值
            value = None
    if value:
        os.environ["ML_VAR_DIR"] = value
    return value


def _tuning_number(ctx, key, env_name, default, minimum, maximum):
    """取一个可调数值：config 优先，其次环境变量，最后默认值；越界就夹到区间内。

    配置写错不该让工具挂掉，但也**不能静默改成默认值装作没事** —— 夹到区间是
    「照配置的意思做，只是不许超出安全范围」，这是唯一不做如实报错的取舍。
    """
    value = None
    config = getattr(ctx, "config", None)
    if isinstance(config, dict):
        value = config.get(key)
    if value is None:
        value = os.environ.get(env_name)
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _poll_seconds(ctx=None):
    return _tuning_number(ctx, "wait_poll_seconds", "MCP_WAIT_POLL_SECONDS",
                          DEFAULT_WAIT_POLL_SECONDS,
                          MIN_WAIT_POLL_SECONDS, MAX_WAIT_POLL_SECONDS)


def _cancel_grace_seconds(ctx=None):
    return _tuning_number(ctx, "cancel_grace_seconds", "MCP_CANCEL_GRACE_SECONDS",
                          DEFAULT_CANCEL_GRACE_SECONDS, 0.0, 60.0)


def _cancel_kill_wait_seconds(ctx=None):
    return _tuning_number(ctx, "cancel_kill_wait_seconds", "MCP_CANCEL_KILL_WAIT_SECONDS",
                          DEFAULT_CANCEL_KILL_WAIT_SECONDS, 0.0, 60.0)


def _recent_limit(ctx=None):
    return int(_tuning_number(ctx, "scheduler_recent_limit", "MCP_SCHEDULER_RECENT_LIMIT",
                              DEFAULT_RECENT_LIMIT, 1, 50))


def _max_concurrent_waits(ctx=None):
    """同时允许几个等待挂着（至少一个，否则等待能力直接不可用）。"""
    return int(_tuning_number(ctx, "max_concurrent_waits",
                              "MCP_MAX_CONCURRENT_WAITS",
                              DEFAULT_MAX_CONCURRENT_WAITS, 1, 64))


# ---------------------------------------------------------------------------
# 等待名额：把「长等待占住工作线程」这件事挡在入口
# ---------------------------------------------------------------------------

def _acquire_wait_slot(limit):
    """占用一个等待名额；占不到返回 False（调用方立刻回明确的错误，不排队）。"""
    with _WAIT_LOCK:
        if _WAIT_ACTIVE[0] >= limit:
            return False
        _WAIT_ACTIVE[0] += 1
        return True


def _release_wait_slot():
    """归还等待名额；只在确实占用过的时候减，防止异常路径把计数减成负数。"""
    with _WAIT_LOCK:
        if _WAIT_ACTIVE[0] > 0:
            _WAIT_ACTIVE[0] -= 1


def _active_wait_count():
    """当前有几个等待在挂着（测试与排障用；也回给等待数超限的错误信息）。"""
    with _WAIT_LOCK:
        return _WAIT_ACTIVE[0]



# ---------------------------------------------------------------------------
# 状态读取与摘要
# ---------------------------------------------------------------------------

def _read_status(rt, tid):
    """读任务状态；不存在就透传 TASK_NOT_FOUND，读坏了如实报内部错误（不猜内容）。"""
    try:
        return rt.read_status(tid)
    except rt.CliError as exc:
        if getattr(exc, "code", "") == CODE_TASK_NOT_FOUND:
            raise StructuredError(
                CODE_TASK_NOT_FOUND,
                "任务不存在：%s（编号可能写错，或该任务的目录已被清理）" % tid,
                task_id=tid)
        raise StructuredError(CODE_INTERNAL_ERROR,
                              "读取任务状态失败：%s" % exc.message, task_id=tid)
    except ValueError as exc:                     # json 解析失败 = status.json 被写坏了
        raise StructuredError(
            CODE_INTERNAL_ERROR,
            "任务状态文件无法解析（%s）：%s" % (type(exc).__name__, exc), task_id=tid)
    except (IOError, OSError) as exc:
        raise StructuredError(CODE_INTERNAL_ERROR,
                             "读取任务状态文件出错：%s" % exc, task_id=tid)


def _status_label(status):
    return STATUS_LABELS.get(status, "")


def _seconds_text(seconds):
    """把秒数写成给人读的短串（整数就不带小数点）。"""
    if float(seconds).is_integer():
        return "%d" % int(seconds)
    return "%.1f" % seconds


def _status_view(status):
    """状态文件的对外摘要：只取真实存在的字段，缺的就不给（不填占位）。"""
    view = {
        "task_id": status.get("task_id"),
        "status": status.get("status"),
        "status_label": _status_label(status.get("status")),
        "stage": status.get("stage"),
        "stage_index": status.get("stage_index"),
        "stage_total": status.get("stage_total"),
        "progress_percent": status.get("progress_percent"),
        "started_at": status.get("started_at"),
        "updated_at": status.get("updated_at"),
        "message": status.get("message", ""),
    }
    for key in ("task_type", "scope", "exec_mode", "data_version", "tag", "source",
                "from_task", "finished_at", "aborted_at", "abort_reason"):
        if status.get(key) not in (None, ""):
            view[key] = status[key]
    return view


def _stage_list_for(rt, status):
    """按任务类型取阶段序列，保证写状态后的进度百分比仍按它自己的序列算。"""
    task_type = status.get("task_type") or "full"
    if task_type == "clean":
        return rt.CLEAN_STAGES
    if task_type == "score":
        return rt.SCORE_STAGES
    return rt.STAGES


def _elapsed_seconds(started_at):
    """按状态文件里的开始时间算已过多少秒；格式不认识就回 None（不猜）。"""
    if not started_at:
        return None
    try:
        started = datetime.datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    started = started.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - started
    return max(0.0, round(delta.total_seconds(), 1))


# ---------------------------------------------------------------------------
# 进程定位与终止
# ---------------------------------------------------------------------------

def _read_cmdline(pid):
    """读一个进程的完整命令行（参数列表原样，不用 shell 拼串）。"""
    try:
        with io.open(os.path.join(PROC_ROOT, str(pid), "cmdline"), "rb") as fh:
            raw = fh.read()
    except (IOError, OSError):
        return None
    if not raw:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part]


def _matches_driver_run(argv, tid):
    """命令行是否正是本 driver 的 `_run` 后台子进程。

    三个条件**同时**成立才算数，宁可漏杀也不误杀别的进程：
      1. 解释器名字里有 python —— 排除 `grep`、`tail` 之类命令行里恰好带同样字样的进程；
      2. 参数里既有 `run_task.py` 也有 `_run` 子命令 —— 确认是后台执行入口；
      3. `--task-id` 的取值正是本任务编号 —— 确认是这个任务。

    任务目录里没有记录进程号，所以只能这样按命令行反查；查到之后还要在
    `_terminate` 里核对进程组，才动手发信号。
    """
    if not argv:
        return False
    if "python" not in os.path.basename(argv[0]).lower():
        return False
    if "run_task.py" not in [os.path.basename(token) for token in argv]:
        return False
    if "_run" not in argv:
        return False
    for index, token in enumerate(argv):
        if token == "--task-id" and index + 1 < len(argv) and argv[index + 1] == tid:
            return True
        if token == "--task-id=%s" % tid:
            return True
    return False


def _find_driver_processes(tid):
    """扫描进程表，找出该任务的后台执行进程（可能不止一个，返回按进程号排序）。"""
    if not os.path.isdir(PROC_ROOT):
        return []
    try:
        names = os.listdir(PROC_ROOT)
    except OSError:
        return []
    found = []
    me = os.getpid()
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == me:
            continue
        if _matches_driver_run(_read_cmdline(pid), tid):
            found.append(pid)
    return sorted(found)


def _pid_alive(pid):
    """进程是否还活着；僵尸（已退出未回收）算死 —— 它已经不会再写任何状态。"""
    if not os.path.isdir(PROC_ROOT):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        with io.open(os.path.join(PROC_ROOT, str(pid), "stat"), "r",
                     errors="replace") as fh:
            text = fh.read()
    except (IOError, OSError):
        return False
    # 第 3 个字段是进程状态；命令名里可能带空格与括号，先截掉 `)` 之前的部分。
    fields = text[text.rfind(")") + 1:].split()
    return bool(fields) and fields[0] not in ("Z", "X", "x")


def _signal_name(sig):
    try:
        return signal.Signals(sig).name
    except (ValueError, AttributeError):
        return str(sig)


def _terminate(pids, sig, tid):
    """发终止信号：优先整组终止（后台子进程是独立进程组），并避开本服务自己的组。"""
    sent = []
    my_group = os.getpgrp()
    seen_groups = set()
    for pid in pids:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = None
        if pgid and pgid != my_group:
            if pgid in seen_groups:
                continue          # 同一组前面已经整组发过，不重复发
            seen_groups.add(pgid)
            try:
                os.killpg(pgid, sig)
                sent.append({"pid": pid, "target": "process_group", "pgid": pgid,
                             "signal": _signal_name(sig)})
                continue
            except ProcessLookupError:
                continue
            except OSError as exc:
                raise StructuredError(
                    CODE_CANCEL_FAILED,
                    "按进程组发送 %s 失败（组 %s）：%s" % (_signal_name(sig), pgid, exc),
                    task_id=tid, pids=list(pids))
        # 退化路径：组号与本服务同组或读不到时，只针对该进程，免得连累自己不相关的进程。
        try:
            os.kill(pid, sig)
            sent.append({"pid": pid, "target": "process",
                         "signal": _signal_name(sig)})
        except ProcessLookupError:
            continue
        except OSError as exc:
            raise StructuredError(
                CODE_CANCEL_FAILED,
                "向进程 %s 发送 %s 失败：%s" % (pid, _signal_name(sig), exc),
                task_id=tid, pids=list(pids))
    return sent


def _wait_gone(pids, seconds):
    """等到这些进程都不在了，或到点；返回仍然活着的进程号。"""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        left = [pid for pid in pids if _pid_alive(pid)]
        if not left:
            return []
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return left
        time.sleep(min(PROCESS_POLL_SECONDS, remaining))


# ---------------------------------------------------------------------------
# 中止请求标记与「经本服务提交」凭据
# ---------------------------------------------------------------------------

def _cancel_marker_path(rt, tid):
    return os.path.join(rt.task_dir(tid),
                        getattr(rt, "CANCEL_MARKER", CANCEL_MARKER_FILENAME))


def _submit_marker_path(rt, tid):
    return os.path.join(rt.task_dir(tid), SUBMIT_MARKER_FILENAME)


def _submitted_by_service(rt, tid):
    """这个任务有没有「经本服务提交」的凭据。

    只判文件在不在，内容一概不读：标记由发起类处理器写，字段名以它实际落地为准；
    这里若依赖某个字段，两边一旦对不上，判定就会静默失效 —— 那比没有凭据更糟。
    有凭据 + 找不到执行进程 = 可以确认任务已不在运行；没有凭据就只能人工确认。
    """
    return os.path.isfile(_submit_marker_path(rt, tid))


def _write_cancel_marker(rt, tid, status, reason):
    """在任务目录写下「已请求中止」的标记（driver 写终态前会看它）。

    已经存在就不覆盖：第一次请求的时间才是真实请求时间，后来者不该把它改成新时间。
    """
    path = _cancel_marker_path(rt, tid)
    existing = _read_json_safely(path)
    if isinstance(existing, dict) and existing.get("requested_at"):
        return existing
    record = {
        "task_id": tid,
        "requested_at": rt.now_utc(),
        "status_at_request": status.get("status"),
        "stage_at_request": status.get("stage"),
        "reason": reason,
    }
    rt.write_json(path, record)
    return record


def _read_json_safely(path):
    try:
        if not os.path.isfile(path):
            return None
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (IOError, OSError, ValueError):
        return None


def _clear_cancel_marker(rt, tid):
    """删掉中止请求标记 —— **没有真的中止成功的分支必须走这一步**。

    为什么非要删：driver 认的是「标记存在 + 要写状态」就改成已中止。标记若在
    一次**失败**的取消里被留下来，这个任务后来真的跑成功了，终态也会被改写成
    「已中止」，结果从此取不到 —— 一次真实成功被一份残留文件埋掉，而回答里还写着
    「未做任何改动」。所以标记只在**确认可以中止之后**才落盘，没达成中止一律清掉。
    """
    path = _cancel_marker_path(rt, tid)
    try:
        if os.path.isfile(path):
            os.remove(path)
            return True
    except OSError:
        return False
    return False


def _settling_processes(tid, seconds=PROCESS_SETTLE_SECONDS):
    """在一个小窗口里反复扫进程表，返回扫到的进程号（扫不到就回空）。

    用来对付「后台进程正在拉起」那段延迟：只扫一次就断言「没有执行进程」太脆，
    机器忙或解释器启动慢的时候，一次扫描的结论会和半秒后的现实相反。
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        pids = _find_driver_processes(tid)
        if pids:
            return pids
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []
        time.sleep(min(PROCESS_POLL_SECONDS, remaining))


def _stop_pids(pids, ctx, tid):
    """先温和后强制地停住这些进程；返回 (发出的信号, 仍未退出的进程号)。"""
    signals = _terminate(pids, signal.SIGTERM, tid)
    left = _wait_gone(pids, _cancel_grace_seconds(ctx))
    if left:
        signals += _terminate(left, signal.SIGKILL, tid)
        left = _wait_gone(left, _cancel_kill_wait_seconds(ctx))
    return signals, left


def _cancel_result(tid, status_value, previous, aborted, reason, **extra):
    """取消类回答的公共形状（成败共用外壳，业务上靠 aborted 区分）。"""
    payload = {
        "task_id": tid,
        "status": status_value,
        "status_label": _status_label(status_value),
        "aborted": aborted,
        "previous_status": previous,
        "reason": reason,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# 工具一：等任务结束
# ---------------------------------------------------------------------------

def handle_wait_for_task(args, ctx=None):
    """挂起等待，直到任务进入终态或到达等待上限。

    三件事必须说清：

    1. **上限用单调时钟算，全程有真实的时间边界** —— 系统改时间也不会卡住或提前返回；
       超时回「仍在运行」而不是失败，对端可以再等一次，断线重来只是多等一轮。
    2. **单次上限不超过三十分钟**（`MAX_WAIT_SECONDS`）。调用方要得更多就按上限执行，
       并在回答里注明「被压到上限」—— 不静默照办，也不默默改小。
    3. **同时进行的等待有上限**（`_max_concurrent_waits`）。等待是同步处理函数、占着
       一个工作线程，几十个长等待就能让其余能力集体排队；超出的调用**立刻**回一个
       明确的结构化错误，而不是占着线程干等 —— 那正是要避免的局面。
    """
    _ensure_var_dir(ctx)
    rt = _driver(ctx)
    tid = (args.get("task_id") or "").strip()

    try:
        requested = float(args.get("timeout_seconds", DEFAULT_WAIT_SECONDS))
    except (TypeError, ValueError):
        requested = DEFAULT_WAIT_SECONDS
    requested = max(0.0, requested)
    timeout = min(MAX_WAIT_SECONDS, requested)
    clamped = timeout < requested
    poll = _poll_seconds(ctx)

    status = _read_status(rt, tid)              # 任务不存在时在这里透传 TASK_NOT_FOUND

    # 已经是终态：直接回答，不占等待名额（这类调用根本不会挂住）。
    if status.get("status") in TERMINAL_STATUSES:
        payload = {"task_id": tid}
        payload.update(_status_view(status))
        payload["terminal"] = True
        payload["timed_out"] = False
        payload["waited_seconds"] = 0.0
        payload["poll_interval_seconds"] = poll
        payload["poll_count"] = 0
        payload["note"] = "任务已进入终态：%s" % (
            _status_label(status.get("status")) or status.get("status"))
        return payload

    limit = _max_concurrent_waits(ctx)
    acquired = _acquire_wait_slot(limit)
    if not acquired:
        raise StructuredError(
            CODE_WAIT_LIMIT_REACHED,
            "当前等待数已达上限（%d 个），本次没有等待、也没有做任何改动；"
            "请改用较短的等待、分成几轮来等，或稍后再试。"
            "若只是想看进度，用查任务状态的能力即可，它不会占等待名额。"
            % limit,
            task_id=tid,
            max_concurrent_waits=limit,
            active_waits=_active_wait_count(),
            hint="每轮等几分钟、超时后再调一次，比一次挂满上限更稳")

    try:
        started = time.monotonic()
        deadline = started + timeout
        polls = 0
        while True:
            if status.get("status") in TERMINAL_STATUSES:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll, remaining))
            polls += 1
            status = _read_status(rt, tid)      # 等待期间被清理掉也如实报 TASK_NOT_FOUND
    finally:
        _release_wait_slot()

    waited = round(time.monotonic() - started, 3)
    terminal = status.get("status") in TERMINAL_STATUSES
    payload = {"task_id": tid}
    payload.update(_status_view(status))
    payload["terminal"] = terminal
    payload["timed_out"] = not terminal
    payload["waited_seconds"] = waited
    payload["poll_interval_seconds"] = poll
    payload["poll_count"] = polls
    payload["timeout_seconds"] = timeout
    if clamped:
        payload["requested_timeout_seconds"] = requested
        payload["timeout_clamped"] = True
    if terminal:
        payload["note"] = "任务已进入终态：%s" % (_status_label(status.get("status")) or
                                                 status.get("status"))
    else:
        payload["note"] = ("已等满 %s 秒，任务仍在%s；这不是失败，可以再调用一次继续等"
                           % (_seconds_text(timeout),
                              _status_label(status.get("status")) or "运行"))
    return payload



# ---------------------------------------------------------------------------
# 工具二：取消任务
# ---------------------------------------------------------------------------

def _abort_now(rt, tid, status, reason):
    """写中止标记 → 落「已中止」状态。**只在这个函数里做这两件事。**

    先写标记再落状态：标记是执行侧那处守卫的依据，落状态这一下也要被守卫覆盖
    （否则一个正在收尾的执行进程可能在两步之间把状态写成「运行中」）。
    """
    marker = _write_cancel_marker(rt, tid, status, reason)
    final = rt.write_status(tid, stage_list=_stage_list_for(rt, status),
                            status=STATUS_ABORTED, message="已按请求中止",
                            aborted_at=rt.now_utc(), abort_reason=reason)
    return marker, final


def _status_is_aborted(rt, tid):
    """任务当前状态是不是「已中止」（读不到/读坏了按「不是」处理，偏向保守）。"""
    record = _read_json_safely(os.path.join(rt.task_dir(tid), "status.json"))
    return bool(isinstance(record, dict) and record.get("status") == STATUS_ABORTED)


def _cancel_with_processes(rt, tid, status, previous, pids, ctx, reason=CANCEL_REASON):
    """已经确认「有执行进程」时的中止：发信号 → 确认退出 → 落状态。

    进程没死透就抛错、**状态一个字都不改**；中止标记由调用方在 finally 里清掉，
    免得一次没成功的取消把任务后来的真实结果改写成「已中止」。
    """
    # 先落标记（执行侧收尾时会看它），再发信号；状态要等进程确认退出之后才写。
    marker = _write_cancel_marker(rt, tid, status, reason)
    started = time.monotonic()
    signals, left = _stop_pids(pids, ctx, tid)
    if left:
        raise StructuredError(
            CODE_CANCEL_FAILED,
            "已按进程组发送终止信号（先温和后强制），但进程仍未退出：%s。"
            "任务状态与占用都没有改动，现场保留待人工确认"
            % "、".join(str(pid) for pid in left),
            task_id=tid,
            pids=left,
            stage=status.get("stage"),
            did_anything=True,
            note="中止未达成，状态未改动；请人工确认这些进程后再处理")
    stopped_after = round(time.monotonic() - started, 3)
    final = rt.write_status(tid, stage_list=_stage_list_for(rt, status),
                            status=STATUS_ABORTED, message="已按请求中止",
                            aborted_at=rt.now_utc(),
                            abort_reason="已按请求中止（%s）" % reason)
    return _cancel_result(
        tid, STATUS_ABORTED, previous, True, reason,
        previous_status_label=_status_label(previous),
        stage=final.get("stage"),
        progress_percent=final.get("progress_percent"),
        aborted_at=final.get("aborted_at"),
        stopped_pids=pids,
        signals=signals,
        waited_seconds=stopped_after,
        note="执行进程已确认退出，任务状态已落成已中止，执行位随之释放",
        cancel_request_file=CANCEL_MARKER_FILENAME,
        cancel_requested_at=marker.get("requested_at") if marker else None)


def _post_abort_sweep(tid, ctx):
    """落成已中止之后紧接的一小段扫尾：把「刚被拉起」的执行进程停掉。

    排队态被中止时，后台进程可能恰好在落状态之后才起来（机器忙、解释器启动慢）。
    执行侧那处守卫会把它的「运行中」改回「已中止」，但那只是让**状态**不再自相矛盾，
    进程本身还在跑 —— 所以这里再扫一次、能停就停，停不住的如实回报。
    返回 (扫到的进程号, 发出的信号, 仍未退出的进程号)。
    """
    late = _find_driver_processes(tid)
    if not late:
        return [], [], []
    signals, left = _stop_pids(late, ctx, tid)
    return late, signals, left


def handle_cancel_task(args, ctx=None):
    """请求中止一个排队或运行中的任务，并如实回报是否真的停住。

    **顺序是这段代码的全部要点**（上一版在这里出过两个真问题，都是审出来的）：

      1. **先定位进程、再决定要不要中止。** 中止请求标记只在「确认可以中止」之后才落盘：
         有进程时是发信号之前（执行侧收尾时要看到它），没有进程时是落状态之前。
         绝不像上一版那样一进门就写标记。
      2. **没达成中止就删标记。** 拒绝（没有凭据）、进程没死透、参数/状态异常，全部在
         `finally` 里清掉标记 —— 否则标记会残留，这个任务后来真的跑成功了，终态也会被
         改写成「已中止」，结果从此取不到；而回答里还写着「未做任何改动」。
         判定依据是**任务当前状态**：状态已经是「已中止」就保留标记（它是执行侧守卫的依据），
         不是「已中止」就删掉。
      3. **排队态先复核、再落状态，落完再扫一次。** 后台进程被拉起有延迟，只扫一次就断言
         「没有进程」太脆；复核窗口 + 落状态后的扫尾 + 执行侧守卫（不许把状态改回排队/运行中）
         三处一起把竞态窗口收到最小。
    """
    _ensure_var_dir(ctx)
    rt = _driver(ctx)
    tid = (args.get("task_id") or "").strip()
    status = _read_status(rt, tid)              # 不存在则 TASK_NOT_FOUND
    previous = status.get("status")

    if previous in TERMINAL_STATUSES:
        # 已经结束：一个字段都不写，也不碰标记
        return _cancel_result(
            tid, previous, previous, False,
            "任务已经结束（%s），无需中止" % (_status_label(previous) or previous))

    result = None
    try:
        pids = _find_driver_processes(tid)
        if pids:
            result = _cancel_with_processes(rt, tid, status, previous, pids, ctx)
            return result

        # 没有进程：复核一次状态（扫描的同时它可能刚好自己结束了）
        status = _read_status(rt, tid)
        if status.get("status") in TERMINAL_STATUSES:
            result = _cancel_result(
                tid, status.get("status"), previous, False,
                "任务已经结束（%s），无需中止"
                % (_status_label(status.get("status")) or status.get("status")))
            return result

        if status.get("status") == STATUS_QUEUED:
            # 排队态：先在一个小窗口里确认确实没有进程，再落状态；落完再扫尾。
            late = _settling_processes(tid)
            if late:
                result = _cancel_with_processes(rt, tid, status, previous, late, ctx,
                                                reason="排队阶段被请求中止（后台进程刚起来就被停掉）")
                return result
            marker, final = _abort_now(rt, tid, status, "排队阶段被请求中止")
            swept, sweep_signals, sweep_left = _post_abort_sweep(tid, ctx)
            result = _cancel_result(
                tid, STATUS_ABORTED, previous, True,
                "任务还在排队、复核窗口内没有执行进程，已直接落成已中止"
                if not swept else "任务刚被拉起就被停掉，状态已落成已中止",
                previous_status_label=_status_label(previous),
                stage=final.get("stage"),
                progress_percent=final.get("progress_percent"),
                aborted_at=final.get("aborted_at"),
                stopped_pids=swept,
                signals=sweep_signals,
                waited_seconds=0.0,
                note="任务状态已落成已中止，执行位随之释放"
                if not sweep_left else
                "状态已落成已中止，但扫尾时发现执行进程仍未退出：请人工确认",
                cancel_request_file=CANCEL_MARKER_FILENAME,
                cancel_requested_at=marker.get("requested_at") if marker else None)
            if sweep_left:
                raise StructuredError(
                    CODE_CANCEL_FAILED,
                    "状态已落成已中止，但落状态之后又发现执行进程、且未能停住：%s。"
                    "请人工确认这些进程后再处理" % "、".join(str(pid) for pid in sweep_left),
                    task_id=tid, pids=sweep_left, stage=final.get("stage"),
                    status_landed=STATUS_ABORTED)
            return result

        # 状态说在跑、却找不到执行进程。先看有没有「经本服务提交」的凭据：
        #   * 有凭据 → 本服务受理过的任务、进程已不在，据此落成已中止并释放执行位；
        #   * 没凭据 → 可能是别人在终端里以前台方式起的任务，本端无从确认，一律不动。
        submitted = _submitted_by_service(rt, tid)
        if submitted:
            late = _settling_processes(tid)
            if late:
                result = _cancel_with_processes(
                    rt, tid, status, previous, late, ctx,
                    reason="执行进程已不存在，但复核时又扫到进程，按请求中止")
                return result
            marker, final = _abort_now(rt, tid, status, "执行进程已不存在，按请求中止")
            result = _cancel_result(
                tid, STATUS_ABORTED, previous, True,
                "任务由本服务提交，执行进程已不存在，已按请求落成已中止",
                previous_status_label=_status_label(previous),
                stage=final.get("stage"),
                progress_percent=final.get("progress_percent"),
                aborted_at=final.get("aborted_at"),
                stopped_pids=[], signals=[], waited_seconds=0.0,
                note="没有执行进程可停；凭提交凭据可确认任务已不在运行，"
                     "状态已落成已中止，执行位随之释放",
                submit_marker_file=SUBMIT_MARKER_FILENAME,
                cancel_request_file=CANCEL_MARKER_FILENAME,
                cancel_requested_at=marker.get("requested_at") if marker else None)
            return result

        raise StructuredError(
            CODE_CANCEL_FAILED,
            "状态显示任务仍在%s，但进程表里找不到它对应的执行进程，且任务目录里没有"
            "「经本服务提交」的标记：它可能是别人在终端里以前台方式起的任务，"
            "本端无法确认，没有做任何改动（也没有留下中止请求）；"
            "请在那个终端里中断，或人工确认后再处理"
            % (_status_label(status.get("status")) or "运行"),
            task_id=tid,
            stage=status.get("stage"),
            submit_marker_file=SUBMIT_MARKER_FILENAME,
            submit_marker_found=False,
            cancel_request_file=CANCEL_MARKER_FILENAME,
            did_anything=False,
            hint="稍后再试；若它确实是本服务提交的任务，请确认提交标记是否被清理掉了")
    finally:
        # 只有「状态确实是已中止」才保留标记；其余一切分支（拒绝、进程没死透、
        # 异常）都把标记清掉 —— 残留标记会把任务后来的真实终态改写掉。
        if not _status_is_aborted(rt, tid):
            _clear_cancel_marker(rt, tid)


# ---------------------------------------------------------------------------
# 工具三：查占用状态
# ---------------------------------------------------------------------------

def handle_get_scheduler_state(args, ctx=None):
    """当前有没有任务占用执行位、占用者是谁、怎么解除。

    占用判定**直接复用 driver 的 `running_task()`**：两处各写一套的话，
    迟早会出现「工具说没占、发起却被拒」这种自相矛盾的回答。
    """
    _ensure_var_dir(ctx)
    rt = _driver(ctx)
    holder_id = rt.running_task()
    payload = {
        "busy": bool(holder_id),
        "holder": None,
        "how_to_release": [],
        "recent_tasks": _recent_tasks(rt, _recent_limit(ctx)),
    }

    if holder_id:
        status = _read_status(rt, holder_id)
        holder = _status_view(status)
        scan_available = os.path.isdir(PROC_ROOT)
        pids = _find_driver_processes(holder_id)
        submitted = _submitted_by_service(rt, holder_id)
        holder["process_scan_available"] = scan_available
        holder["holder_pids"] = pids
        # 扫不了进程表时「没找到」不等于「不存在」，所以这一项要说清能不能扫
        holder["process_found"] = bool(pids) if scan_available else None
        # 有没有「经本服务提交」的凭据：决定这个残留占用能不能靠取消来解开
        holder["submitted_by_service"] = submitted
        holder["elapsed_seconds"] = _elapsed_seconds(status.get("started_at"))
        payload["holder"] = holder
        payload["how_to_release"] = [
            {"action": "wait",
             "description": "等它结束：用等待工具挂到终态，天然不会撞上占用",
             "tool": "wait_for_task",
             "params": {"task_id": holder_id}},
            {"action": "cancel",
             "description": "取消它：真的发终止信号，确认停住后状态落成已中止，占用随释放",
             "tool": "cancel_task",
             "params": {"task_id": holder_id}},
            {"action": "force",
             "description": "用强制开关发起新任务：不解除占用，会让两个任务并行，"
                            "有互相干扰的风险，返回里会一并提示",
             "tool": "start_cleaning_task",
             "params": {"force": True}},
        ]
        if scan_available and not pids:
            if submitted:
                payload["note"] = (
                    "占用者的状态文件仍显示%s，但它的执行进程已经不在；"
                    "这个任务有「经本服务提交」的标记，可以取消它来释放执行位"
                    % (_status_label(status.get("status")) or "运行"))
            else:
                payload["note"] = (
                    "占用者的状态文件仍显示%s，但进程表里找不到它的执行进程，"
                    "任务目录里也没有「经本服务提交」的标记：可能是别人在终端里"
                    "以前台方式起的任务，本端无法确认，只能人工处理（在发起它的"
                    "终端里中断，或确认后按异常清理）"
                    % (_status_label(status.get("status")) or "运行"))
        elif not scan_available:
            payload["note"] = ("占用者仍在%s；当前环境没有 /proc，无法核对执行进程是否还活着"
                               % (_status_label(status.get("status")) or "运行"))
    else:
        payload["note"] = "当前没有任务占用执行位，可以直接发起。"

    return payload


def _recent_tasks(rt, limit):
    """最近几个任务的状态摘要（只读状态文件，不做任何推断）。"""
    out = []
    directory = rt.tasks_dir()
    if not os.path.isdir(directory):
        return out
    try:
        names = sorted(os.listdir(directory), reverse=True)
    except OSError:
        return out
    for tid in names:
        if len(out) >= limit:
            break
        if not os.path.isdir(os.path.join(directory, tid)):
            continue
        status = rt.read_json(os.path.join(directory, tid, "status.json"))
        if not status:
            continue
        item = {
            "task_id": tid,
            "status": status.get("status"),
            "status_label": _status_label(status.get("status")),
            "stage": status.get("stage"),
            "progress_percent": status.get("progress_percent"),
            "started_at": status.get("started_at"),
            "updated_at": status.get("updated_at"),
        }
        for key in ("task_type", "tag", "data_version"):
            if status.get(key) not in (None, ""):
                item[key] = status[key]
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 工具四：能力自描述
# ---------------------------------------------------------------------------

def handle_describe_capabilities(args, ctx=None):
    """能力自描述：原样给出工具表，并补上「真正可调用」与「尚未接入」两份清单。

    工具表本身由 `tools.describe_capabilities` 产出（与对端对齐的权威来源），
    这里只做加法：排障时一眼能看出哪些能力已经接上处理器、哪些还空着。
    """
    server_info = None
    if ctx is not None and hasattr(ctx, "as_server_info"):
        server_info = ctx.as_server_info()
    payload = build_capabilities(server_info)
    declared = [tool["name"] for tool in TOOLS]
    ready = list(_dispatch.available_tools())
    payload["available_tools"] = ready
    payload["unavailable_tools"] = [name for name in declared if name not in ready]
    payload["declared_count"] = len(declared)
    payload["ready_count"] = len(ready)
    return payload


# ---------------------------------------------------------------------------
# 工具五：健康检查
# ---------------------------------------------------------------------------

def _probe_driver(ctx=None):
    """极轻量探活：脚本在不在 + 能不能导入执行层。**不启动作业链**。

    导入 `driver.run_task` 会连带拉起 engine 的解析链，是「driver 真能用」里最便宜
    的一种证据；`validate`、`schemes` 这类同步子命令虽然也安全，但要另起一个解释器，
    探活不该有这么重的代价。
    """
    script_rel = _driver_script(ctx)
    script_abs = os.path.join(_repo_root(ctx), script_rel)
    probe = {
        "checked": "入口脚本存在性 + 导入 driver.run_task（含 engine 解析链）；任何作业都不启动",
        "script": script_rel,
        "script_exists": os.path.isfile(script_abs),
        "ready": False,
        "reason": "",
    }
    if not probe["script_exists"]:
        probe["reason"] = "driver 入口脚本不存在：%s" % script_rel
        return probe
    try:
        rt = _driver(ctx)
    except Exception as exc:                        # noqa: BLE001 —— 探活要把原因原样带回去
        probe["reason"] = "导入 driver.run_task 失败：%s: %s" % (type(exc).__name__, exc)
        return probe
    probe["ready"] = True
    probe["driver_module"] = "driver.run_task"
    probe["driver_interface_version"] = getattr(rt, "INTERFACE_VERSION", "")
    return probe


def handle_health_check(args, ctx=None):
    """探活与版本：服务名、契约版本、启动时间、能力就绪情况、driver 是否可用。"""
    config = getattr(ctx, "config", None) or {}
    started_at = getattr(ctx, "started_at", None)
    if started_at is not None and not isinstance(started_at, str):
        # 服务层如果给的是时间对象，就转成 ISO 串再回（对外只给字符串，便于比对）
        isoformat = getattr(started_at, "isoformat", None)
        started_at = isoformat() if callable(isoformat) else str(started_at)
    declared = [tool["name"] for tool in TOOLS]
    ready = list(_dispatch.available_tools())

    payload = {
        "service": config.get("name") or os.environ.get("MCP_SERVICE_NAME")
                   or DEFAULT_SERVICE_NAME,
        "interface_version": INTERFACE_VERSION,
        "driver_interface_version": DRIVER_INTERFACE_VERSION,
        "python": sys.version.split()[0],
        "tools_declared": len(declared),
        "tools_ready": len(ready),
        "tools_unavailable": [name for name in declared if name not in ready],
    }
    if started_at:                       # 没给启动时间就不回这一项（不填占位）
        payload["started_at"] = started_at
    uptime = _elapsed_seconds(started_at)
    if uptime is not None:
        payload["uptime_seconds"] = uptime

    _ensure_var_dir(ctx)
    probe = _probe_driver(ctx)
    payload["driver_ready"] = probe["ready"]
    payload["driver_probe"] = probe
    return payload


# ---------------------------------------------------------------------------
# 处理器登记表（dispatch.load_handlers 按模块取这个名字）
# ---------------------------------------------------------------------------

HANDLERS = {
    "wait_for_task": handle_wait_for_task,
    "cancel_task": handle_cancel_task,
    "get_scheduler_state": handle_get_scheduler_state,
    "describe_capabilities": handle_describe_capabilities,
    "health_check": handle_health_check,
}
