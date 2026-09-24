# -*- coding: utf-8 -*-
"""M0 骨架测试：仓库结构、包可导入性、配置校验可用性。

TDD 说明：本文件在 M0 实现之前编写，此时应当**失败**（engine/ jobs/ driver/ 等目录尚不存在）；
建好目录与 __init__.py 后转为通过。

运行方式见 hadoop/scripts/run_tests.sh（它会把 <repo>/hadoop 放进 PYTHONPATH，
因此测试里可以 `from engine import ...`、`from jobs import ...`）。
"""
import json
import os
import subprocess
import sys
import unittest

# .../hadoop/tests/test_skeleton.py -> .../hadoop/tests -> .../hadoop -> repo 根
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HADOOP_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(HADOOP_DIR)

# plan.md §3 仓库结构中 hadoop/ 下的目录
REQUIRED_DIRS = ["engine", "jobs", "driver", "tests", "scripts", "conf", "tools", "tests/fixtures"]
# 需要有 __init__.py、可被 import 的包
REQUIRED_PACKAGES = ["engine", "jobs"]


class TestRepoSkeleton(unittest.TestCase):

    def test_required_directories_exist(self):
        """plan.md §3 要求的目录结构齐备。"""
        missing = [d for d in REQUIRED_DIRS
                   if not os.path.isdir(os.path.join(HADOOP_DIR, d))]
        self.assertEqual([], missing, "缺少目录: %s" % missing)

    def test_required_packages_are_importable(self):
        """engine / jobs 是可导入的包（有 __init__.py）。"""
        for pkg in REQUIRED_PACKAGES:
            with self.subTest(package=pkg):
                init = os.path.join(HADOOP_DIR, pkg, "__init__.py")
                self.assertTrue(os.path.isfile(init), "缺少 %s" % init)

    def test_engine_package_imports(self):
        """engine 包能被 Python 真正导入（不只是文件存在）。"""
        proc = subprocess.run(
            [sys.executable, "-c", "import engine; print(engine.__file__)"],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=HADOOP_DIR),
        )
        self.assertEqual(0, proc.returncode,
                         "import engine 失败:\n%s" % proc.stderr)

    def test_run_tests_script_exists(self):
        """hadoop/scripts/run_tests.sh 存在且是普通文件。"""
        p = os.path.join(HADOOP_DIR, "scripts", "run_tests.sh")
        self.assertTrue(os.path.isfile(p), "缺少 %s" % p)

    def test_configs_are_valid_json_with_expected_identity(self):
        """两份 v1 配置可解析，且 scheme_id/version 与接口文档一致。"""
        with open(os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json"),
                  encoding="utf-8") as f:
            rules = json.load(f)
        with open(os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json"),
                  encoding="utf-8") as f:
            scoring = json.load(f)
        self.assertEqual("cleaning_rules", rules["config_type"])
        self.assertEqual("ml1m-cleaning-default", rules["scheme_id"])
        self.assertEqual("scoring_scheme", scoring["config_type"])
        self.assertEqual("ml1m-quality-default", scoring["scheme_id"])
        self.assertEqual("1.0.0", rules["version"])
        self.assertEqual("1.0.0", scoring["version"])

    def test_validate_configs_script_passes(self):
        """M0 验收：python3 hadoop/tools/validate_configs.py 退出码 0 且 RESULT: PASS。"""
        proc = subprocess.run(
            [sys.executable, os.path.join(HADOOP_DIR, "tools", "validate_configs.py")],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(0, proc.returncode, "校验失败:\n%s\n%s" % (proc.stdout, proc.stderr))
        self.assertIn("RESULT: PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
