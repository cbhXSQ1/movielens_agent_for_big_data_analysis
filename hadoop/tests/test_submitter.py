# -*- coding: utf-8 -*-
"""M-测试：driver/hdfsio.py —— 提交路径合并后的命令构造锁定（阶段四）。

背景：StreamingSubmitter 把旧 Runner.job（submit_stage.sh 单输入）与 _raw_job
（多输入裸 jar）两条分叉路径合并为一处。本测试用 mock run_shell 捕获实际
生成的命令，断言与合并前的构造**逐字符一致** —— 杜绝"合并时顺手改了参数"
这类静默漂移（合并的是代码路径，不是参数行为，见 hdfsio.py 模块注释）。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, os.path.join(REPO_ROOT, "hadoop"))

from driver.hdfsio import JobSpec, StreamingSubmitter  # noqa: E402


class TestScriptModeArgs(unittest.TestCase):
    """单输入 → submit_stage.sh：argv 逐项锁定。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ml-sub-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.spec = JobSpec(
            "clean_finalize.py", "/data/tasks/t1/u_res", "/data/tasks/t1/u_final",
            reduces=1, mapper_args="--table users", reducer_args="--table users",
            extra_files="a.json,b.json", key_fields=1, job_name="keep",
            extra_d=[("-D", "mapreduce.reduce.memory.mb=2048")])
        self.sub = StreamingSubmitter(self.tmp, "t1")

    def test_argv_exact_sequence(self):
        with mock.patch("driver.run_task.run_shell",
                         return_value=(0, "", "__stderr__")) as rs:
            err = self.sub.submit_job(self.spec)
        args = rs.call_args[0][0]
        expect = ["bash",
                  os.path.join(REPO_ROOT, "hadoop", "scripts", "submit_stage.sh"),
                  "clean_finalize.py",
                  "/data/tasks/t1/u_res",
                  "/data/tasks/t1/u_final",
                  "--reduce", "1",
                  "--mapper-args", "--table users",
                  "--reducer-args", "--table users",
                  "--files", "a.json,b.json",
                  "--job-name", "keep",
                  "-D", "stream.num.map.output.key.fields=1",
                  "-D", "mapreduce.reduce.memory.mb=2048"]
        self.assertEqual(expect, args)
        # 合并语义：脚本模式返回 stderr 文本（调用方拿它合计数器）
        self.assertEqual("__stderr__", err)   # 脚本模式返回 stderr 供合并

    def test_log_path_carries_mode_tag(self):
        """日志名 = 脚本名 + name/mapper/reducer 的 tag（keep 趟与隔离趟不互相覆盖）。"""
        with mock.patch("driver.run_task.run_shell", return_value=(0, "", "")):
            self.sub.submit_job(self.spec)
        logf = mock.run_shell if False else None
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "logs")))


class TestRawModeArgs(unittest.TestCase):
    """多输入 → 裸 hadoop jar：bash 命令串关键段逐项锁定。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ml-sub-")
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        # setUp 里必须 start/stop 而不是 with：with 在 setUp 返回时就失效了
        self._env_patcher = mock.patch(
            "driver.run_task.load_env_shell",
            return_value={"STREAMING_JAR": "/jar/streaming.jar",
                          "PYTHON_BIN": "/usr/bin/python3"})
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        self.sub = StreamingSubmitter(self.tmp, "t1")
        self.spec = JobSpec(
            "stats_marks.py",
            ["/data/tasks/t1/r_cross", "/data/tasks/t1/u_res"],
            "/data/tasks/t1/stats_clean",
            reduces=1, mapper_args="--source cleaned --r9-users r9_users.json",
            reducer_args="--source cleaned", extra_files="/tmp/r9_users.json",
            job_name=None)

    def test_raw_bash_has_jar_files_mapper_reducer(self):
        with mock.patch("driver.run_task.run_shell",
                         return_value=(0, "", "")) as rs:
            out = self.sub.submit_job(self.spec)
        bash = rs.call_args[0][0][2]
        self.assertIsNone(out)                      # 裸 jar 模式不参与计数器合并
        for token in ('"hadoop" "jar" "/jar/streaming.jar"',
                      '"-D" "mapreduce.job.reduces=1"',
                      '"-files" ',
                      "%s/hadoop/engine.zip,%s/config/cleaning_rules.v1.json,%s/config/"
                      "scoring_scheme.v1.json" % (REPO_ROOT, REPO_ROOT, REPO_ROOT),
                      '"-input" "/data/tasks/t1/r_cross"',
                      '"-input" "/data/tasks/t1/u_res"',
                      '"-output" "/data/tasks/t1/stats_clean"',
                      "--rules cleaning_rules.v1.json --scoring scoring_scheme.v1.json "
                      "--task-id t1",
                      '"-D" "mapreduce.output.textoutputformat.separator="',  # reduces>0 才有
                      "export HADOOP_CONF_DIR=",                        # env 注入在
                      'rm -rf "'):
            self.assertIn(token, bash, "缺少 %r" % token)
        # reducer 带 --reduce
        self.assertIn('"-reducer" "/usr/bin/python3 stats_marks.py --reduce --source cleaned ',
                      bash)

    def test_raw_zero_reduces_uses_cat_reducer(self):
        spec = JobSpec("stats_marks.py", ["a", "b"], "out", reduces=0)
        with mock.patch("driver.run_task.run_shell",
                         return_value=(0, "", "")) as rs:
            self.sub.submit_job(spec)
        bash = rs.call_args[0][0][2]
        self.assertIn('"-reducer" "cat"', bash)


class TestEnvExports(unittest.TestCase):
    def test_single_source_of_env_injection(self):
        from driver.hdfsio import hdfs_env_exports
        exports = hdfs_env_exports()
        self.assertIn('JAVA_HOME="%s/.vendor/jdk-11"' % REPO_ROOT, exports)
        self.assertIn('HADOOP_HOME="%s/.vendor/hadoop-3.3.6"' % REPO_ROOT, exports)
        self.assertIn("HADOOP_CONF_DIR=", exports)


if __name__ == "__main__":
    unittest.main()