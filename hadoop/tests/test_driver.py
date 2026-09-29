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
所以跑得快且不污染仓库。发布（publish）是**集群模式专属**的收尾环节（D-015）：
local 模式不触碰 HDFS，也不往 HDFS 写任何东西 —— 见 TestPublishGuard。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_scope_must_be_full_or_sample(self):
        """D-016：--scope 只接受 full|sample；非法值 = USAGE(2)。"""
        tmp = tempfile.mkdtemp()
        try:
            env, rc, _ = run_cli(["start", "--exec", "local", "--scope", "bogus"],
                                 var_dir=tmp,
                                 env_extra={"ML_RAW_DIR": os.path.join(FIXTURES, "raw")})
            self.assertEqual(2, rc)
            self.assertEqual("USAGE", env["error"]["code"])
            self.assertIn("--scope", env["error"]["message"])
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

    def test_report_documents_evidence_vs_detect(self):
        """报告必须写清 evidence 与 detect 的 4 处差异（decisions.md D-011）。

        这是「不编造、不含糊」的一部分：执行口径以 detect 为准，
        但要把两套数字并列，免得评审看到 M8=1 组、R9=17 人 以为算错了。
        """
        env, rc, _ = self.cli(["report", "--task-id", self.tid, "--format", "json"])
        self.assertEqual(0, rc)
        notes = env["report"]["rule_notes"]["notes"]
        self.assertEqual(["U2", "U3", "M8", "R9"], [n["rule_id"] for n in notes])
        for n in notes:
            with self.subTest(rule=n["rule_id"]):
                for key in ("detect_semantics", "detect_observed", "evidence_says",
                            "why_different", "contract_impact"):
                    self.assertTrue(n.get(key), key)
        # 每条的「配置这么说」与「实测如此」都必须在报告里出现
        joined = " ".join(n["evidence_says"] + n["detect_observed"] for n in notes)
        for token in ("records: 123", "measured_hits: 202", "groups: 218",
                      "matched_users: 30", "369", "201", "17 人", "1 组"):
            self.assertIn(token, joined)

    def test_report_md_carries_rule_notes_section(self):
        env, rc, _ = self.cli(["report", "--task-id", self.tid, "--format", "md"])
        self.assertEqual(0, rc)
        self.assertIn("## 5 规则口径说明", env["report"])
        for rid in ("U2", "U3", "M8", "R9"):
            self.assertIn(rid, env["report"])
        self.assertIn("以 `detect` 为准", env["report"])

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

    def test_local_mode_published_dir_is_null(self):
        """D-015：local 模式不发布，result.paths.published_dir 必须为 null。

        之前这里写死 HDFS 路径（/data/published/...），端到端用例还会真的
        把 fixture 结果传进 HDFS —— 本地模式对 Hadoop 的隐性依赖与副作用。
        """
        env, rc, _ = self.cli(["result", "--task-id", self.tid])
        self.assertEqual(0, rc)
        self.assertIsNone(env["paths"]["published_dir"])

    def test_local_mode_status_published_is_null(self):
        """local 模式不发布：status.json 的 published 必须是 null（D-015）。"""
        st = load_json(os.path.join(self.tmp, "tasks", self.tid, "status.json"))
        self.assertIsNone(st["published"])
        self.assertEqual("done", st["stage"])        # publish 是 no-op 但 stage 序列不变

    def test_default_scope_is_full(self):
        """D-016：start 不传 --scope 时默认 full（全量正式口径）并落盘 status.json。"""
        st = load_json(os.path.join(self.tmp, "tasks", self.tid, "status.json"))
        self.assertEqual("full", st["scope"])

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


