# -*- coding: utf-8 -*-
"""M5 测试：hadoop/driver/run_task.py —— CLI 契约（docs/hadoop/agent-interface.md v1.0）。

以**子进程**调用 CLI，逐条验证契约里可判定的部分：
  * stdout 只有一个 JSON 信封，日志走 stderr
  * 退出码 0/2/3/4/5/6 的语义
  * 八个子命令都能出信封；`result` 的字段与接口文档示例同形
  * 同一时刻只允许 1 个运行中任务（TASK_ALREADY_RUNNING）
  * 未完成/失败的任务，`result` 拒绝返回（不编造）
  * data_version 与配置不一致 → VERSION_CONFLICT

端到端那一例用 fixture 当原始数据（`ML_RAW_DIR`）并在临时 `ML_VAR_DIR` 里落盘，
所以跑得快且不污染仓库；需要 HDFS 的发布步骤在不可用时跳过。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
DRIVER = os.path.join(REPO_ROOT, "hadoop", "driver", "run_task.py")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")
PY = sys.executable or "python3"


def load_json(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def hdfs_available():
    try:
        proc = subprocess.Popen(["bash", "-c",
                                 'source "%s" >/dev/null 2>&1; hdfs dfs -ls / >/dev/null 2>&1'
                                 % os.path.join(REPO_ROOT, "hadoop", "scripts", "env.sh")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.wait(timeout=30) == 0
    except Exception:                                   # pragma: no cover
        return False


HDFS_OK = hdfs_available()


def run_cli(args, var_dir=None, env_extra=None, timeout=600):
    """调用 CLI，返回 (envelope | None, rc, stderr)。"""
    env = dict(os.environ)
    env["ML_VAR_DIR"] = var_dir or env.get("ML_VAR_DIR", "")
    env.setdefault("HDFS_BASE", "/data")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen([PY, DRIVER] + list(args), cwd=REPO_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(timeout=timeout)
    text = out.decode("utf-8", "replace").strip()
    try:
        env_obj = json.loads(text)
    except ValueError:
        env_obj = None
    return env_obj, proc.returncode, err.decode("utf-8", "replace")


class TestEnvelope(unittest.TestCase):
    def test_validate_ok(self):
        env, rc, err = run_cli(["validate", "--rules", RULES, "--scoring", SCORING])
        self.assertEqual(0, rc)
        self.assertTrue(env["ok"])
        self.assertEqual("1.0", env["interface_version"])
        self.assertEqual(["errors", "warnings", "versions"],
                         [k for k in ("errors", "warnings", "versions") if k in env])
        self.assertEqual("1.0.0", env["versions"]["rule"])

    def test_schemes_lists_both_defaults(self):
        env, rc, _ = run_cli(["schemes"])
        self.assertEqual(0, rc)
        ids = [s["scheme_id"] for s in env["schemes"]]
        self.assertIn("ml1m-cleaning-default", ids)
        self.assertIn("ml1m-quality-default", ids)
        for s in env["schemes"]:
            self.assertEqual("registered_default", s["status"])

    def test_tasks_empty_is_ok(self):
        tmp = tempfile.mkdtemp()
        try:
            env, rc, _ = run_cli(["tasks"], var_dir=tmp)
            self.assertEqual(0, rc)
            self.assertEqual([], env["tasks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_subcommand_is_usage_error(self):
        env, rc, _ = run_cli(["frobnicate"])
        self.assertEqual(2, rc)
        self.assertFalse(env["ok"])
        self.assertEqual("USAGE", env["error"]["code"])

    def test_stdout_is_pure_json(self):
        """stdout 只能是信封；任何日志都必须走 stderr。"""
        proc = subprocess.Popen([PY, DRIVER, "schemes"], cwd=REPO_ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _err = proc.communicate()
        json.loads(out.decode("utf-8"))          # 不抛即通过
        self.assertTrue(out.decode("utf-8").startswith("{"))


class TestExitCodes(unittest.TestCase):
    def test_config_invalid_is_2(self):
        env, rc, _ = run_cli(["validate", "--rules", os.path.join(FIXTURES, "nope.json")])
        self.assertEqual(2, rc)
        self.assertEqual("CONFIG_INVALID", env["error"]["code"])

    def test_task_not_found_is_4(self):
        tmp = tempfile.mkdtemp()
        try:
            env, rc, _ = run_cli(["status", "--task-id", "nope"], var_dir=tmp)
            self.assertEqual(4, rc)
            self.assertEqual("TASK_NOT_FOUND", env["error"]["code"])
            env, rc, _ = run_cli(["result", "--task-id", "nope"], var_dir=tmp)
            self.assertEqual(4, rc)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_samples_bad_type_is_2(self):
        env, rc, _ = run_cli(["samples", "--task-id", "x", "--type", "bogus"])
        self.assertEqual(2, rc)
        self.assertEqual("USAGE", env["error"]["code"])

    def test_version_conflict_is_6(self):
        tmp = tempfile.mkdtemp()
        try:
            env, rc, _ = run_cli(["start", "--exec", "local",
                                  "--data-version", "ml1m-clean-v9"], var_dir=tmp)
            self.assertEqual(6, rc)
            self.assertEqual("VERSION_CONFLICT", env["error"]["code"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTaskLifecycle(unittest.TestCase):
    """端到端（local 后端，fixture 数据）：start → status → result → samples → report。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ml-driver-")
        env = {"ML_RAW_DIR": os.path.join(FIXTURES, "raw"),
               "HDFS_BASE": "/data/driver-test"}
        cls.env = env
        cls.start, cls.rc, cls.err = run_cli(
            ["start", "--exec", "local", "--foreground", "--tag", "test"],
            var_dir=cls.tmp, env_extra=env)
        cls.tid = (cls.start or {}).get("task_id", "")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def cli(self, args):
        return run_cli(args, var_dir=self.tmp, env_extra=self.env)

    def test_start_succeeds(self):
        self.assertEqual(0, self.rc, self.err[-1000:])
        self.assertTrue(self.start["ok"])
        self.assertEqual("succeeded", self.start["status"])
        self.assertTrue(self.start["task_id"])
        self.assertTrue(os.path.isdir(self.start["task_dir"]))

    def test_status_reports_done(self):
        env, rc, _ = self.cli(["status", "--task-id", self.tid])
        self.assertEqual(0, rc)
        self.assertEqual("succeeded", env["status"])
        self.assertEqual("done", env["stage"])
        self.assertEqual(100, env["progress_percent"])
        self.assertEqual(9, env["stage_total"])

    def test_result_shape_matches_contract(self):
        env, rc, _ = self.cli(["result", "--task-id", self.tid])
        self.assertEqual(0, rc, env)
        for key in ("task_id", "status", "data_version", "versions",
                    "time_boundaries", "counts", "scores", "quarantine_summary",
                    "paths", "limitations"):
            with self.subTest(key=key):
                self.assertIn(key, env)
        self.assertEqual({"rule", "scoring", "policy", "operator_library"},
                         set(env["versions"]))
        self.assertEqual("1.0.0", env["versions"]["rule"]["version"])
        self.assertEqual({"T1", "T2"}, set(env["time_boundaries"]))
        self.assertEqual({"input", "output", "quarantine", "dedupe", "fix"},
                         set(env["counts"]))
        self.assertEqual({"before", "after", "delta", "metrics"}, set(env["scores"]))
        self.assertEqual({"before", "after"}, set(env["scores"]["metrics"]))
        for k in ("task_dir", "cleaned_dir", "quarantine_dir", "metrics_dir",
                  "report_md", "report_json", "published_dir"):
            self.assertIn(k, env["paths"])
        self.assertTrue(env["limitations"])

    def test_result_counts_match_the_fixture(self):
        env, _rc, _ = self.cli(["result", "--task-id", self.tid])
        expected = load_json(os.path.join(FIXTURES, "expected", "counts.json"))["counts"]
        self.assertEqual(expected["output"], env["counts"]["output"])
        self.assertEqual(expected["quarantine"]["by_rule"],
                         env["counts"]["quarantine"]["by_rule"])
        self.assertEqual(expected["dedupe"], env["counts"]["dedupe"])
        self.assertEqual(expected["fix"], env["counts"]["fix"])

    def test_metrics_has_18_entries_each_side(self):
        env, _rc, _ = self.cli(["result", "--task-id", self.tid])
        self.assertEqual(18, len(env["scores"]["metrics"]["before"]))
        self.assertEqual(18, len(env["scores"]["metrics"]["after"]))

    def test_samples_quarantine_and_cleaned(self):
        env, rc, _ = self.cli(["samples", "--task-id", self.tid, "--type", "quarantine",
                               "--table", "movies", "--n", "2"])
        self.assertEqual(0, rc)
        self.assertEqual("quarantine", env["type"])
        # 契约 §4.6 的示例里 total_available 是**全局**隔离总数（--table ratings 时
        # 给的是 100830，而非 ratings 一张表的数），故这里等于 fixture 的隔离总数
        self.assertEqual(21, env["total_available"])
        for s in env["samples"]:
            self.assertEqual({"line_no", "raw_line", "rule_id", "stage", "reason"},
                             set(s))
        env, rc, _ = self.cli(["samples", "--task-id", self.tid, "--type", "cleaned",
                               "--table", "users", "--n", "3"])
        self.assertEqual(0, rc)
        self.assertEqual(10, env["total_available"])
        self.assertEqual(3, len(env["samples"]))

    def test_report_md_and_json(self):
        env, rc, _ = self.cli(["report", "--task-id", self.tid, "--format", "md"])
        self.assertEqual(0, rc)
        self.assertIn("# 迭代一 数据质量评估报告", env["report"])
        self.assertIn("五维评分", env["report"])
        self.assertIn(self.tid, env["report"])
        env, rc, _ = self.cli(["report", "--task-id", self.tid, "--format", "json"])
        self.assertEqual(0, rc)
        self.assertEqual("succeeded", env["report"]["status"])

    def test_task_is_listed(self):
        env, rc, _ = self.cli(["tasks"])
        self.assertEqual(0, rc)
        self.assertIn(self.tid, [t["task_id"] for t in env["tasks"]])

    def test_repeat_run_has_identical_cleaned_hash(self):
        """幂等：同输入 + 同配置重跑，cleaned 三表内容哈希一致（接口 §2）。"""
        import hashlib
        first = {}
        for table in ("users", "movies", "ratings"):
            path = os.path.join(self.start["task_dir"], "cleaned", "ml1m-clean-v1",
                                "%s.dat" % table)
            with io.open(path, "rb") as fh:
                first[table] = hashlib.sha256(fh.read()).hexdigest()
        env, rc, err = self.cli(["start", "--exec", "local", "--foreground",
                                 "--force"])
        self.assertEqual(0, rc, err[-800:])
        for table, want in first.items():
            path = os.path.join(env["task_dir"], "cleaned", "ml1m-clean-v1",
                                "%s.dat" % table)
            with io.open(path, "rb") as fh:
                self.assertEqual(want, hashlib.sha256(fh.read()).hexdigest(),
                                 "%s 的 cleaned 哈希应可复现" % table)


