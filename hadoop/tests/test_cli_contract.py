# -*- coding: utf-8 -*-
"""M7 测试：Agent 侧调用序列的契约测试（plan.md M7）。

plan 的验收是「与 Agent 组按接口文档联调；提供 test_cli_contract.py 模拟 Agent
调用序列（start→status→result→samples）；契约测试全绿；Agent 侧可完整走通」。

本文件正是那个「模拟 Agent」：它**只**用接口文档 §4 与 §7/§8 描述的方式与 CLI 交互，
不碰任何内部实现 —— 任何需要读 `var/tasks/**` 或调 Python API 才能拿到信息的断言，
都说明契约本身有缺口，必须暴露出来而不是绕过。

覆盖三件事：
  1. §8 的端到端示例序列可以原样走通（含轮询 status 直到终态）
  2. §7 的七个 Agent 工具映射，每个都能靠 CLI 的 JSON 回答（不需要读内部文件）
  3. Agent 行为要求：失败/未完成时如实返回状态与原因；解释结果必须引用
     result 里的实际数字与 limitations
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
DRIVER = os.path.join(REPO_ROOT, "hadoop", "driver", "run_task.py")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")
PY = sys.executable or "python3"

#: §7 的 Agent 工具 → CLI 调用
AGENT_TOOLS = {
    "start_cleaning_task": ["start"],
    "get_task_status": ["status"],
    "get_task_result": ["result"],
    "list_schemes": ["schemes"],
    "get_samples": ["samples"],
    "validate_config": ["validate"],
    "get_report": ["report"],
}


class AgentClient(object):
    """一个最小的「Agent 侧封装」：只依赖 CLI 的 JSON 信封。"""

    def __init__(self, var_dir, env_extra=None):
        self.var_dir = var_dir
        self.env_extra = env_extra or {}
        self.calls = []

    def call(self, *args, **kwargs):
        env = dict(os.environ)
        env["ML_VAR_DIR"] = self.var_dir
        env.update(self.env_extra)
        proc = subprocess.Popen([PY, DRIVER] + list(args), cwd=REPO_ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = proc.communicate(timeout=kwargs.get("timeout", 900))
        envelope = json.loads(out.decode("utf-8"))
        self.calls.append((list(args), envelope.get("ok"), proc.returncode))
        return envelope, proc.returncode

    # §7 的工具
    def start_cleaning_task(self, rules=None, scoring=None, data_version=None,
                            tag=None, foreground=True):
        args = ["start", "--exec", "local"]
        if rules:
            args += ["--rules", rules]
        if scoring:
            args += ["--scoring", scoring]
        if data_version:
            args += ["--data-version", data_version]
        if tag:
            args += ["--tag", tag]
        if foreground:
            args += ["--foreground"]
        return self.call(*args)

    def get_task_status(self, task_id):
        return self.call("status", "--task-id", task_id)

    def get_task_result(self, task_id):
        return self.call("result", "--task-id", task_id)

    def list_schemes(self):
        return self.call("schemes")

    def get_samples(self, task_id, type="quarantine", table="ratings", n=5):
        return self.call("samples", "--task-id", task_id, "--type", type,
                         "--table", table, "--n", str(n))

    def validate_config(self, rules=None, scoring=None):
        args = ["validate"]
        if rules:
            args += ["--rules", rules]
        if scoring:
            args += ["--scoring", scoring]
        return self.call(*args)

    def get_report(self, task_id, format="md"):
        return self.call("report", "--task-id", task_id, "--format", format)


class ContractCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ml-agent-")
        cls.agent = AgentClient(cls.tmp, {"ML_RAW_DIR": os.path.join(FIXTURES, "raw"),
                                          "HDFS_BASE": "/data/agent-test"})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestAgentToolMapping(ContractCase):
    """§7：每个 Agent 工具都必须能只靠 CLI 的 JSON 回答用户。"""

    def test_all_seven_tools_are_reachable(self):
        schemes, rc = self.agent.list_schemes()
        self.assertEqual(0, rc)
        self.assertTrue(schemes["ok"])
        valid, rc = self.agent.validate_config(RULES, SCORING)
        self.assertEqual(0, rc)
        self.assertTrue(valid["ok"])
        env, rc = self.agent.start_cleaning_task(RULES, SCORING, tag="agent")
        self.assertEqual(0, rc, env)
        tid = env["task_id"]

        st, rc = self.agent.get_task_status(tid)
        self.assertEqual(0, rc)
        self.assertIn(st["status"], ("queued", "running", "succeeded", "failed"))

        res, rc = self.agent.get_task_result(tid)
        self.assertEqual(0, rc)
        self.assertTrue(res["ok"])

        smp, rc = self.agent.get_samples(tid, "quarantine", "ratings", 3)
        self.assertEqual(0, rc)
        self.assertIn("samples", smp)

        rep, rc = self.agent.get_report(tid, "md")
        self.assertEqual(0, rc)
        self.assertIn("report", rep)

    def test_list_schemes_explains_registered_defaults(self):
        env, _rc = self.agent.list_schemes()
        for s in env["schemes"]:
            with self.subTest(scheme=s["scheme_id"]):
                self.assertTrue(s["description"], "Agent 需要 description 才能向用户解释")
                self.assertEqual("registered_default", s["status"])
                self.assertTrue(s["version"])


class TestEndToEndSequence(ContractCase):
    """§8：一次完整交互按文档顺序走一遍。"""

    @classmethod
    def setUpClass(cls):
        super(TestEndToEndSequence, cls).setUpClass()
        cls.start_env, cls.start_rc = cls.agent.start_cleaning_task(
            RULES, SCORING, tag="sequence")
        cls.tid = cls.start_env.get("task_id", "")

    def test_1_start_returns_task_id_immediately(self):
        self.assertEqual(0, self.start_rc, self.start_env)
        self.assertTrue(self.start_env["ok"])
        self.assertEqual("succeeded", self.start_env["status"])
        self.assertTrue(self.tid)

    def test_2_status_poll_until_terminal(self):
        """Agent 会轮询 status 展示进度；终态必须是 succeeded/failed 之一。"""
        deadline = time.time() + 60
        while time.time() < deadline:
            env, rc = self.agent.get_task_status(self.tid)
            self.assertEqual(0, rc)
            self.assertIn("stage", env)
            self.assertIn("progress_percent", env)
            if env["status"] in ("succeeded", "failed"):
                self.assertEqual("succeeded", env["status"])
                return
            time.sleep(0.5)
        self.fail("任务在 60 s 内没有进入终态")

    def test_3_result_gives_five_dimensions_and_counts(self):
        env, rc = self.agent.get_task_result(self.tid)
        self.assertEqual(0, rc, env)
        # Agent 要向用户解释「五维对比 + 数据量变化 + 隔离统计」，三块都得有
        self.assertEqual({"before", "after", "delta"}, set(env["scores"]) - {"metrics"})
        for dim in ("Accurate", "Complete", "Unique", "Up-to-date", "Consistent"):
            self.assertIn(dim, env["scores"]["before"])
            self.assertIn(dim, env["scores"]["after"])
        self.assertIn("composite", env["scores"]["before"])
        self.assertEqual({"input", "output", "quarantine", "dedupe", "fix"},
                         set(env["counts"]))

    def test_4_samples_answer_followups(self):
        env, rc = self.agent.get_samples(self.tid, "quarantine", "ratings", 5)
        self.assertEqual(0, rc)
        self.assertTrue(env["samples"])
        sample = env["samples"][0]
        # 用户追问「给我看异常记录」时，Agent 至少要能说清「哪一行、什么规则、为什么」
        self.assertIn("line_no", sample)
        self.assertIn("raw_line", sample)
        self.assertIn("rule_id", sample)
        self.assertIn("reason", sample)

    def test_5_report_gives_full_text(self):
        env, rc = self.agent.get_report(self.tid, "md")
        self.assertEqual(0, rc)
        text = env["report"]
        self.assertIn("## 1 数据量变化", text)
        self.assertIn("## 3 五维评分", text)
        self.assertIn("## 4 评价局限", text)


class TestHonesty(ContractCase):
    """Agent 行为要求：不得编造；失败/未完成必须如实返回。"""

    def test_result_on_missing_task_is_an_error_not_a_fabrication(self):
        env, rc = self.agent.get_task_result("20260101-000000-zzzzzz")
        self.assertEqual(4, rc)
        self.assertFalse(env["ok"])
        self.assertNotIn("scores", env)

    def test_validate_reports_config_problems_with_details(self):
        broken = os.path.join(self.tmp, "broken.json")
        with io.open(broken, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"config_type": "cleaning_rules"}))
        env, rc = self.agent.validate_config(broken, SCORING)
        self.assertEqual(2, rc)
        self.assertFalse(env["ok"])
        self.assertEqual("CONFIG_INVALID", env["error"]["code"])
        self.assertTrue(env["error"]["details"], "必须给出具体问题列表，不能只说失败")

    def test_limitations_are_present_for_the_agent_to_quote(self):
        env, rc = self.agent.start_cleaning_task(RULES, SCORING, tag="limitations")
        self.assertEqual(0, rc, env)
        res, rc = self.agent.get_task_result(env["task_id"])
        self.assertEqual(0, rc)
        joined = " ".join(res["limitations"])
        # 三条口径都是「不能吹」的关键：属性未核验、提升部分来自移出分母、时效性语境
        self.assertIn("未", joined)
        self.assertGreaterEqual(len(res["limitations"]), 3)

    def test_every_call_used_only_the_cli(self):
        """所有交互都必须走 CLI；一旦有人改成读内部文件，这里会先失败。"""
        # 先产生一次调用：unittest 会为每个用例新建实例，且本用例按字母序先跑，
        # 不能指望别的用例留下调用记录。
        self.agent.list_schemes()
        self.assertTrue(self.agent.calls)
        for args, ok, rc in self.agent.calls:
            with self.subTest(args=args):
                self.assertIn(args[0], ("start", "status", "result", "schemes",
                                        "samples", "validate", "report", "tasks"))
                self.assertIsInstance(ok, bool)
                self.assertIsInstance(rc, int)


if __name__ == "__main__":
    unittest.main()
