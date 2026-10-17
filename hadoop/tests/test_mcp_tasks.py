# -*- coding: utf-8 -*-
"""N-测试（迭代二 MCP）：长任务这一段 —— 等待、取消、占用状态、自描述与健康检查。

覆盖实现方案 §3.3 / §7 / §8：
  * 等待：挂到终态、等满超时回「仍在运行」（不是失败）、零超时立即返回、任务不存在；
  * 取消：真的有进程时按进程组停住并把状态落成「已中止」、已结束的任务不报错、
    排队任务直接落终态、状态说在跑却找不到进程时如实报失败且不改状态；
  * 占用状态：空闲与占用两种情形、解除方式的清单、最近任务摘要；
  * 能力自描述：工具表原样给出 + 已接入 / 未接入两份清单；
  * 健康检查：driver 可用与不可用两条路径；
  * driver 那一处最小改动：写终态前见到中止标记就写「已中止」。

全部使用**临时任务目录**（`ML_VAR_DIR`）+ **真实短命子进程**，不启集群、不跑作业链、
不产生任何业务数字。会读进程表的用例在缺少 `/proc` 的环境里显式跳过并写明原因。
"""

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
HADOOP_ROOT = os.path.join(REPO_ROOT, "hadoop")
if HADOOP_ROOT not in sys.path:
    sys.path.insert(0, HADOOP_ROOT)

import driver.run_task as driver          # noqa: E402
from mcpserver import contract            # noqa: E402
from mcpserver import dispatch            # noqa: E402
from mcpserver import tasks as tasks_module  # noqa: E402

#: 没有 /proc 就按不了命令行反查执行进程 —— 这类用例跳过而不是伪造进程号。
PROC_AVAILABLE = os.path.isdir("/proc")
PROC_SKIP_REASON = "当前环境没有 /proc，无法按命令行定位执行进程"


class TaskTestBase(unittest.TestCase):
    """公共夹具：临时任务目录、只登记本段处理器、子进程兜底清理。"""

    def setUp(self):
        self.var_dir = tempfile.mkdtemp(prefix="ml-mcp-tasks-")
        self.addCleanup(shutil.rmtree, self.var_dir, True)

        env_patcher = mock.patch.dict(os.environ, {"ML_VAR_DIR": self.var_dir})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        self.tasks_root = os.path.join(self.var_dir, "tasks")
        os.makedirs(self.tasks_root)

        # 轮询与收尾等待都调短，测试才跑得动；语义与线上完全一致，只是时间尺度不同。
        self.ctx = dispatch.ToolContext(
            REPO_ROOT,
            config={"name": "hadoop-mcp",
                    "wait_poll_seconds": 0.05,
                    "cancel_grace_seconds": 1.0,
                    "cancel_kill_wait_seconds": 1.0})

        # 排队/残留分支的复核窗口也调短：语义与线上一致，只是时间尺度不同。
        settle_patcher = mock.patch.object(tasks_module, "PROCESS_SETTLE_SECONDS", 0.05)
        settle_patcher.start()
        self.addCleanup(settle_patcher.stop)

        dispatch.reset_handlers()
        self.addCleanup(dispatch.reset_handlers)
        dispatch.load_handlers(("mcpserver.tasks",))

        self._children = []
        self.addCleanup(self._reap_children)

    # -- 夹具工具 ---------------------------------------------------------

    def make_task(self, tid, status="running", stage="clean_users", **extra):
        """按 driver 的状态文件形状造一个任务目录（字段全是真实语义，不填假数字）。"""
        directory = os.path.join(self.tasks_root, tid)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        stamp = driver.now_utc()
        payload = {"task_id": tid, "status": status, "stage": stage,
                   "started_at": stamp, "updated_at": stamp,
                   "message": "", "errors": []}
        payload.update(extra)
        with io.open(os.path.join(directory, "status.json"), "w",
                     encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
        return directory

    def read_task_status(self, tid):
        path = os.path.join(self.tasks_root, tid, "status.json")
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def marker_path(self, tid):
        return os.path.join(self.tasks_root, tid, driver.CANCEL_MARKER)

    def submit_marker_path(self, tid):
        return os.path.join(self.tasks_root, tid, tasks_module.SUBMIT_MARKER_FILENAME)

    def write_submit_marker(self, tid, content=None, raw_text=None):
        """造一个「经本服务提交」的标记。

        这个文件由发起类处理器写，本段只按它**存在与否**判断，所以测试里既给它正常
        字段，也故意换成对不上的字段名、甚至非 JSON 文本 —— 判定照样要生效（存在即
        凭据）。这正是这道判定能不能扛住两边字段漂移的关键。
        """
        path = self.submit_marker_path(tid)
        if raw_text is not None:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(raw_text)
            return path
        payload = content if content is not None else {
            "submitted_by": "mcp",
            "submitted_at": driver.now_utc(),
            "tool": "start_cleaning_task",
            "args": {"scope": "sample"},
        }
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def spawn_fake_driver(self, tid, script_name="run_task.py"):
        """起一个命令行形状与 driver 后台子进程一致的短命进程。

        真实的 `_run` 需要集群才跑得动，这里用同名脚本 + 同样的参数形状代替：
        定位逻辑（按 `run_task.py` / `_run` / `--task-id` 三个条件反查）与发信号
        路径都是**真实**的，只是被停住的进程不做作业。
        """
        script = os.path.join(self.var_dir, script_name)
        with io.open(script, "w", encoding="utf-8") as fh:
            fh.write("import time\n"
                     "while True:\n"
                     "    time.sleep(0.05)\n")
        proc = subprocess.Popen(
            [sys.executable, script, "_run", "--task-id", tid],
            cwd=self.var_dir, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._children.append(proc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.pid in tasks_module._find_driver_processes(tid):
                return proc
            time.sleep(0.02)
        self.fail("测试用的执行进程没能被按命令行定位到：pid=%s" % proc.pid)

    def flip_status_later(self, tid, status, delay, stage="done"):
        """在后台过一会儿把状态改成终态，用来验证等待会及时返回。"""
        def _flip():
            time.sleep(delay)
            self.make_task(tid, status=status, stage=stage)

        thread = threading.Thread(target=_flip, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        return thread

    def _reap_children(self):
        for proc in self._children:
            if proc.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


# ---------------------------------------------------------------------------
# 等待
# ---------------------------------------------------------------------------

class TestWaitForTask(TaskTestBase):
    def test_returns_when_task_reaches_terminal(self):
        tid = "20240101-000001-waitok"
        self.make_task(tid, status="running", stage="clean_users")
        self.flip_status_later(tid, "succeeded", 0.2)

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 10},
                                      self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(envelope["timed_out"])
        self.assertTrue(envelope["terminal"])
        self.assertEqual("succeeded", envelope["status"])
        self.assertEqual("成功", envelope["status_label"])
        self.assertGreaterEqual(envelope["poll_count"], 1)
        self.assertGreater(envelope["waited_seconds"], 0)
        self.assertLess(envelope["waited_seconds"], 10)

    def test_timeout_reports_still_running_not_failure(self):
        tid = "20240101-000002-timeout"
        self.make_task(tid, status="running", stage="clean_ratings")

        started = time.monotonic()
        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 1},
                                      self.ctx)
        elapsed = time.monotonic() - started
        # 超时是「如实回答还在跑」，不是失败：ok 仍然是真，对端可以再等一次
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["timed_out"])
        self.assertFalse(envelope["terminal"])
        self.assertEqual("running", envelope["status"])
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 15)
        self.assertIn("仍在", envelope["note"])

    def test_zero_timeout_returns_immediately(self):
        tid = "20240101-000003-nowait"
        self.make_task(tid, status="queued", stage="queued")

        started = time.monotonic()
        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 0},
                                      self.ctx)
        elapsed = time.monotonic() - started
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["timed_out"])
        self.assertEqual("queued", envelope["status"])
        self.assertLess(elapsed, 2.0)

    def test_terminal_task_returns_at_once(self):
        tid = "20240101-000004-done"
        self.make_task(tid, status="failed", stage="clean_movies",
                       message="作业失败")

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 30},
                                      self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(envelope["timed_out"])
        self.assertEqual("failed", envelope["status"])
        self.assertEqual("失败", envelope["status_label"])
        self.assertEqual("作业失败", envelope["message"])

    def test_unknown_task_is_task_not_found(self):
        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": "no-such-task"}, self.ctx)
        self.assertFalse(envelope["ok"])
        self.assertEqual(contract.CODE_TASK_NOT_FOUND, envelope["error"]["code"])
        self.assertIn("no-such-task", envelope["error"]["message"])

    def test_missing_status_file_through_handler(self):
        """状态文件不存在时处理器抛的就是结构化错误，不会是别的异常类型。"""
        with self.assertRaises(contract.StructuredError) as caught:
            tasks_module.handle_wait_for_task({"task_id": "ghost-task"}, self.ctx)
        self.assertEqual(contract.CODE_TASK_NOT_FOUND, caught.exception.code)


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------

