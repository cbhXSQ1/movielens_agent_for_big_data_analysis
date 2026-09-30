# -*- coding: utf-8 -*-
"""M-测试（v1.1）：精细任务 —— 参数形状与依赖错误码（不跑真集群）。

覆盖（接口文档 §4.3/§4.9 + 拍板 #7/#8/#9）：
  * score 的 --source 必填/枚举；clean 仅集群（local 拒绝）
  * DEPENDENCY_MISSING 三例：缺 from-task / 目标任务不存在 / 目标任务缺产物 /
    发布区不存在
  * start --task-type clean 的后台 spawn 参数透传（mock Popen）
  * clean 任务 result 形状：scores 空 + scored:false（mock 全链执行）
真集群验收（clean 与 full 逐行一致、score 与 full 侧一致）见冒烟记录。
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
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")


def run_cli(*args, env_extra=None, timeout=120):
    env = dict(os.environ)
    env["ML_VAR_DIR"] = tempfile.mkdtemp(prefix="ml-v11-")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen([sys.executable, DRIVER] + list(args),
                            cwd=REPO_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(timeout=timeout)
    return json.loads(out.decode("utf-8")), proc.returncode, env["ML_VAR_DIR"]


class TestScoreArgs(unittest.TestCase):
    def test_source_required(self):
        env, rc, _d = run_cli("score")
        self.assertEqual(2, rc)
        self.assertEqual("USAGE", env["error"]["code"])
        self.assertIn("--source", env["error"]["message"])

    def test_source_enum(self):
        env, rc, _d = run_cli("score", "--source", "bogus")
        self.assertEqual(2, rc)
        self.assertIn("raw / published / task", env["error"]["message"])

    def test_task_source_requires_from_task(self):
        env, rc, _d = run_cli("score", "--source", "task")
        self.assertEqual(2, rc)
        self.assertEqual("DEPENDENCY_MISSING", env["error"]["code"])
        self.assertIn("--from-task", env["error"]["message"])


class TestCleanOnlyClusterOnly(unittest.TestCase):
    def test_clean_rejected_in_local_mode(self):
        env, rc, _d = run_cli("start", "--exec", "local", "--task-type", "clean")
        self.assertEqual(2, rc)
        self.assertEqual("USAGE", env["error"]["code"])
        self.assertIn("仅支持集群", env["error"]["message"])

    def test_clean_spawn_passes_task_type(self):
        """后台 spawn 必须带 --task-type clean（_run 据此选 CleanPipeline）。"""
        sys.path.insert(0, REPO_ROOT)
        import hadoop.driver.run_task as rt
        import hadoop.driver.commands as cmds
        tmp = tempfile.mkdtemp(prefix="ml-v11spawn-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        old = os.environ.get("ML_VAR_DIR")
        os.environ["ML_VAR_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__(
            "ML_VAR_DIR", old) if old is not None else os.environ.pop("ML_VAR_DIR", None))
        with mock.patch.object(cmds.subprocess, "Popen") as popen:
            with mock.patch.object(rt, "running_task", return_value=None):
                with mock.patch.object(rt, "new_task_id",
                                       return_value="v11-clean-spawn-000001"):
                    cls = cmds.find_command("start")
                    opts = {"scope": "sample", "exec": "cluster",
                            "task-type": "clean", "tag": "",
                            "rules": RULES, "scoring": SCORING}
                    cls(opts, []).execute_command()
        args = popen.call_args[0][0]
        self.assertIn("--task-type", args)
        self.assertEqual("clean", args[args.index("--task-type") + 1])
        self.assertIn("_run", args)


class TestDependencyMissing(unittest.TestCase):
    """DEPENDENCY_MISSING 三例（进程内直调 _execute_score，不跑集群作业）。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, REPO_ROOT)
        import hadoop.driver.run_task as rt
        cls.rt = rt

    def _runner_env(self):
        tmp = tempfile.mkdtemp(prefix="ml-v11dep-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        old = os.environ.get("ML_VAR_DIR")
        os.environ["ML_VAR_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__(
            "ML_VAR_DIR", old) if old is not None else os.environ.pop("ML_VAR_DIR", None))
        return tmp

    def test_missing_from_task(self):
        self._runner_env()
        with self.assertRaises(self.rt.CliError) as cm:
            self.rt._execute_score("t1", SCORING, "task", from_task=None)
        self.assertEqual("DEPENDENCY_MISSING", cm.exception.code)

    def test_from_task_does_not_exist(self):
        self._runner_env()
        with self.assertRaises(self.rt.CliError) as cm:
            self.rt._execute_score("t1", SCORING, "task", from_task="nope-000000-000000")
        self.assertEqual("DEPENDENCY_MISSING", cm.exception.code)
        self.assertIn("不存在", cm.exception.message)

    def test_from_task_missing_cleaned_product(self):
        tmp = self._runner_env()
        d = os.path.join(tmp, "tasks", "ok-task-000000-000001")
        os.makedirs(d)
        with io.open(os.path.join(d, "status.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"status": "succeeded", "data_version": "ml1m-clean-v1"}))
        with self.assertRaises(self.rt.CliError) as cm:
            self.rt._execute_score("t1", SCORING, "task",
                                   from_task="ok-task-000000-000001")
        self.assertEqual("DEPENDENCY_MISSING", cm.exception.code)
        self.assertIn("cleaned", cm.exception.message)

    def test_published_missing(self):
        """发布区不存在：mock run_shell 全部失败 → -test 探测 rc!=0 → 拒绝。"""
        self._runner_env()
        with mock.patch.object(self.rt, "run_shell",
                               return_value=(1, "", "")):
            with self.assertRaises(self.rt.CliError) as cm:
                self.rt._execute_score("t1", SCORING, "published")
        self.assertEqual("DEPENDENCY_MISSING", cm.exception.code)
        self.assertIn("发布区", cm.exception.message)


class TestCleanResultShape(unittest.TestCase):
    def test_clean_result_scored_false(self):
        """clean 任务收尾：scores 空 + scored:false（mock 掉集群执行）。"""
        sys.path.insert(0, REPO_ROOT)
        import hadoop.driver.run_task as rt
        tmp = tempfile.mkdtemp(prefix="ml-v11cln-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        old = os.environ.get("ML_VAR_DIR")
        os.environ["ML_VAR_DIR"] = tmp
        self.addCleanup(lambda: os.environ.__setitem__(
            "ML_VAR_DIR", old) if old is not None else os.environ.pop("ML_VAR_DIR", None))
        runner = rt.Runner("cln-1", RULES, SCORING, "cluster", task_type="clean")
        runner._write_report({"output": {"ratings": 10}}, {}, {})
        res = rt.read_json(os.path.join(runner.task_dir, "result.json"))
        self.assertEqual("clean", res["task_type"])
        self.assertFalse(res["scored"])
        self.assertEqual({}, res["scores"])
        self.assertEqual(rt.CLEAN_STAGES, runner.stage_list)


if __name__ == "__main__":
    unittest.main()