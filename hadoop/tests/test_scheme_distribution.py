# -*- coding: utf-8 -*-
"""tests/test_scheme_distribution.py —— 方案分发：作业拿到的必须是"本次选中的方案"。

问题背景（本次修复）：两条提交路径（单输入 `submit_stage.sh`、多输入裸 jar）此前都
把仓库**默认**的两份配置写死进 `-files` 与 `--rules/--scoring`。于是用户上传一份
自定义清洗方案、用它发起任务时：驱动端的校验、血缘、报告都按自定义方案走，**作业却
仍按默认方案清洗** —— 用户拿到的结果与他上传的方案对不上，而且没有任何报错。

本文件锁住三件事：
  1. 选中自定义方案时，提交参数里出现的是**自定义方案的路径与单层文件名**，
     默认方案的文件不再出现（避免"同时带两份"）；
  2. 沿用默认方案时，提交参数与改动前完全一致（向后兼容，行为不变）；
  3. 两份选中方案同名时直接拒绝 —— 它们会被分发到同一个名字下互相覆盖。

验证手段是"检查实际拼出来的提交参数"（mock 掉执行侧调用、或用桩程序顶替集群命令），
**不发起任何真实集群作业**。
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, os.path.join(REPO_ROOT, "hadoop"))

from driver.hdfsio import (  # noqa: E402
    DEFAULT_RULES_PATH,
    DEFAULT_SCORING_PATH,
    JobSpec,
    StreamingSubmitter,
)


def _write(path, text):
    """写一个方案文件（内容不重要，测试只看"分发的是哪一份"）。"""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class _TempSchemes(unittest.TestCase):
    """公共夹具：造一对自定义方案文件（名字与默认不同）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ml-scheme-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.rules = _write(os.path.join(self.tmp, "my_rules.v2.json"), '{"config_type": "cleaning_rules"}')
        self.scoring = _write(os.path.join(self.tmp, "my_scoring.v2.json"), '{"config_type": "scoring_scheme"}')