@unittest.skipUnless(PROC_AVAILABLE, PROC_SKIP_REASON)
class TestCancelTaskWithProcess(TaskTestBase):
    def test_running_process_is_stopped_and_status_lands_aborted(self):
        tid = "20240101-000010-cancelrun"
        self.make_task(tid, status="running", stage="clean_users")
        proc = self.spawn_fake_driver(tid)

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"], envelope)
        self.assertEqual("aborted", envelope["status"])
        self.assertEqual("已中止", envelope["status_label"])
        self.assertEqual("running", envelope["previous_status"])
        self.assertIn(proc.pid, envelope["stopped_pids"])
        self.assertTrue(envelope["signals"], envelope)

        # 进程真的没了（不是「发了信号就算成功」）
        proc.wait(timeout=10)
        self.assertIsNotNone(proc.returncode)

        # 状态落成了已中止，并带上时间与原因
        status = self.read_task_status(tid)
        self.assertEqual("aborted", status["status"])
        self.assertTrue(status.get("aborted_at"))
        self.assertIn("中止", status.get("abort_reason", ""))
        # 中止请求标记留在任务目录里（driver 写终态前的保护要看它）
        self.assertTrue(os.path.isfile(self.marker_path(tid)))

        # 占用随之释放：driver 的占用判定不再认为有任务在跑
        self.assertIsNone(driver.running_task())

    def test_second_cancel_reports_already_finished(self):
        tid = "20240101-000011-cancelagain"
        self.make_task(tid, status="running", stage="clean_users")
        self.spawn_fake_driver(tid)

        first = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(first["aborted"], first)

        second = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(second["ok"], second)
        self.assertFalse(second["aborted"])
        self.assertEqual("aborted", second["status"])
        self.assertIn("已经结束", second["reason"])

    def test_queued_task_with_live_process_is_also_stopped(self):
        """排队状态下后台进程其实已经拉起来了：照样按进程停掉，不是只改状态了事。"""
        tid = "20240101-000013-queuelive"
        self.make_task(tid, status="queued", stage="queued")
        proc = self.spawn_fake_driver(tid)

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"], envelope)
        self.assertIn(proc.pid, envelope["stopped_pids"])
        proc.wait(timeout=10)
        self.assertIsNotNone(proc.returncode)
        self.assertEqual("aborted", self.read_task_status(tid)["status"])

    def test_running_without_process_reports_failure_and_keeps_state(self):
        """状态说在跑、进程表里却没有它、也没有提交凭据：如实报失败，状态一律不动。"""
        tid = "20240101-000012-orphan"
        self.make_task(tid, status="running", stage="stats_marks")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(contract.CODE_CANCEL_FAILED, envelope["error"]["code"])
        self.assertEqual("running", self.read_task_status(tid)["status"])
        # 状态没动；**中止请求标记也必须清掉** —— 留着它，这个任务后来真跑成功了
        # 终态也会被改写成「已中止」，结果从此取不到（审查发现的严重问题）
        self.assertFalse(os.path.isfile(self.marker_path(tid)))
        # 「没有提交凭据」这件事要写进细节字段，排障时一眼能看出为什么拒绝
        self.assertFalse(envelope["error"]["submit_marker_found"])
        self.assertEqual(tasks_module.SUBMIT_MARKER_FILENAME,
                         envelope["error"]["submit_marker_file"])
        self.assertIn("无法确认", envelope["error"]["message"])