class TestConcurrency(unittest.TestCase):
    def test_second_start_is_rejected_while_running(self):
        """同一时刻只允许 1 个运行中任务（契约 §2）。"""
        tmp = tempfile.mkdtemp(prefix="ml-driver-busy-")
        try:
            # 手写一个「运行中」状态，模拟后台任务
            tid = "20260101-000000-abcdef"
            d = os.path.join(tmp, "tasks", tid)
            os.makedirs(d)
            with io.open(os.path.join(d, "status.json"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"task_id": tid, "status": "running",
                                     "stage": "clean_ratings"}))
            env, rc, _ = run_cli(["start", "--exec", "local"], var_dir=tmp,
                                 env_extra={"ML_RAW_DIR": os.path.join(FIXTURES, "raw")})
            self.assertEqual(2, rc)
            self.assertEqual("TASK_ALREADY_RUNNING", env["error"]["code"])
            self.assertEqual(tid, env["error"]["task_id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_result_refuses_unfinished_task(self):
        tmp = tempfile.mkdtemp(prefix="ml-driver-unfin-")
        try:
            tid = "20260101-000000-ghijkl"
            d = os.path.join(tmp, "tasks", tid)
            os.makedirs(d)
            with io.open(os.path.join(d, "status.json"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"task_id": tid, "status": "running",
                                     "stage": "clean_movies"}))
            env, rc, _ = run_cli(["result", "--task-id", tid], var_dir=tmp)
            self.assertEqual(5, rc)
            self.assertEqual("TASK_NOT_FINISHED", env["error"]["code"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_result_refuses_failed_task(self):
        tmp = tempfile.mkdtemp(prefix="ml-driver-failed-")
        try:
            tid = "20260101-000000-mnopqr"
            d = os.path.join(tmp, "tasks", tid)
            os.makedirs(d)
            with io.open(os.path.join(d, "status.json"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"task_id": tid, "status": "failed", "stage": "clean_ratings",
                                     "errors": [{"stage": "ratings_dedupe",
                                                 "job": "ratings_dedupe",
                                                 "exit_code": 1,
                                                 "message": "stderr 摘要"}]}))
            env, rc, _ = run_cli(["result", "--task-id", tid], var_dir=tmp)
            self.assertEqual(3, rc)
            self.assertEqual("TASK_FAILED", env["error"]["code"])
            self.assertEqual("ratings_dedupe", env["error"]["job"])
            self.assertEqual(1, env["error"]["exit_code"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