class ScriptModeSchemeTest(_TempSchemes):
    """单输入路径：参数交给 submit_stage.sh（名字由脚本按 basename 推导）。"""

    def _argv(self, sub):
        spec = JobSpec("clean_users.py", "/in/u", "/out/u", reduces=0)
        with mock.patch("driver.run_task.run_shell", return_value=(0, "", "")) as rs:
            sub.submit_job(spec)
        return rs.call_args[0][0]

    def test_custom_scheme_is_distributed(self):
        argv = self._argv(StreamingSubmitter(self.tmp, "t1",
                                             rules=self.rules, scoring=self.scoring))
        self.assertIn("--rules-file", argv)
        self.assertEqual(argv[argv.index("--rules-file") + 1], self.rules)
        self.assertIn("--scoring-file", argv)
        self.assertEqual(argv[argv.index("--scoring-file") + 1], self.scoring)
        # 默认方案不得同时出现（否则作业可能仍读默认那份）
        self.assertNotIn(DEFAULT_RULES_PATH, argv)
        self.assertNotIn(DEFAULT_SCORING_PATH, argv)

    def test_default_scheme_unchanged(self):
        """不传方案时，携带的就是仓库默认的两份 —— 与改动前行为一致。"""
        argv = self._argv(StreamingSubmitter(self.tmp, "t1"))
        self.assertEqual(argv[argv.index("--rules-file") + 1], DEFAULT_RULES_PATH)
        self.assertEqual(argv[argv.index("--scoring-file") + 1], DEFAULT_SCORING_PATH)

    def test_relative_path_resolved_against_repo_root(self):
        """相对路径按仓库根解析：驱动 resolve_path 给的就是这种相对写法。"""
        argv = self._argv(StreamingSubmitter(self.tmp, "t1",
                                             rules="config/cleaning_rules.v1.json",
                                             scoring="config/scoring_scheme.v1.json"))
        self.assertEqual(argv[argv.index("--rules-file") + 1],
                         os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json"))

    def test_same_basename_rejected(self):
        """两份方案同名 → 拒绝（会分发到同一名字下互相覆盖）。"""
        a = _write(os.path.join(self.tmp, "a", "same.json"), "{}")
        b = _write(os.path.join(self.tmp, "b", "same.json"), "{}")
        sub = StreamingSubmitter(self.tmp, "t1", rules=a, scoring=b)
        with self.assertRaises(Exception) as cm:
            self._argv(sub)
        self.assertEqual("USAGE", getattr(cm.exception, "code", None))
        self.assertIn("文件名相同", str(cm.exception))


class RawModeSchemeTest(_TempSchemes):
    """多输入路径：裸 jar 的 `-files` 与 mapper/reducer 参数里必须是选中的方案。"""

    def setUp(self):
        _TempSchemes.setUp(self)
        self._env_patcher = mock.patch(
            "driver.run_task.load_env_shell",
            return_value={"STREAMING_JAR": "/jar/streaming.jar",
                          "PYTHON_BIN": "/usr/bin/python3"})
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)

    def _bash(self, sub):
        spec = JobSpec("stats_marks.py", ["/in/a", "/in/b"], "/out/x", reduces=1)
        with mock.patch("driver.run_task.run_shell", return_value=(0, "", "")) as rs:
            sub.submit_job(spec)
        return rs.call_args[0][0][2]

    def test_custom_scheme_in_files_and_args(self):
        bash = self._bash(StreamingSubmitter(self.tmp, "t1",
                                             rules=self.rules, scoring=self.scoring))
        # 分发的文件列表里是自定义方案的真实路径
        self.assertIn(self.rules, bash)
        self.assertIn(self.scoring, bash)
        # 作业侧命令里是它们的单层文件名（分发名与读取名同一个）
        self.assertIn("--rules my_rules.v2.json", bash)
        self.assertIn("--scoring my_scoring.v2.json", bash)
        # 默认方案不得同时出现
        self.assertNotIn(DEFAULT_RULES_PATH, bash)
        self.assertNotIn(DEFAULT_SCORING_PATH, bash)
        self.assertNotIn("--rules cleaning_rules.v1.json", bash)

    def test_default_scheme_in_files_and_args(self):
        bash = self._bash(StreamingSubmitter(self.tmp, "t1"))
        self.assertIn(DEFAULT_RULES_PATH, bash)
        self.assertIn(DEFAULT_SCORING_PATH, bash)
        self.assertIn("--rules cleaning_rules.v1.json", bash)
        self.assertIn("--scoring scoring_scheme.v1.json", bash)

    def test_same_basename_rejected(self):
        a = _write(os.path.join(self.tmp, "a", "dup.json"), "{}")
        b = _write(os.path.join(self.tmp, "b", "dup.json"), "{}")
        with self.assertRaises(Exception) as cm:
            self._bash(StreamingSubmitter(self.tmp, "t1", rules=a, scoring=b))
        self.assertIn("文件名相同", str(cm.exception))