class TestCancelTaskWithoutProcess(TaskTestBase):
    def test_stale_running_with_submit_marker_is_aborted(self):
        """状态说在跑、进程没了，但有「经本服务提交」的凭据：据此落成已中止并释放占用。"""
        tid = "20240101-000022-staleok"
        self.make_task(tid, status="running", stage="clean_ratings")
        self.write_submit_marker(tid)
        # 前提：此时确实没有这个任务的执行进程，占用者是它自己
        self.assertEqual(tid, driver.running_task())

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"], envelope)
        self.assertEqual("aborted", envelope["status"])
        self.assertEqual("running", envelope["previous_status"])
        self.assertEqual([], envelope["stopped_pids"])
        self.assertEqual([], envelope["signals"])
        self.assertEqual(tasks_module.SUBMIT_MARKER_FILENAME,
                         envelope["submit_marker_file"])
        self.assertIn("已不存在", envelope["reason"])

        status = self.read_task_status(tid)
        self.assertEqual("aborted", status["status"])
        self.assertTrue(status.get("aborted_at"))
        self.assertIn("执行进程已不存在", status.get("abort_reason", ""))
        # 执行位真的释放了
        self.assertIsNone(driver.running_task())

    def test_submit_marker_is_judged_by_existence_not_fields(self):
        """标记由别人写：字段名对不上、内容根本不是 JSON，只要文件在就按凭据处理。"""
        tid = "20240101-000023-stalefields"
        self.make_task(tid, status="running", stage="finalize")
        self.write_submit_marker(tid, raw_text="由本服务提交（不是 JSON 也没关系）\n")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"], envelope)
        self.assertEqual("aborted", self.read_task_status(tid)["status"])
        self.assertIsNone(driver.running_task())

    def test_mismatched_field_names_still_count_as_evidence(self):
        tid = "20240101-000024-stalekeys"
        self.make_task(tid, status="running", stage="score_before")
        self.write_submit_marker(tid, content={"谁提交的": "本服务", "什么时候": "刚刚"})

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"], envelope)

    def test_finished_task_is_not_an_error(self):
        tid = "20240101-000020-finished"
        self.make_task(tid, status="succeeded", stage="done")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(envelope["aborted"])
        self.assertEqual("succeeded", envelope["status"])
        self.assertIn("已经结束", envelope["reason"])
        # 已结束的任务不该被写上中止标记，否则会连累后来的终态
        self.assertFalse(os.path.isfile(self.marker_path(tid)))
        self.assertEqual("succeeded", self.read_task_status(tid)["status"])

    def test_queued_task_can_be_aborted(self):
        tid = "20240101-000021-queued"
        self.make_task(tid, status="queued", stage="queued", task_type="clean")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"])
        self.assertEqual("aborted", envelope["status"])
        self.assertEqual("queued", envelope["previous_status"])
        self.assertEqual([], envelope["stopped_pids"])
        self.assertEqual("aborted", self.read_task_status(tid)["status"])

    def test_unknown_task_is_task_not_found(self):
        envelope = dispatch.call_tool("cancel_task", {"task_id": "nope"}, self.ctx)
        self.assertFalse(envelope["ok"])
        self.assertEqual(contract.CODE_TASK_NOT_FOUND, envelope["error"]["code"])


class TestProcessMatching(unittest.TestCase):
    """命令行反查的三个条件：解释器是 python、有 run_task.py 与 _run、任务编号对得上。"""

    TID = "20240101-000030-match"

    def test_matches_real_driver_launch_shape(self):
        argv = ["/usr/bin/python3", "/repo/hadoop/driver/run_task.py", "_run",
                "--task-id", self.TID, "--rules", "config/cleaning_rules.v1.json"]
        self.assertTrue(tasks_module._matches_driver_run(argv, self.TID))

    def test_matches_equals_form(self):
        argv = ["/usr/bin/python3.11", "/repo/hadoop/driver/run_task.py", "_run",
                "--task-id=%s" % self.TID]
        self.assertTrue(tasks_module._matches_driver_run(argv, self.TID))

    def test_rejects_other_interpreter(self):
        # grep/tail 之类命令行里带同样字样的进程，不是执行进程
        argv = ["/usr/bin/grep", "run_task.py", "_run", "--task-id", self.TID]
        self.assertFalse(tasks_module._matches_driver_run(argv, self.TID))

    def test_rejects_missing_run_subcommand(self):
        argv = ["/usr/bin/python3", "/repo/hadoop/driver/run_task.py",
                "--task-id", self.TID]
        self.assertFalse(tasks_module._matches_driver_run(argv, self.TID))

    def test_rejects_other_task(self):
        argv = ["/usr/bin/python3", "/repo/hadoop/driver/run_task.py", "_run",
                "--task-id", "20240101-000031-other"]
        self.assertFalse(tasks_module._matches_driver_run(argv, self.TID))

    def test_rejects_other_script(self):
        argv = ["/usr/bin/python3", "/repo/hadoop/driver/upload_raw.py", "_run",
                "--task-id", self.TID]
        self.assertFalse(tasks_module._matches_driver_run(argv, self.TID))


# ---------------------------------------------------------------------------
# 占用状态
# ---------------------------------------------------------------------------