class TestPublishGuard(unittest.TestCase):
    """D-015/D-017：发布是集群模式专属；local 零 HDFS 调用；发布冲突保护跨任务生效。

    进程内测试直接验证 `Runner.publish`：
    - local 模式：不发起任何 `run_shell`（含探测）；
    - cluster 模式：冲突比对以**发布目录里的哈希清单**为准（D-017），
      只读本任务目录记录拦不住新 task_id 首次发布覆盖旧发布。
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, REPO_ROOT)
        import hadoop.driver.run_task as rt          # noqa: E402
        cls.rt = rt

    def _runner(self, mode):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="ml-pubguard-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        old = os.environ.get("ML_VAR_DIR")
        os.environ["ML_VAR_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__(
            "ML_VAR_DIR", old) if old is not None else os.environ.pop("ML_VAR_DIR", None))
        return self.rt.Runner("pubguard-%s" % mode, RULES, SCORING, mode)

    def _prep_cleaned(self, runner, content="x\n"):
        version = runner.schemes.rules["data_version"]["id"]
        cleaned = os.path.join(runner.d, "cleaned", version)
        os.makedirs(cleaned)
        for t in ("users", "movies", "ratings"):
            with io.open(os.path.join(cleaned, "%s.dat" % t), "w",
                         encoding="iso-8859-1") as fh:
                fh.write(content)
        return cleaned

    def _local_hashes(self, runner):
        import hashlib
        version = runner.schemes.rules["data_version"]["id"]
        cleaned = os.path.join(runner.d, "cleaned", version)
        out = {}
        for t in ("users", "movies", "ratings"):
            with io.open(os.path.join(cleaned, "%s.dat" % t), "rb") as fh:
                out[t] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def test_local_mode_never_touches_hdfs(self):
        runner = self._runner("local")
        with mock.patch.object(self.rt, "run_shell") as rs:
            out = runner.publish({"x": 1})
        self.assertIsNone(out)
        rs.assert_not_called()                        # 连 -test 探测都不许有

    def test_cluster_mode_fresh_publish(self):
        """全新发布：目录不存在 → 不比对 → put 三表 + 写元文件。"""
        runner = self._runner("cluster")
        self._prep_cleaned(runner)
        calls = []
        with mock.patch.object(self.rt, "run_shell",
                               side_effect=lambda *a, **k: calls.append(a) or (1, "", "")):
            out = runner.publish({})
        self.assertTrue(out, "cluster 模式必须返回发布信息")
        self.assertEqual(3, len(calls))               # exists + put 三表 + put 元文件
        self.assertIn("/data/published/ml1m-clean-v1", out["dir"])

    def test_reuse_ok_when_hashes_identical(self):
        """幂等重发布：发布目录哈希清单与本次相同 → 放行（接口 §2 幂等）。"""
        runner = self._runner("cluster")
        self._prep_cleaned(runner)
        same = json.dumps(self._local_hashes(runner))
        seq = iter([(0, "", ""),                      # exists → 目录在
                    (0, same, ""),                    # cat 元文件 → 哈希一致
                    (0, "", ""),                      # put 三表
                    (0, "", "")])                     # put 元文件
        with mock.patch.object(self.rt, "run_shell",
                               side_effect=lambda *a, **k: next(seq)):
            runner.publish({})                        # 不抛即通过

    def test_conflict_when_hashes_differ(self):
        """D-017：发布目录哈希清单与本次不同 → VERSION_CONFLICT，且不 put。"""
        runner = self._runner("cluster")
        self._prep_cleaned(runner)
        other = {"users": "a" * 64, "movies": "b" * 64, "ratings": "c" * 64}
        seq = iter([(0, "", ""),                      # exists → 目录在
                    (0, json.dumps(other), "")])      # cat 元文件 → 哈希不同
        calls = []
        with mock.patch.object(self.rt, "run_shell",
                               side_effect=lambda *a, **k: calls.append(a) or next(seq)):
            with self.assertRaises(self.rt.CliError) as cm:
                runner.publish({})
        self.assertEqual("VERSION_CONFLICT", cm.exception.code)
        self.assertEqual(2, len(calls))               # 比对失败即停，没有 put

    def test_legacy_publish_without_meta_falls_back_to_content_hash(self):
        """旧发布（无元文件）：现算目录三表哈希比对，一致则放行并补元文件。"""
        runner = self._runner("cluster")
        self._prep_cleaned(runner)
        same = self._local_hashes(runner)
        seq = iter([(0, "", ""),                      # exists → 目录在
                    (1, "", ""),                      # cat 元文件 → 不存在
                    (0, same["users"] + "  -", ""),   # cat users | sha256sum
                    (0, same["movies"] + "  -", ""),  # cat movies | sha256sum
                    (0, same["ratings"] + "  -", ""), # cat ratings | sha256sum
                    (0, "", ""),                      # put 三表
                    (0, "", "")])                     # put 元文件
        calls = []
        with mock.patch.object(self.rt, "run_shell",
                               side_effect=lambda *a, **k: calls.append(a) or next(seq)):
            runner.publish({})                        # 不抛即通过
        self.assertEqual(7, len(calls))


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
