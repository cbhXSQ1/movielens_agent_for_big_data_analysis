# -*- coding: utf-8 -*-
"""测试 hadoop/tools/quick_clean.py —— 演示性数据清洗（不经过 Hadoop）。

核心不变量：
  1. 产出与本地 runner / 集群任务**同源同数**（内部就是 run_local，
     因此 summary 的 counts 必须等于 fixtures/expected/counts.json 的手写期望）
  2. **零 Hadoop 依赖**：只在子进程里跑这个脚本，不碰 hdfs、yarn、cluster.sh
  3. stdout 只有一个 JSON 信封；进度走 stderr
  4. --sample 只抽评分表，维表保持全量（引用完整性）；出错的入参给 ok:false
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
TOOL = os.path.join(REPO_ROOT, "hadoop", "tools", "quick_clean.py")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RAW = os.path.join(FIXTURES, "raw")
EXPECTED = os.path.join(FIXTURES, "expected", "counts.json")
PY = sys.executable or "python3"


def run_tool(args, env_extra=None, timeout=300):
    env = dict(os.environ)
    env.setdefault("ML_RAW_DIR", RAW)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen([PY, TOOL] + args, cwd=REPO_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(timeout=timeout)
    return json.loads(out.decode("utf-8")), proc.returncode, err.decode("utf-8")


class TestQuickClean(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="ml-qc-")
        cls.env, cls.rc, cls.err = run_tool(["--out", cls.out, "--task-id", "T-QC"])
        with io.open(EXPECTED, encoding="utf-8") as fh:
            cls.expected = json.load(fh)["counts"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_envelope_and_exit_code(self):
        self.assertEqual(0, self.rc, self.err)
        self.assertTrue(self.env["ok"])
        self.assertEqual("1.0", self.env["interface_version"])
        self.assertEqual("T-QC", self.env["summary"]["task_id"])

    def test_counts_match_hand_written_fixture_expectations(self):
        """同源同数：counts 必须与 fixtures 的手写期望完全一致。"""
        got = self.env["summary"]["counts"]
        self.assertEqual(self.expected["output"], got["output"])
        self.assertEqual(self.expected["quarantine"]["by_rule"],
                         got["quarantine"]["by_rule"])
        self.assertEqual(self.expected["quarantine"]["total"],
                         got["quarantine"]["total"])
        self.assertEqual(self.expected["dedupe"], got["dedupe"])
        self.assertEqual(self.expected["fix"], got["fix"])

    def test_outputs_written(self):
        ver = self.env["summary"]["data_version"]
        d = os.path.join(self.out, "cleaned", ver)
        for table, n in (("ratings", 9), ("users", 10), ("movies", 14)):
            with self.subTest(table=table):
                path = os.path.join(d, "%s.dat" % table)
                self.assertTrue(os.path.isfile(path))
                lines = [l for l in io.open(path, encoding="iso-8859-1") if l.strip()]
                self.assertEqual(n, len(lines))
        with io.open(os.path.join(self.out, "metrics", "after.json"),
                     encoding="utf-8") as fh:
            self.assertAlmostEqual(94.39, json.load(fh)["composite"], places=2)

    def test_stdout_is_pure_json(self):
        """进度必须走 stderr；stdout 只能有一个信封。"""
        proc = subprocess.Popen([PY, TOOL, "--raw", RAW, "--out", self.out],
                                cwd=REPO_ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = proc.communicate()
        json.loads(out.decode("utf-8"))          # 不抛即通过
        self.assertIn("quick_clean:", err.decode("utf-8"))

    def test_sample_keeps_dimension_tables_full(self):
        """--sample N 只抽评分表；维表全量 → 引用完整、结果可复现。"""
        out2 = tempfile.mkdtemp(prefix="ml-qc-sample-")
        try:
            env, rc, err = run_tool(["--out", out2, "--sample", "3"])
            self.assertEqual(0, rc, err)
            c = env["summary"]["counts"]["output"]
            self.assertEqual({"users": 10, "movies": 14}, {"users": c["users"],
                                                           "movies": c["movies"]})
            self.assertLessEqual(c["ratings"], 3)
        finally:
            shutil.rmtree(out2, ignore_errors=True)

    def test_bad_raw_dir_fails_cleanly(self):
        env, rc, _ = run_tool(["--raw", "/nonexistent"])
        self.assertEqual(2, rc)
        self.assertFalse(env["ok"])
        self.assertIn("error", env)


if __name__ == "__main__":
    unittest.main()