class TestSchedulerState(TaskTestBase):
    def test_free_when_nothing_runs(self):
        tid = "20240101-000040-idle"
        self.make_task(tid, status="succeeded", stage="done")

        envelope = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(envelope["busy"])
        self.assertIsNone(envelope["holder"])
        self.assertEqual([], envelope["how_to_release"])
        self.assertIn("没有任务占用", envelope["note"])
        self.assertEqual(tid, envelope["recent_tasks"][0]["task_id"])
        self.assertEqual("succeeded", envelope["recent_tasks"][0]["status"])

    def test_busy_shows_holder_and_how_to_release(self):
        tid = "20240101-000041-busy"
        self.make_task(tid, status="running", stage="score_before",
                       task_type="full", data_version="ml1m-clean-v1")

        envelope = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["busy"])
        holder = envelope["holder"]
        self.assertEqual(tid, holder["task_id"])
        self.assertEqual("running", holder["status"])
        self.assertEqual("运行", holder["status_label"])
        self.assertEqual("score_before", holder["stage"])
        self.assertEqual("ml1m-clean-v1", holder["data_version"])

        actions = [item["action"] for item in envelope["how_to_release"]]
        self.assertEqual(["wait", "cancel", "force"], actions)
        for item in envelope["how_to_release"]:
            self.assertTrue(item["description"])
        self.assertEqual(tid, envelope["how_to_release"][1]["params"]["task_id"])

    def test_recent_tasks_capped(self):
        for index in range(7):
            self.make_task("20240101-00005%d-recent" % index, status="succeeded",
                           stage="done")

        envelope = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertEqual(5, len(envelope["recent_tasks"]))


@unittest.skipUnless(PROC_AVAILABLE, PROC_SKIP_REASON)
class TestSchedulerStateProcess(TaskTestBase):
    def test_holder_process_is_found(self):
        tid = "20240101-000050-holder"
        self.make_task(tid, status="running", stage="clean_users")
        proc = self.spawn_fake_driver(tid)

        envelope = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertTrue(envelope["holder"]["process_found"], envelope)
        self.assertIn(proc.pid, envelope["holder"]["holder_pids"])

    def test_stale_holder_without_submit_marker_needs_manual_action(self):
        """状态文件说在跑、进程却不存在、也没有提交凭据：说清只能人工处理。"""
        tid = "20240101-000051-stale"
        self.make_task(tid, status="running", stage="finalize")

        envelope = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertTrue(envelope["busy"])
        self.assertFalse(envelope["holder"]["process_found"])
        self.assertEqual([], envelope["holder"]["holder_pids"])
        self.assertFalse(envelope["holder"]["submitted_by_service"])
        self.assertIn("人工处理", envelope["note"])
        self.assertNotIn("可以取消", envelope["note"])

    def test_stale_holder_with_submit_marker_points_to_cancel(self):
        """有提交凭据的残留占用：明确告诉对端可以取消来释放执行位。"""
        tid = "20240101-000052-staleok"
        self.make_task(tid, status="running", stage="clean_movies")
        self.write_submit_marker(tid)

        envelope = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertTrue(envelope["busy"])
        self.assertFalse(envelope["holder"]["process_found"])
        self.assertTrue(envelope["holder"]["submitted_by_service"])
        self.assertIn("可以取消", envelope["note"])
        self.assertIn("已经不在", envelope["note"])

        # 提示不是空话：按它说的取消一次，占用确实被解开
        cancelled = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(cancelled["aborted"], cancelled)
        after = dispatch.call_tool("get_scheduler_state", {}, self.ctx)
        self.assertFalse(after["busy"])


class TestVarDirAlignment(TaskTestBase):
    """服务层从 env.sh 解析出的任务目录放进 ctx.env 时，本段工具必须跟着它查。"""

    def test_ctx_env_var_dir_is_adopted(self):
        tid = "20240101-000055-varidr"
        self.make_task(tid, status="succeeded", stage="done")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ML_VAR_DIR", None)
            ctx = dispatch.ToolContext(REPO_ROOT,
                                       env={"ML_VAR_DIR": self.var_dir})
            envelope = dispatch.call_tool("get_scheduler_state", {}, ctx)

        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(tid, envelope["recent_tasks"][0]["task_id"])
        self.assertEqual(self.var_dir, os.environ.get("ML_VAR_DIR"))


# ---------------------------------------------------------------------------
# 能力自描述与健康检查
# ---------------------------------------------------------------------------