class SubmitStageShellTest(_TempSchemes):
    """直接验证 `submit_stage.sh`：它自己也会拼 `-files` 与 `--rules/--scoring`。

    做法是搭一个**隔离的假仓库**：把真实的提交脚本拷进去，配一份只导出变量、不做
    任何 PATH 改写的环境脚本，再用桩程序顶替 `hadoop` / `hdfs`（记录参数后退出 0）。
    这样既不碰真实仓库配置，也**不会**发起任何真实集群作业。
    （直接给真实环境加 PATH 前缀是不行的：真实的环境脚本会把 `.vendor` 里的 hadoop
    插到 PATH 最前面，桩会被绕过 —— 这一点在实现时实测踩到过。）
    """

    def setUp(self):
        _TempSchemes.setUp(self)
        real_script = os.path.join(REPO_ROOT, "hadoop", "scripts", "submit_stage.sh")
        self.stub_dir = os.path.join(self.tmp, "stubs")
        os.makedirs(self.stub_dir)
        self.log = os.path.join(self.tmp, "stub.log")
        for name in ("hadoop", "hdfs"):
            path = os.path.join(self.stub_dir, name)
            _write(path, '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "%s"\nexit 0\n' % self.log)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # 假仓库：脚本 + 环境脚本 + 引擎包 + 作业脚本 + 默认方案
        self.fake = os.path.join(self.tmp, "repo")
        self.script = os.path.join(self.fake, "hadoop", "scripts", "submit_stage.sh")
        if not os.path.isfile(real_script):
            self.skipTest("缺少真实的 submit_stage.sh，无法验证脚本侧分发")
        os.makedirs(os.path.dirname(self.script))
        shutil.copyfile(real_script, self.script)
        _write(os.path.join(self.fake, "hadoop", "scripts", "env.sh"),
               'export ML_REPO_ROOT="%s"\n'
               'export STREAMING_JAR="/jar/streaming.jar"\n'
               'export PYTHON_BIN="python3"\n'
               'export PATH="%s:$PATH"\n' % (self.fake, self.stub_dir))
        _write(os.path.join(self.fake, "hadoop", "engine.zip"), "PK-dummy")
        for job in ("users_normalize.py", "_common.py"):
            _write(os.path.join(self.fake, "hadoop", "jobs", job), "# dummy\n")
        self.fake_rules = _write(os.path.join(self.fake, "config", "cleaning_rules.v1.json"), "{}")
        self.fake_scoring = _write(os.path.join(self.fake, "config", "scoring_scheme.v1.json"), "{}")

    def _run(self, extra_args):
        env = dict(os.environ)
        env["ML_TASK_ID"] = "T-TEST"
        env.pop("PATH", None)          # 路径由备好的环境脚本给，避免继承真实 hadoop
        proc = subprocess.run(
            ["bash", self.script, "users_normalize.py", "/in/u", "/out/u"] + extra_args,
            env=env, cwd=self.fake, capture_output=True, text=True, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr[-2000:])
        with open(self.log, encoding="utf-8") as fh:
            return fh.read()

    def _assert_path_in(self, out, path):
        """路径断言容忍分隔符差异。

        submit_stage.sh 在 bash 里用 `/` 拼 `$ML_REPO_ROOT/config/...`，而 Windows 上
        ML_REPO_ROOT 自带反斜杠，拼出来的是混合分隔符（功能等价 —— Windows API 两种
        都收）。逐字符比对会把可用结果误判成失败，所以两边都归一化成 `/` 再比。
        """
        if path in out:
            return
        self.assertIn(path.replace("\\", "/"), out.replace("\\", "/"),
                      "输出里找不到 %r（已容忍分隔符差异）" % path)

    def _assert_path_not_in(self, out, path):
        """与 _assert_path_in 同口径的"不得出现"断言（同样容忍分隔符差异）。"""
        self.assertNotIn(path.replace("\\", "/"), out.replace("\\", "/"),
                         "输出里不应出现 %r（已容忍分隔符差异）" % path)

    def test_custom_scheme_names_reach_the_job_command(self):
        out = self._run(["--rules-file", self.rules, "--scoring-file", self.scoring])
        # -files 里是自定义方案的真实路径
        self._assert_path_in(out, self.rules)
        self._assert_path_in(out, self.scoring)
        # 作业命令里是它们的单层文件名
        self.assertIn("--rules my_rules.v2.json", out)
        self.assertIn("--scoring my_scoring.v2.json", out)
        # 默认方案不再出现（避免同时带两份）
        self._assert_path_not_in(out, self.fake_rules)
        self.assertNotIn("--rules cleaning_rules.v1.json", out)

    def test_default_scheme_keeps_old_behavior(self):
        out = self._run([])
        self._assert_path_in(out, self.fake_rules)
        self._assert_path_in(out, self.fake_scoring)
        self.assertIn("--rules cleaning_rules.v1.json", out)
        self.assertIn("--scoring scoring_scheme.v1.json", out)

    def test_same_basename_rejected_by_script(self):
        a = _write(os.path.join(self.tmp, "a", "dup.json"), "{}")
        b = _write(os.path.join(self.tmp, "b", "dup.json"), "{}")
        env = dict(os.environ)
        env.pop("PATH", None)
        proc = subprocess.run(
            ["bash", self.script, "users_normalize.py", "/in/u", "/out/u",
             "--rules-file", a, "--scoring-file", b],
            env=env, cwd=self.fake, capture_output=True, text=True, timeout=120)
        self.assertEqual(2, proc.returncode)
        self.assertIn("文件名相同", proc.stderr)

    def test_missing_scheme_file_rejected(self):
        env = dict(os.environ)
        env.pop("PATH", None)
        proc = subprocess.run(
            ["bash", self.script, "users_normalize.py", "/in/u", "/out/u",
             "--rules-file", os.path.join(self.tmp, "nope.json")],
            env=env, cwd=self.fake, capture_output=True, text=True, timeout=120)
        self.assertEqual(2, proc.returncode)
        self.assertIn("找不到方案文件", proc.stderr)


if __name__ == "__main__":
    unittest.main()