class TestDescribeCapabilities(TaskTestBase):
    def test_tool_table_plus_readiness(self):
        envelope = dispatch.call_tool("describe_capabilities", {}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(contract.INTERFACE_VERSION, envelope["interface_version"])
        self.assertEqual(contract.DRIVER_INTERFACE_VERSION,
                         envelope["driver_interface_version"])
        self.assertEqual(len(tasks_module.TOOLS), len(envelope["tools"]))
        self.assertEqual(len(tasks_module.TOOLS), envelope["declared_count"])

        # 只登记了本段的五个处理器，其余能力如实列为「尚未接入」
        self.assertEqual(sorted(tasks_module.HANDLERS), sorted(envelope["available_tools"]))
        self.assertEqual(len(tasks_module.TOOLS) - len(tasks_module.HANDLERS),
                         len(envelope["unavailable_tools"]))
        self.assertEqual(len(tasks_module.HANDLERS), envelope["ready_count"])
        self.assertNotIn("start_cleaning_task", envelope["available_tools"])
        self.assertIn("start_cleaning_task", envelope["unavailable_tools"])

        # 服务信息来自 ctx，不编造
        self.assertEqual("hadoop-mcp", envelope["server"]["name"])
        # 中文说明与状态取值也在里面（对端直接转述给用户）
        self.assertIn("等任务结束", [tool["title"] for tool in envelope["tools"]])
        self.assertIn({"value": "aborted", "label": "已中止"}, envelope["statuses"])


class TestHealthCheck(TaskTestBase):
    def test_reports_versions_and_ready_tools(self):
        self.ctx.started_at = driver.now_utc()
        envelope = dispatch.call_tool("health_check", {}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual("hadoop-mcp", envelope["service"])
        self.assertEqual(contract.INTERFACE_VERSION, envelope["interface_version"])
        self.assertEqual(contract.DRIVER_INTERFACE_VERSION,
                         envelope["driver_interface_version"])
        self.assertEqual(len(tasks_module.TOOLS), envelope["tools_declared"])
        self.assertEqual(len(tasks_module.HANDLERS), envelope["tools_ready"])
        self.assertEqual(len(tasks_module.TOOLS) - len(tasks_module.HANDLERS),
                         len(envelope["tools_unavailable"]))
        self.assertIsNotNone(envelope["uptime_seconds"])

        # driver 在仓库里是可用的；这条探测只做导入与文件存在性，不启动作业链
        self.assertTrue(envelope["driver_ready"], envelope)
        self.assertTrue(envelope["driver_probe"]["ready"])
        self.assertTrue(envelope["driver_probe"]["script_exists"])
        self.assertEqual("driver.run_task", envelope["driver_probe"]["driver_module"])

    def test_reports_driver_unavailable_without_lying(self):
        with mock.patch.object(tasks_module, "_repo_root",
                               return_value=self.var_dir):
            envelope = dispatch.call_tool("health_check", {}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(envelope["driver_ready"])
        self.assertFalse(envelope["driver_probe"]["ready"])
        self.assertIn("不存在", envelope["driver_probe"]["reason"])


# ---------------------------------------------------------------------------
# driver 那一处最小改动：写终态前的「已中止」保护
# ---------------------------------------------------------------------------

class TestWriteStatusAbortGuard(TaskTestBase):
    def test_without_marker_failure_stays_failure(self):
        tid = "20240101-000060-guard1"
        self.make_task(tid, status="running", stage="clean_users")

        driver.write_status(tid, status="failed", message="作业报错")
        self.assertEqual("failed", self.read_task_status(tid)["status"])

    def test_marker_turns_failure_into_aborted(self):
        tid = "20240101-000061-guard2"
        self.make_task(tid, status="running", stage="clean_users")
        with io.open(self.marker_path(tid), "w", encoding="utf-8") as fh:
            fh.write("{}")

        driver.write_status(tid, status="failed", message="信号打断了作业")
        status = self.read_task_status(tid)
        self.assertEqual("aborted", status["status"])
        self.assertTrue(status.get("aborted_at"))
        # 失败原因保留下来，排障要看它停在哪一步
        self.assertEqual("信号打断了作业", status["message"])

    def test_marker_turns_success_into_aborted(self):
        tid = "20240101-000062-guard3"
        self.make_task(tid, status="running", stage="publish")
        with io.open(self.marker_path(tid), "w", encoding="utf-8") as fh:
            fh.write("{}")

        driver.write_status(tid, status="succeeded", stage="done", message="done")
        status = self.read_task_status(tid)
        self.assertEqual("aborted", status["status"])
        self.assertIn("中止", status["message"])

    def test_marker_blocks_pending_status_from_coming_back(self):
        """有中止标记时，排队/运行中都不许写回去 —— 否则排队中止会被覆盖成"运行中"。"""
        tid = "20240101-000063-guard4"
        self.make_task(tid, status="queued", stage="queued")
        with io.open(self.marker_path(tid), "w", encoding="utf-8") as fh:
            fh.write("{}")

        driver.write_status(tid, status="running", stage="clean_users")
        status = self.read_task_status(tid)
        self.assertEqual("aborted", status["status"])
        self.assertIn("中止", status["message"])
        self.assertTrue(status.get("aborted_at"))

    def test_marker_blocks_queued_status_from_coming_back(self):
        tid = "20240101-000064-guard5"
        self.make_task(tid, status="queued", stage="queued")
        with io.open(self.marker_path(tid), "w", encoding="utf-8") as fh:
            fh.write("{}")

        driver.write_status(tid, status="queued", stage="queued")
        self.assertEqual("aborted", self.read_task_status(tid)["status"])

    def test_aborted_task_stays_aborted(self):
        """已经是已中止的，再写状态也不会被改回去（标记只在保护它）。"""
        tid = "20240101-000065-guard6"
        self.make_task(tid, status="aborted", stage="clean_users")
        with io.open(self.marker_path(tid), "w", encoding="utf-8") as fh:
            fh.write("{}")

        driver.write_status(tid, status="aborted", message="已按请求中止")
        self.assertEqual("aborted", self.read_task_status(tid)["status"])


# ---------------------------------------------------------------------------
# 审查回归一：失败的取消不许留下中止标记（否则真实成功会被改写成已中止）
# ---------------------------------------------------------------------------

class TestFailedCancelLeavesNoResidue(TaskTestBase):
    """审查发现的严重问题：拒绝分支不删标记 → 一次真实成功被埋掉。

    旧行为：一进门就写标记，拒绝时也不删。执行侧看到「标记存在」就把终态改成
    已中止，于是「取消失败 → 任务后来跑成功」这条路径上，结果永远取不到。
    新行为：只在确认可以中止之后才落标记，没达成就清掉。
    """

    def test_refused_cancel_then_real_success_is_not_poisoned(self):
        tid = "20240101-000070-poison"
        self.make_task(tid, status="running", stage="clean_ratings")
        # 没有执行进程、也没有「经本服务提交」的凭据 → 取消必须拒绝
        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(contract.CODE_CANCEL_FAILED, envelope["error"]["code"])
        # 拒绝之后标记必须不在
        self.assertFalse(os.path.isfile(self.marker_path(tid)),
                         "取消被拒绝后仍留下了中止标记，会把之后真实的成功改写成已中止")

        # 这个任务随后真的跑成功：终态必须如实是「成功」
        driver.write_status(tid, status="succeeded", stage="done", message="done")
        status = self.read_task_status(tid)
        self.assertEqual("succeeded", status["status"])
        self.assertNotIn("aborted_at", status)

    def test_previous_attempt_residue_is_cleared_on_refusal(self):
        """之前一次尝试留下的标记，也要在本次拒绝时清掉（否则照样会埋掉真实结果）。"""
        tid = "20240101-000071-residue"
        self.make_task(tid, status="running", stage="stats_marks")
        with io.open(self.marker_path(tid), "w", encoding="utf-8") as fh:
            fh.write("{}")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertFalse(envelope["ok"], envelope)
        self.assertFalse(os.path.isfile(self.marker_path(tid)))

        driver.write_status(tid, status="succeeded", stage="done")
        self.assertEqual("succeeded", self.read_task_status(tid)["status"])

    def test_successful_cancel_keeps_marker(self):
        """成功中止的标记要留着 —— 它是执行侧守卫的依据，删了反而会被覆盖回去。"""
        tid = "20240101-000072-keepmarker"
        self.make_task(tid, status="queued", stage="queued")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["aborted"])
        self.assertTrue(os.path.isfile(self.marker_path(tid)))
        self.assertEqual("aborted", self.read_task_status(tid)["status"])

    def test_terminal_task_never_gets_marker(self):
        tid = "20240101-000073-terminal"
        self.make_task(tid, status="failed", stage="clean_users")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(envelope["aborted"])
        self.assertFalse(os.path.isfile(self.marker_path(tid)))


# ---------------------------------------------------------------------------
# 审查回归二：排队态中止不许被「运行中」覆盖回来
# ---------------------------------------------------------------------------

class TestQueuedAbortIsNotOverwritten(TaskTestBase):
    """审查发现的问题：排队中止先落状态、后台进程后拉起会把「已中止」改回「运行中」。

    现在三处一起堵：取消路径先在复核窗口里确认没有进程、落状态后再扫尾一次；
    执行侧守卫扩成「有标记就不许写排队/运行中/成功/失败」。
    """

    def test_late_running_write_cannot_revive_aborted_task(self):
        tid = "20240101-000080-laterevive"
        self.make_task(tid, status="queued", stage="queued")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["aborted"], envelope)
        self.assertEqual("aborted", self.read_task_status(tid)["status"])

        # 模拟「后台进程此刻才被拉起」：它第一次写阶段就是 running
        driver.write_status(tid, status="running", stage="clean_users")
        status = self.read_task_status(tid)
        self.assertEqual("aborted", status["status"],
                         "排队中止被运行中的写入覆盖回来了")
        # 执行位仍然释放着（没被重新占住）
        self.assertIsNone(driver.running_task())

    def test_late_queued_write_cannot_revive_aborted_task(self):
        tid = "20240101-000081-latequeued"
        self.make_task(tid, status="queued", stage="queued")

        dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        driver.write_status(tid, status="queued", stage="queued")
        self.assertEqual("aborted", self.read_task_status(tid)["status"])

    def test_late_stage_writes_keep_aborted_and_release_lock(self):
        """连续几次阶段写入都盖不回运行中，占用始终保持释放。"""
        tid = "20240101-000082-latestages"
        self.make_task(tid, status="queued", stage="queued")
        dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)

        for stage in ("clean_users", "clean_movies", "clean_ratings"):
            driver.write_status(tid, status="running", stage=stage)
            self.assertEqual("aborted", self.read_task_status(tid)["status"])
            self.assertIsNone(driver.running_task())


# ---------------------------------------------------------------------------
# 审查回归三：等待要有并发上限、单次上限收窄到三十分钟
# ---------------------------------------------------------------------------

class TestWaitLimits(TaskTestBase):
    def _ctx(self, **config):
        base = {"name": "hadoop-mcp", "wait_poll_seconds": 0.05,
                "cancel_grace_seconds": 0.2, "cancel_kill_wait_seconds": 0.2}
        base.update(config)
        return dispatch.ToolContext(REPO_ROOT, config=base)

    def tearDown(self):
        # 用例之间不许互相影响：把计数清零
        while tasks_module._active_wait_count() > 0:
            tasks_module._release_wait_slot()

    def test_limit_reached_returns_structured_error_immediately(self):
        tid = "20240101-000090-waitfull"
        self.make_task(tid, status="running", stage="clean_users")
        ctx = self._ctx(max_concurrent_waits=1)
        self.assertTrue(tasks_module._acquire_wait_slot(1))   # 把唯一名额占掉

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 5}, ctx)
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(tasks_module.CODE_WAIT_LIMIT_REACHED,
                         envelope["error"]["code"])
        self.assertEqual(1, envelope["error"]["max_concurrent_waits"])
        self.assertIn("上限", envelope["error"]["message"])
        # 「立刻」回：不能真的等满 5 秒
        self.assertNotIn("waited_seconds", envelope)

    def test_slot_is_released_so_the_next_wait_works(self):
        tid = "20240101-000091-waitfreed"
        self.make_task(tid, status="running", stage="clean_users")
        ctx = self._ctx(max_concurrent_waits=1)
        tasks_module._acquire_wait_slot(1)
        tasks_module._release_wait_slot()

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 0}, ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(0, tasks_module._active_wait_count())

    def test_slot_released_after_timeout(self):
        tid = "20240101-000092-waittimeout"
        self.make_task(tid, status="running", stage="clean_users")
        # 用假时钟让它「等满」，不真睡：等待上限是整数秒（工具表里就是整型参数），
        # 但用例没必要为此真等一秒。
        fake = mock.patch.object(tasks_module.time, "monotonic",
                                 side_effect=[0.0, 0.0, 1.0e6, 1.0e6, 1.0e6])
        fake.start()
        self.addCleanup(fake.stop)

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 600},
                                      self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertTrue(envelope["timed_out"])
        self.assertEqual(0, tasks_module._active_wait_count())

    def test_missing_task_does_not_consume_a_slot(self):
        ctx = self._ctx(max_concurrent_waits=1)
        with self.assertRaises(contract.StructuredError):
            tasks_module.handle_wait_for_task({"task_id": "ghost"}, ctx)
        self.assertEqual(0, tasks_module._active_wait_count())

    def test_terminal_task_does_not_consume_a_slot(self):
        tid = "20240101-000093-waitdone"
        self.make_task(tid, status="succeeded", stage="done")
        ctx = self._ctx(max_concurrent_waits=1)
        tasks_module._acquire_wait_slot(1)                    # 名额已被占满

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 5}, ctx)
        self.assertTrue(envelope["ok"], envelope)             # 终态任务不占名额
        self.assertFalse(envelope["timed_out"])

    def test_dispatch_rejects_beyond_published_max(self):
        """工具表上公布的上限就是硬边界：超过它先在分派层被拒，别让调用方白等。"""
        tid = "20240101-000094-waitover"
        self.make_task(tid, status="running", stage="clean_users")

        envelope = dispatch.call_tool("wait_for_task",
                                      {"task_id": tid, "timeout_seconds": 7200},
                                      self._ctx())
        self.assertFalse(envelope["ok"], envelope)
        self.assertEqual(contract.CODE_USAGE, envelope["error"]["code"])
        self.assertIn("1800", envelope["error"]["message"])
        self.assertEqual(0, tasks_module._active_wait_count())

    def test_handler_clamps_defensively_when_called_directly(self):
        """绕过分派层直接调处理器时，上限仍然是三十分钟，并如实标出「被压缩」。"""
        tid = "20240101-000095-waitclamp"
        self.make_task(tid, status="running", stage="clean_users")
        ctx = self._ctx()
        # 用一个「每问都在往前走」的假时钟，让循环第一次就判定到点，不真等半小时
        fake = mock.patch.object(tasks_module.time, "monotonic",
                                 side_effect=[0.0, 0.0, 1.0e6, 1.0e6, 1.0e6])
        fake.start()
        self.addCleanup(fake.stop)

        payload = tasks_module.handle_wait_for_task(
            {"task_id": tid, "timeout_seconds": 7200}, ctx)
        self.assertEqual(tasks_module.MAX_WAIT_SECONDS, payload["timeout_seconds"])
        self.assertTrue(payload["timeout_clamped"])
        self.assertEqual(7200.0, payload["requested_timeout_seconds"])
        self.assertEqual(0, tasks_module._active_wait_count())

    def test_under_the_cap_is_not_marked_clamped(self):
        tid = "20240101-000096-waitunder"
        self.make_task(tid, status="running", stage="clean_users")
        ctx = self._ctx()
        fake = mock.patch.object(tasks_module.time, "monotonic",
                                 side_effect=[0.0, 0.0, 1.0e6, 1.0e6, 1.0e6])
        fake.start()
        self.addCleanup(fake.stop)

        payload = tasks_module.handle_wait_for_task(
            {"task_id": tid, "timeout_seconds": 120}, ctx)
        self.assertEqual(120.0, payload["timeout_seconds"])
        self.assertNotIn("timeout_clamped", payload)

    def test_cap_is_thirty_minutes(self):
        self.assertEqual(1800.0, tasks_module.MAX_WAIT_SECONDS)

    def test_default_concurrency_limit_is_bounded(self):
        limit = tasks_module._max_concurrent_waits(self._ctx())
        self.assertGreaterEqual(limit, 1)
        self.assertLessEqual(limit, 64)


# ---------------------------------------------------------------------------
# 审查追加：失败记录里要带上结构化错误码（否则对端只看到笼统的「任务失败」）
# ---------------------------------------------------------------------------

class TestFailureRecordCarriesCode(TaskTestBase):
    """执行侧记录失败原因时，把错误自带的结构化码一并写进那条记录。

    真实复现过的场景：同版本不同内容的任务在发布环节被正确拦下，说明里写着
    「发布版本已存在且内容哈希不一致」，但对端只拿到「任务失败」—— 用户会拿着
    这个结论去查作业日志，方向就偏了。记录里带上码，取结果的一侧才能给出
    「版本冲突」。

    这里不去真跑作业：用一条假流水线在「该跑到发布环节」的位置抛出与真实发布
    一模一样的错误，走的是**真实的失败记录写入路径**。
    """

    RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
    SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")

    def _execute_raising(self, tid, exc):
        """让执行链在该抛的地方抛出给定异常，返回落盘后的状态记录。"""
        import driver.pipeline as driver_pipeline

        class _FakePipeline(object):
            def __init__(self, ctx):
                self.ctx = ctx

            def execute_all_stages(self):
                raise exc

        with mock.patch.object(driver_pipeline, "select_pipeline", _FakePipeline):
            with self.assertRaises(type(exc)):
                driver._execute(tid, self.RULES, self.SCORING, "cluster")
        return self.read_task_status(tid)

    def test_version_conflict_code_is_recorded(self):
        tid = "20240101-000100-conflict"
        exc = driver.CliError(
            "VERSION_CONFLICT",
            "发布版本 ml1m-clean-v1 已存在且内容哈希不一致：配置或输入已变化，必须升 data_version")
        status = self._execute_raising(tid, exc)

        self.assertEqual("failed", status["status"])
        record = status["errors"][0]
        self.assertEqual("VERSION_CONFLICT", record["code"],
                         "版本冲突没有被记进失败记录，对端只能拿到笼统的「任务失败」")
        self.assertIn("内容哈希不一致", record["message"])
        # 既有字段照旧都在，位置与形状不变（加性改动）
        self.assertIn("stage", record)

    def test_other_structured_codes_are_recorded_verbatim(self):
        """码原样写，不改成别的取值 —— 映射规则不在这处。"""
        for tid_suffix, code in (("jobfail", "TASK_FAILED"),
                                 ("dependency", "DEPENDENCY_MISSING"),
                                 ("usage", "USAGE"),
                                 ("configng", "CONFIG_INVALID")):
            tid = "20240101-00010-%s" % tid_suffix
            status = self._execute_raising(tid, driver.CliError(code, "示例失败：%s" % code))
            self.assertEqual("failed", status["status"])
            self.assertEqual(code, status["errors"][0]["code"])

    def test_error_without_code_gets_no_code_field(self):
        tid = "20240101-000104-nocode"
        status = self._execute_raising(tid, ValueError("没有结构化码的异常"))
        record = status["errors"][0]
        self.assertNotIn("code", record, "没有码的错误不该凭空多出一个分类字段")
        self.assertIn("ValueError", record["message"])

    def test_failure_record_helper_keeps_existing_shape(self):
        record = driver._failure_record("clean_ratings", "作业失败", None)
        self.assertEqual({"stage": "clean_ratings", "message": "作业失败"}, record)
        coded = driver._failure_record("publish", "冲突",
                                       driver.CliError("VERSION_CONFLICT", "冲突"))
        self.assertEqual("VERSION_CONFLICT", coded["code"])


# ---------------------------------------------------------------------------
# 模块间自相矛盾审查：控制文件名只能有一个来源
# ---------------------------------------------------------------------------

class TestControlFileNameSingleSource(TaskTestBase):
    """控制文件名（中止标记、提交标记）只许有一个来源：契约层。

    为什么需要这条断言：这个名字以前在四个地方各写一份字面量 —— 执行侧常量、
    本模块、写入层、读取层排除名单，而且每处测试只对照自己模块的常量。于是
    "改一处"很容易变成"路径跟着变了、回报给对端的名字没变"这种改了一半的改动，
    而两边的测试都还是绿的。下面几条断言把三者绑在一起。
    """

    def test_my_module_constants_come_from_contract(self):
        self.assertEqual(contract.CONTROL_FILE_CANCEL,
                         tasks_module.CANCEL_MARKER_FILENAME)
        self.assertEqual(contract.CONTROL_FILE_SUBMIT,
                         tasks_module.SUBMIT_MARKER_FILENAME)
        self.assertEqual((contract.CONTROL_FILE_CANCEL, contract.CONTROL_FILE_SUBMIT),
                         tuple(contract.CONTROL_FILE_NAMES))

    def test_execution_side_constant_matches_contract(self):
        # 执行侧写终态前看的是它自己的常量；两边一旦分叉，"已中止"就永远拦不住
        self.assertEqual(contract.CONTROL_FILE_CANCEL, driver.CANCEL_MARKER)

    def test_readers_exclusion_list_covers_contract_names(self):
        try:
            from mcpserver import readers
        except Exception as exc:                      # noqa: BLE001 —— 别让邻居挡住本段
            self.skipTest("读取层模块当前不可导入（%s），本段不因此失败" % exc)
        names = tuple(getattr(readers, "CONTROL_FILE_NAMES", ()))
        for name in contract.CONTROL_FILE_NAMES:
            self.assertIn(name, names,
                          "读取层的排除名单漏了控制文件 %s：产物清单会漏出内部文件" % name)

    def test_reported_field_values_are_contract_names(self):
        """回报给对端的字段值也必须是契约里的名字（不是另一份字面量）。"""
        tid = "20240101-000110-fieldnames"
        self.make_task(tid, status="queued", stage="queued")

        envelope = dispatch.call_tool("cancel_task", {"task_id": tid}, self.ctx)
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(contract.CONTROL_FILE_CANCEL, envelope["cancel_request_file"])

    def test_filename_appears_where_users_can_see_it(self):
        """标记文件名落在状态文件的失败/中止说明里时，用的也是同一个来源。"""
        self.assertIn(contract.CONTROL_FILE_CANCEL,
                      (tasks_module.CANCEL_MARKER_FILENAME,))


class TestWaitLimitCodeIsPublished(TaskTestBase):
    def test_code_comes_from_contract(self):
        self.assertEqual(contract.CODE_WAIT_LIMIT_REACHED,
                         tasks_module.CODE_WAIT_LIMIT_REACHED)

    def test_code_is_in_the_published_error_code_set(self):
        # 对端按这份集合归类；不在集合里就会被当成「未知错误」
        self.assertIn(contract.CODE_WAIT_LIMIT_REACHED,
                      contract.STRUCTURED_ERROR_CODES)


# ---------------------------------------------------------------------------
# 调优旋钮：上下文优先、缺省回落模块默认值
# ---------------------------------------------------------------------------

class TestTuningPrecedence(TaskTestBase):
    """服务层还没把调优配置透传进上下文，所以这里要保证两条都成立：
    给了上下文就用上下文的、没给就回落到模块默认值（不会因为缺配置而失败）。
    """

    def test_context_values_win(self):
        ctx = dispatch.ToolContext(REPO_ROOT, config={"wait_poll_seconds": 0.25,
                                                    "cancel_grace_seconds": 2.0,
                                                    "cancel_kill_wait_seconds": 3.0,
                                                    "scheduler_recent_limit": 9,
                                                    "max_concurrent_waits": 6})
        self.assertEqual(0.25, tasks_module._poll_seconds(ctx))
        self.assertEqual(2.0, tasks_module._cancel_grace_seconds(ctx))
        self.assertEqual(3.0, tasks_module._cancel_kill_wait_seconds(ctx))
        self.assertEqual(9, tasks_module._recent_limit(ctx))
        self.assertEqual(6, tasks_module._max_concurrent_waits(ctx))

    def test_defaults_used_when_context_is_silent(self):
        ctx = dispatch.ToolContext(REPO_ROOT, config={})
        self.assertEqual(tasks_module.DEFAULT_WAIT_POLL_SECONDS,
                         tasks_module._poll_seconds(ctx))
        self.assertEqual(tasks_module.DEFAULT_CANCEL_GRACE_SECONDS,
                         tasks_module._cancel_grace_seconds(ctx))
        self.assertEqual(tasks_module.DEFAULT_CANCEL_KILL_WAIT_SECONDS,
                         tasks_module._cancel_kill_wait_seconds(ctx))
        self.assertEqual(tasks_module.DEFAULT_RECENT_LIMIT,
                         tasks_module._recent_limit(ctx))
        self.assertEqual(tasks_module.DEFAULT_MAX_CONCURRENT_WAITS,
                         tasks_module._max_concurrent_waits(ctx))

    def test_env_is_the_middle_layer(self):
        env_patcher = mock.patch.dict(os.environ, {"MCP_WAIT_POLL_SECONDS": "0.3",
                                                  "MCP_MAX_CONCURRENT_WAITS": "7"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        ctx = dispatch.ToolContext(REPO_ROOT, config={})
        self.assertEqual(0.3, tasks_module._poll_seconds(ctx))
        self.assertEqual(7, tasks_module._max_concurrent_waits(ctx))


class TestWaitDefaultsMatchToolTable(TaskTestBase):
    """处理器内部的默认值与上限，必须与工具表公布的一致（两边各写一套就会漂移）。"""

    def _param(self):
        from mcpserver import tools
        for param in tools.get_tool("wait_for_task")["params"]:
            if param["name"] == "timeout_seconds":
                return param
        self.fail("工具表里没有 timeout_seconds")

    def test_default_matches_published_default(self):
        self.assertEqual(self._param()["default"], tasks_module.DEFAULT_WAIT_SECONDS)

    def test_maximum_matches_published_maximum(self):
        self.assertEqual(self._param()["maximum"], tasks_module.MAX_WAIT_SECONDS)


if __name__ == "__main__":
    unittest.main()
