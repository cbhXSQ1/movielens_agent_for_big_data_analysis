# -*- coding: utf-8 -*-
"""回合产物引用（artifacts，接口文档 §3.12）测试。

覆盖：
  * 分类 / 标题 / kind / id 稳定性（纯函数）；
  * 一次离线直调长任务后：回合信封 `artifacts` 含任务文件 + 方案「依据」；
  * `artifact_added` **只对新增**产物发（第二轮不重复刷屏；新文件只补发一条）；
  * 持久化：产物列表写进对话上下文，恢复端点返回一致列表；
  * LLM `run_turn` / 异步 `run_turn_async` / 长任务续写 `run_continuation` 三条路径也会收集；
  * 任务目录 / 方案文件不存在就跳过，不抛错。

全程不触网、不触模型（离线用假工具后端；其余用脚本化假 provider）。
"""
import asyncio
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent import http_api  # noqa: E402
from agent.runtime import (ARTIFACT_ADDED, MESSAGE_DONE, RESULT_PAYLOAD,  # noqa: E402
                           Runtime, artifacts_ref)
from agent.tests.stage3_helpers import (FakeToolBackend, make_provider,  # noqa: E402
                                        text_response, tool_response)
from agent.tests.stage3b_helpers import event_types  # noqa: E402

TASK_ID = "20260101-000000-aaaaaa"
HADOOP_ID = "2026-jd42"
VERSION = "ml1m-clean-v1"


class FakeRemoteClient(object):
    """假远端客户端：按工具名回罐头信封，并记下每次调用的参数（不连网）。"""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, dict(args or {})))
        handler = self.responses.get(name)
        if handler is None:
            return {"ok": False, "error": {
                "code": "MCP_UNAVAILABLE",
                "message": "本测试没有为 %s 准备罐头信封" % name}}
        return handler(args or {}) if callable(handler) else handler

    def calls_for(self, name):
        return [args for called, args in self.calls if called == name]


def remote_result_envelope():
    """一份远端 `get_task_result` 的成功信封：计数 + 五维分 + 产物清单。"""
    return {
        "ok": True, "task_id": HADOOP_ID, "status": "succeeded",
        "counts": {"input_rows": 1000000, "output_rows": 950000,
                   "quarantined": 50000},
        "scores": {"after": {"Accurate": 0.9, "Complete": 0.8, "Unique": 0.95,
                             "Up-to-date": 0.9, "Consistent": 0.85,
                             "composite": 0.88}},
        "note": "清洗完成",
        "artifacts": [
            {"id": "task:%s:report.md" % HADOOP_ID, "title": "评估报告",
             "category": "报告", "kind": "md", "path": "report.md", "size": 10},
            {"id": "task:%s:metrics/before.json" % HADOOP_ID,
             "title": "清洗前评分", "category": "评分", "kind": "json",
             "path": "metrics/before.json", "size": 2},
            {"id": "task:%s:counts.json" % HADOOP_ID, "title": "数据量统计",
             "category": "证据", "kind": "json", "path": "counts.json", "size": 2},
            {"id": "task:%s:driver.log" % HADOOP_ID, "title": "driver.log",
             "category": "日志", "kind": "text", "path": "driver.log", "size": 5},
        ],
    }


def _sample_files():
    """一套覆盖各类别的任务产物（相对任务目录）。"""
    return {
        "result.json": b"{}",
        "metadata.json": b"{}",
        "stats.json": b"{}",
        "status.json": b"{}",
        "counts.json": b"{}",
        "report.md": b"# report\n",
        "report.json": b"{}",
        "metrics/before.json": b"{}",
        "metrics/after.json": b"{}",
        "metrics/composite.json": b"{}",
        "cleaned/%s/ratings.dat" % VERSION: b"1\r\n",
        "cleaned/%s/users.dat" % VERSION: b"2\r\n",
        "quarantine/%s/ratings.dat" % VERSION: b"3\r\n",
        "quarantine/%s/quarantine_summary.json" % VERSION: b"{}",
    }


class ArtifactTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="artifacts_ref_")
        self.var = os.path.join(self.tmp, "var")
        self.tasks = os.path.join(self.var, "tasks")
        os.makedirs(self.tasks, exist_ok=True)
        self._set_env("ML_VAR_DIR", self.var)
        self.db = os.path.join(self.tmp, "rt.sqlite")
        self.art = os.path.join(self.tmp, "art")
        self.provider_calls = {"n": 0}
        self.rt = Runtime(db_path=self.db, artifact_dir=self.art,
                          provider_factory=self._provider_factory(),
                          mcp_client=self._mcp_client())
        self.addCleanup(self._safe_close, self.rt)
        self.cid = self.rt.create_conversation(title="产物引用")

    def _mcp_client(self):
        """默认：本机未接远端（产物从本机任务目录收集，行为与既有一致）。"""
        return None

    def _provider_factory(self):
        """默认：离线测试绝不该构造模型（被触达即断言失败）。

        子类可覆盖（例如模拟「无模型」：工厂抛 `ModelError(NOT_CONFIGURED)`）。
        """
        def forbidden_factory():
            self.provider_calls["n"] += 1
            raise AssertionError("本测试不该触达模型层")
        return forbidden_factory

    def _set_env(self, key, value):
        old = os.environ.get(key)

        def restore():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        os.environ[key] = value
        self.addCleanup(restore)

    def _safe_close(self, rt):
        try:
            rt.close()
        except Exception:
            pass

    def _write_task(self, tid, files):
        base = os.path.join(self.tasks, tid)
        for rel, data in files.items():
            path = os.path.join(base, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
        return base

    def _start_long_task(self, tid=TASK_ID):
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": tid, "status": "queued"}})
        result = self.rt.run_offline(self.cid, "start_cleaning_task",
                                     {"scope": "full"}, tool_backend=backend,
                                     facts={})
        self.assertTrue(result["ok"], result)
        return result

    def _added(self, sub):
        return [e["data"]["artifact"] for e in sub.drain()
                if e["event"] == ARTIFACT_ADDED]


class TestCategoriesAndTitles(unittest.TestCase):
    def test_category_of(self):
        self.assertEqual("评分", artifacts_ref.category_of("metrics/before.json"))
        self.assertEqual("报告", artifacts_ref.category_of("report.md"))
        self.assertEqual("报告", artifacts_ref.category_of("report.json"))
        self.assertEqual("证据", artifacts_ref.category_of("counts.json"))
        self.assertEqual("证据",
                         artifacts_ref.category_of(
                             "quarantine/ml1m-clean-v1/ratings.dat"))
        self.assertEqual("文件",
                         artifacts_ref.category_of(
                             "cleaned/ml1m-clean-v1/ratings.dat"))
        self.assertEqual("其它", artifacts_ref.category_of("result.json"))
        self.assertEqual("其它", artifacts_ref.category_of("status.json"))

    def test_titles(self):
        self.assertEqual("评估报告", artifacts_ref.friendly_title("report.md"))
        self.assertEqual("清洗前评分",
                         artifacts_ref.friendly_title("metrics/before.json"))
        self.assertEqual("数据量统计",
                         artifacts_ref.friendly_title("counts.json"))
        self.assertEqual(
            "清洗后 ratings（ml1m-clean-v1）",
            artifacts_ref.friendly_title("cleaned/ml1m-clean-v1/ratings.dat"))
        # 不认识的回落成相对路径（不臆造）
        self.assertEqual("weird/thing.bin",
                         artifacts_ref.friendly_title("weird/thing.bin"))

    def test_merge_dedupes_by_id(self):
        a = artifacts_ref.build_task_artifact(TASK_ID, "report.md")
        b = artifacts_ref.build_scheme_artifact("cleaning_rules.v1.json")
        full, added = artifacts_ref.merge([a], [a, b])
        self.assertEqual([x["id"] for x in full], [a["id"], b["id"]])
        self.assertEqual([x["id"] for x in added], [b["id"]])
        # 再合并一次同样的 → 无新增
        full2, added2 = artifacts_ref.merge([a, b], [a, b])
        self.assertEqual([x["id"] for x in full2], [a["id"], b["id"]])
        self.assertEqual(added2, [])

    def test_task_ids_of(self):
        ctx = {"task_id": "T1", "task_ids": ["T0", "T1"]}
        long_tasks = {"LT-0001": {"hadoop_task_id": "T2"}}
        self.assertEqual(["T3", "T0", "T1", "T2"],
                         artifacts_ref.task_ids_of(ctx, long_tasks, extra=["T3"]))

    def test_scheme_names_of(self):
        names = artifacts_ref.scheme_names_of(
            {"rules": "config/custom.json"}, {},
            [{"scoring": "config/s.json"}], has_tasks=True)
        self.assertEqual(["custom.json", "s.json"], names)
        # 发起过任务但没指定方案 → 用默认方案补齐（依据）
        defaults = artifacts_ref.scheme_names_of({}, {}, None, has_tasks=True)
        self.assertIn("cleaning_rules.v1.json", defaults)
        self.assertIn("scoring_scheme.v1.json", defaults)
        # 没有任务、也没给方案 → 空
        self.assertEqual([], artifacts_ref.scheme_names_of({}, None, None))


class TestCollectTaskArtifacts(unittest.TestCase):
    def test_missing_task_is_skipped(self):
        self.assertEqual([], artifacts_ref.collect_task_artifacts("no-such-task"))
        self.assertEqual([], artifacts_ref.collect_task_artifacts(""))


class TestInternalFileFilter(unittest.TestCase):
    """内部文件（驱动/引擎自用）不进用户产物列表：过滤规则与保留项。"""

    def test_is_internal_file(self):
        for rel in ("driver.log", "metadata.json", "published_hashes.json",
                    "logs/upload_raw.log", "logs/publish.log",
                    "logs/quarantine_ratings.tmp", "logs/nested/deep.log",
                    "logs\\driver.log", ".\\metadata.json"):
            self.assertTrue(artifacts_ref.is_internal_file(rel), rel)
        # 用户真正关心的产物一律保留（含 status.json）
        for rel in ("status.json", "result.json", "counts.json", "stats.json",
                    "report.md", "report.json", "metrics/before.json",
                    "cleaned/ml1m-clean-v1/ratings.dat",
                    "quarantine/ml1m-clean-v1/ratings.dat",
                    "quarantine/ml1m-clean-v1/quarantine_summary.json"):
            self.assertFalse(artifacts_ref.is_internal_file(rel), rel)

    def test_collect_filters_internal_but_keeps_others(self):
        listing = {"task_id": "T", "files": [
            {"path": "result.json", "kind": "json"},
            {"path": "status.json", "kind": "json"},
            {"path": "report.md", "kind": "md"},
            {"path": "counts.json", "kind": "json"},
            {"path": "stats.json", "kind": "json"},
            {"path": "metadata.json", "kind": "json"},
            {"path": "driver.log", "kind": "text"},
            {"path": "published_hashes.json", "kind": "json"},
            {"path": "logs/upload_raw.log", "kind": "text"},
            {"path": "logs/publish.log", "kind": "text"},
            {"path": "logs/driver.log", "kind": "text"},
            {"path": "cleaned/ml1m-clean-v1/ratings.dat", "kind": "dat"},
        ]}
        arts = artifacts_ref.collect_task_artifacts(
            "T", lister=lambda tid: listing)
        paths = {a["path"] for a in arts}
        self.assertEqual(paths, {
            "result.json", "status.json", "report.md", "counts.json",
            "stats.json", "cleaned/ml1m-clean-v1/ratings.dat"})
        # 分类 / 标题 / id 逻辑不变
        by_path = {a["path"]: a for a in arts}
        self.assertEqual("task:T:report.md", by_path["report.md"]["id"])
        self.assertEqual("报告", by_path["report.md"]["category"])
        self.assertEqual("证据", by_path["counts.json"]["category"])
        self.assertEqual("文件",
                         by_path["cleaned/ml1m-clean-v1/ratings.dat"]["category"])



class TestInternalStrippingAndTitles(unittest.TestCase):
    """恢复 / 收集时的防御性再过滤与同名去歧义（纯函数）。"""

    def test_strip_internal_drops_old_internal_entries(self):
        items = [
            artifacts_ref.build_task_artifact(TASK_ID, "driver.log"),
            artifacts_ref.build_task_artifact(TASK_ID, "logs/publish.log"),
            artifacts_ref.build_task_artifact(TASK_ID, "metadata.json"),
            artifacts_ref.build_task_artifact(TASK_ID, "published_hashes.json"),
            artifacts_ref.build_task_artifact(TASK_ID, "status.json"),
            artifacts_ref.build_scheme_artifact("cleaning_rules.v1.json"),
        ]
        kept = {a["id"] for a in artifacts_ref.strip_internal(items)}
        self.assertEqual({
            "task:%s:status.json" % TASK_ID,
            "scheme:cleaning_rules.v1.json"}, kept)

    def test_disambiguate_titles_same_name_across_tasks(self):
        a = artifacts_ref.build_task_artifact("20260101-000000-aaaaaa",
                                              "status.json")
        b = artifacts_ref.build_task_artifact("20260102-000000-bbbbbb",
                                              "status.json")
        out = artifacts_ref.disambiguate_titles([a, b])
        titles = [x["title"] for x in out]
        self.assertEqual(2, len(set(titles)))           # 两个标签不一样
        self.assertIn("aaaaaa", titles[0])
        self.assertIn("bbbbbb", titles[1])
        # 单一任务不补后缀（保持原友好标题）
        solo = artifacts_ref.disambiguate_titles([a])
        self.assertEqual("任务状态", solo[0]["title"])
        # 不改动入参
        self.assertEqual("任务状态", a["title"])

    def test_read_task_result_missing_returns_none(self):
        self.assertIsNone(artifacts_ref.read_task_result(""))
        self.assertIsNone(artifacts_ref.read_task_result("no-such-task"))


class TestOfflineCollection(ArtifactTestBase):
    def test_long_task_turn_collects_task_files_and_schemes(self):
        self._write_task(TASK_ID, _sample_files())
        sub = self.rt.open_stream(self.cid)
        result = self._start_long_task()

        arts = result["artifacts"]
        self.assertTrue(arts)
        by_path = {a["path"]: a for a in arts if a["source"] == "task_file"}
        self.assertEqual(by_path["report.md"]["category"], "报告")
        self.assertEqual(by_path["report.md"]["kind"], "md")
        self.assertEqual(by_path["metrics/before.json"]["category"], "评分")
        self.assertEqual(by_path["counts.json"]["category"], "证据")
        self.assertEqual(
            by_path["quarantine/%s/ratings.dat" % VERSION]["category"], "证据")
        self.assertEqual(
            by_path["cleaned/%s/ratings.dat" % VERSION]["category"], "文件")
        self.assertEqual(by_path["cleaned/%s/ratings.dat" % VERSION]["kind"],
                         "dat")
        self.assertEqual(by_path["result.json"]["category"], "其它")
        # 稳定 id
        self.assertEqual(by_path["report.md"]["id"],
                         "task:%s:report.md" % TASK_ID)
        self.assertIsNone(by_path["report.md"]["scheme_name"])
        self.assertEqual(by_path["report.md"]["task_id"], TASK_ID)

        # 方案「依据」：默认清洗规则 + 评分方案
        schemes = {a["scheme_name"] for a in arts if a["source"] == "scheme"}
        self.assertIn("cleaning_rules.v1.json", schemes)
        self.assertIn("scoring_scheme.v1.json", schemes)
        for a in arts:
            if a["source"] == "scheme":
                self.assertEqual(a["category"], "依据")
                self.assertEqual(a["kind"], "scheme")
                self.assertIsNone(a["task_id"])
                self.assertIsNone(a["path"])

        # artifact_added 只发新片段：每条恰好一份
        added = self._added(sub)
        self.assertEqual({a["id"] for a in added}, {a["id"] for a in arts})
        self.assertEqual(len(added), len(arts))

        # 持久化进对话上下文
        ctx = self.rt.get_conversation(self.cid)["context"]
        self.assertEqual([a["id"] for a in ctx["artifacts"]],
                         [a["id"] for a in arts])
        self.assertIn(TASK_ID, ctx["task_ids"])

    def test_internal_files_not_exposed_as_artifacts(self):
        """真机场景：driver.log / logs/ / metadata.json / published_hashes.json
        不进产物标签；status.json 与真实产物保留。"""
        self._write_task(TASK_ID, {
            "result.json": b"{}",
            "status.json": b"{}",
            "report.md": b"# r\n",
            "metadata.json": b"{}",
            "published_hashes.json": b"{}",
            "driver.log": b"log line\n",
            "logs/upload_raw.log": b"up\n",
            "logs/publish.log": b"pub\n",
        })
        sub = self.rt.open_stream(self.cid)
        result = self._start_long_task()

        paths = {a["path"] for a in result["artifacts"]
                 if a["source"] == "task_file"}
        for internal in ("metadata.json", "published_hashes.json", "driver.log",
                         "logs/upload_raw.log", "logs/publish.log"):
            self.assertNotIn(internal, paths)
        for kept in ("result.json", "status.json", "report.md"):
            self.assertIn(kept, paths)
        # 事件里也不该出现内部文件
        added_paths = {a["path"] for a in self._added(sub)}
        self.assertNotIn("driver.log", added_paths)
        self.assertNotIn("metadata.json", added_paths)

    def test_second_turn_only_emits_new_artifacts(self):
        self._write_task(TASK_ID, _sample_files())
        sub = self.rt.open_stream(self.cid)
        first = self._start_long_task()
        first_added = self._added(sub)
        self.assertTrue(first_added)

        # 追加一个新文件，再跑一轮普通查询（不发起任务）
        new_path = os.path.join(self.tasks, TASK_ID, "extra.json")
        with open(new_path, "wb") as fh:
            fh.write(b"{}")
        backend = FakeToolBackend({"list_tasks": {"ok": True, "tasks": []}})
        second = self.rt.run_offline(self.cid, "list_tasks", {},
                                     tool_backend=backend, facts={})
        added2 = self._added(sub)

        # 只新增被补发的那一条，其余不重复
        self.assertEqual(1, len(added2))
        self.assertEqual(added2[0]["path"], "extra.json")
        # 完整列表 = 旧 + 新
        self.assertEqual(len(second["artifacts"]), len(first["artifacts"]) + 1)
        self.assertEqual({a["id"] for a in second["artifacts"]},
                         {a["id"] for a in first["artifacts"]} | {added2[0]["id"]})

    def test_missing_task_dir_is_skipped(self):
        # 上下文里指向一个不存在的任务目录 -> 收集时跳过，不抛
        self.rt.store.update_context(self.cid, {"task_id": "no-such-task"})
        backend = FakeToolBackend({"list_tasks": {"ok": True, "tasks": []}})
        result = self.rt.run_offline(self.cid, "list_tasks", {},
                                     tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        # 任务目录不存在 -> 没有任务产物（方案「依据」仍可按上下文给出）
        self.assertEqual(
            [], [a for a in result["artifacts"] if a["source"] == "task_file"])

    def test_custom_scheme_from_args_becomes_reference(self):
        custom = "artifact_ref_custom_rules.v1.json"
        path = os.path.join(REPO_ROOT, "config", custom)
        with open(path, "wb") as fh:
            fh.write(b"{}")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        self._write_task(TASK_ID, {"result.json": b"{}"})
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": TASK_ID, "status": "queued"}})
        result = self.rt.run_offline(
            self.cid, "start_cleaning_task",
            {"scope": "full", "rules": "config/" + custom},
            tool_backend=backend, facts={})
        schemes = {a["scheme_name"] for a in result["artifacts"]
                   if a["source"] == "scheme"}
        self.assertIn(custom, schemes)
        self.assertIn("scoring_scheme.v1.json", schemes)     # 缺的一侧用默认


class TestRunTurnCollection(ArtifactTestBase):
    def test_llm_turn_collects_artifacts(self):
        self._write_task(TASK_ID, _sample_files())
        sub = self.rt.open_stream(self.cid)
        provider = make_provider([
            tool_response("start_cleaning_task", {"scope": "full"}),
            text_response("已发起清洗任务。"),
        ])
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": TASK_ID, "status": "queued"}})
        result = self.rt.run_turn(self.cid, "帮我清洗数据", provider=provider,
                                  tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        self.assertEqual("llm", result["engine"])
        paths = {a["path"] for a in result["artifacts"]
                 if a["source"] == "task_file"}
        self.assertIn("report.md", paths)
        self.assertIn("counts.json", paths)
        added = self._added(sub)
        self.assertTrue(added)
        self.assertEqual({a["id"] for a in added},
                         {a["id"] for a in result["artifacts"]})


class TestAsyncCollection(ArtifactTestBase):
    def test_async_turn_collects_artifacts(self):
        self._write_task(TASK_ID, {"result.json": b"{}", "report.md": b"#"})
        sub = self.rt.open_stream(self.cid)
        provider = make_provider([
            tool_response("start_cleaning_task", {"scope": "full"}),
            text_response("异步路径已完成。"),
        ])
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": TASK_ID, "status": "queued"}})

        async def scenario():
            return await self.rt.run_turn_async(
                self.cid, "异步清洗", provider=provider,
                tool_backend=backend, facts={})

        result = asyncio.run(scenario())
        self.assertTrue(result["ok"], result)
        self.assertIn("report.md", {a["path"] for a in result["artifacts"]})
        added = self._added(sub)
        self.assertTrue(added)
        self.assertEqual({a["id"] for a in added},
                         {a["id"] for a in result["artifacts"]})


class TestContinuationCollection(ArtifactTestBase):
    def test_continuation_recollects_new_files(self):
        self._write_task(TASK_ID, {"result.json": b"{}"})
        sub = self.rt.open_stream(self.cid)
        first = self._start_long_task()
        self._added(sub)                                   # 清掉第一轮事件
        agent_task_id = first["long_tasks"][0]["agent_task_id"]

        # 长任务完成前，又落了一个报告文件
        with open(os.path.join(self.tasks, TASK_ID, "report.md"), "wb") as fh:
            fh.write(b"# done\n")

        provider = make_provider([text_response("任务已完成，报告已生成。")])
        outcome = self.rt.long_tasks.complete(
            self.cid, agent_task_id, result={"note": "完成"},
            provider=provider, facts={})
        self.assertTrue(outcome["resumed"])

        turn = outcome["turn"]
        self.assertIn("report.md",
                      {a["path"] for a in turn["artifacts"]})
        added = self._added(sub)
        self.assertEqual(["report.md"], [a["path"] for a in added])

    def test_finalize_collects_new_artifacts_at_terminal(self):
        """到达终态即再收集一次产物（不经模型，如重启核对路径）。"""
        self._write_task(TASK_ID, {"status.json": b"{}"})
        sub = self.rt.open_stream(self.cid)
        first = self._start_long_task()
        agent_task_id = first["long_tasks"][0]["agent_task_id"]
        self._added(sub)                                    # 清掉第一轮事件

        self._write_task(TASK_ID, {"report.md": b"# r\n"})
        self.rt.long_tasks.finalize(self.cid, agent_task_id,
                                    status="succeeded", result={"note": "完成"})
        added = self._added(sub)
        self.assertEqual(["report.md"], [a["path"] for a in added])

    def test_provider_present_keeps_model_continuation(self):
        """有模型时行为不变：仍走模型续写，不走确定性降级。"""
        self._write_task(TASK_ID, {"result.json": b"{}"})
        self._start_long_task()
        agent_task_id = self.rt.long_tasks.tasks_of(self.cid)[0]["agent_task_id"]
        provider = make_provider([text_response("模型续写结果。")])
        outcome = self.rt.long_tasks.complete(
            self.cid, agent_task_id, result={"note": "完成"},
            provider=provider, facts={})
        turn = outcome["turn"]
        self.assertTrue(turn["ok"])
        self.assertFalse(turn.get("degraded"))
        self.assertEqual("llm", turn["engine"])
        self.assertEqual("模型续写结果。", turn["final"])


class TestNoModelContinuation(ArtifactTestBase):
    """无模型（离线 / 未配置）时长任务完成 → 优雅收尾，不报错。"""

    def _provider_factory(self):
        from agent.llm import ModelError, NOT_CONFIGURED

        def no_model():
            self.provider_calls["n"] += 1
            raise ModelError(NOT_CONFIGURED, "大模型未配置或配置不完整")
        return no_model

    def _write_result(self, tid, payload):
        self._write_task(tid, {"result.json":
                               json.dumps(payload, ensure_ascii=False)
                               .encode("utf-8")})

    def test_long_task_completion_without_model_degrades_gracefully(self):
        # 排队时只落了 status.json
        self._write_task(TASK_ID, {"status.json": b"{}"})
        sub = self.rt.open_stream(self.cid)
        first = self._start_long_task()
        agent_task_id = first["long_tasks"][0]["agent_task_id"]
        sub.drain()

        # 完成时才落盘的产物（真机场景：续写失败这些就收不进产物）
        self._write_task(TASK_ID, {"report.md": b"# r\n",
                                   "metrics/before.json": b"{}",
                                   "counts.json": b"{}",
                                   "driver.log": b"log\n"})
        self._write_result(TASK_ID, {
            "status": "succeeded", "data_version": VERSION,
            "counts": {"input": {"ratings_lines": 1000000},
                       "output": {"ratings": 950000},
                       "quarantine": {"total": 50000, "by_rule": {}}},
            "scores": {"after": {"Accurate": 0.9, "Complete": 0.8,
                                 "Unique": 0.95, "Up-to-date": 0.9,
                                 "Consistent": 0.85, "composite": 0.88}},
        })

        out = self.rt.long_tasks.complete(
            self.cid, agent_task_id,
            result={"counts": {"input_rows": 1000000, "output_rows": 950000,
                               "quarantined": 50000}, "note": "清洗完成"},
            facts={})
        self.assertTrue(out["resumed"], out)
        turn = out["turn"]

        # 1) 不再报错：ok=True、无 error、确定性助手消息
        self.assertTrue(turn["ok"], turn)
        self.assertTrue(turn.get("degraded"))
        self.assertIsNone(turn["error"])
        self.assertIn("后台任务已完成", turn["final"])
        self.assertIn("数据清洗与评分", turn["final"])
        self.assertIn("评分", turn["final"])
        self.assertIn("0.88", turn["final"])         # 五维真实分
        self.assertIn("50000", turn["final"])        # 真实隔离计数

        events = sub.drain()
        kinds = event_types(events)
        # 2) 助手消息、result_payload、产物照发；没有 error
        self.assertIn(MESSAGE_DONE, kinds)
        self.assertIn(RESULT_PAYLOAD, kinds)
        self.assertIn(ARTIFACT_ADDED, kinds)
        self.assertNotIn("error", kinds)

        # 3) 完成的真实产物被收起；内部文件不出现
        paths = {a["path"] for a in turn["artifacts"]
                 if a["source"] == "task_file"}
        self.assertIn("report.md", paths)
        self.assertIn("metrics/before.json", paths)
        self.assertIn("counts.json", paths)
        self.assertNotIn("driver.log", paths)
        added_paths = {e["data"]["artifact"]["path"] for e in events
                       if e["event"] == ARTIFACT_ADDED}
        self.assertIn("report.md", added_paths)
        self.assertNotIn("driver.log", added_paths)
        # 消息进 LLM 上下文（同一条对话的助手节点）
        roles = [n["role"] for n in self.rt.read_tree(self.cid)["nodes"]]
        self.assertEqual(["user", "assistant", "task", "assistant"], roles)


class TestArtifactsEndpoint(ArtifactTestBase):
    def setUp(self):
        super().setUp()
        srv = http_api.create_server("127.0.0.1", 0, runtime=self.rt)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop, srv)
        self.port = srv.server_address[1]

    def _stop(self, srv):
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_restore_returns_persisted_list(self):
        self._write_task(TASK_ID, _sample_files())
        result = self._start_long_task()

        status, payload = self._get(
            "/api/conversations/%s/artifacts" % self.cid)
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["conversation_id"], self.cid)
        self.assertEqual([a["id"] for a in payload["artifacts"]],
                         [a["id"] for a in result["artifacts"]])

    def test_restore_unknown_conversation(self):
        status, payload = self._get("/api/conversations/c9999/artifacts")
        self.assertEqual(200, status)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "CONVERSATION_NOT_FOUND")

    def test_restore_empty_when_no_artifacts(self):
        status, payload = self._get(
            "/api/conversations/%s/artifacts" % self.cid)
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["artifacts"], [])

    def test_restore_filters_internal_and_disambiguates_titles(self):
        """老对话里存下的内部文件不再出现；同名产物标题可区分。"""
        old = [
            artifacts_ref.build_task_artifact(TASK_ID, "driver.log"),
            artifacts_ref.build_task_artifact(TASK_ID, "logs/publish.log"),
            artifacts_ref.build_task_artifact(TASK_ID, "metadata.json"),
            artifacts_ref.build_task_artifact(TASK_ID, "status.json"),
            artifacts_ref.build_task_artifact("20260102-000000-bbbbbb",
                                              "status.json"),
        ]
        self.rt.store.update_context(self.cid, {"artifacts": old})

        status, payload = self._get(
            "/api/conversations/%s/artifacts" % self.cid)
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        paths = [a["path"] for a in payload["artifacts"]]
        for internal in ("driver.log", "logs/publish.log", "metadata.json"):
            self.assertNotIn(internal, paths)
        statuses = [a for a in payload["artifacts"] if a["path"] == "status.json"]
        self.assertEqual(2, len(statuses))
        self.assertNotEqual(statuses[0]["title"], statuses[1]["title"])


class TestRemoteArtifactCollection(ArtifactTestBase):
    """远端任务：产物引用从远端结果信封 / 列文件取，绝不读本机任务目录。"""

    def _mcp_client(self):
        self.mcp = FakeRemoteClient({
            "get_task_result": remote_result_envelope(),
            "list_schemes": {"ok": True, "schemes": [
                {"name": "cleaning_rules.v1.json"},
                {"name": "scoring_scheme.v1.json"}]},
        })
        return self.mcp

    def test_remote_task_artifacts_from_remote_result(self):
        sub = self.rt.open_stream(self.cid)
        result = self._start_long_task(tid=HADOOP_ID)

        by_path = {a["path"]: a for a in result["artifacts"]
                   if a["source"] == "task_file"}
        self.assertEqual({"report.md", "metrics/before.json", "counts.json",
                          "driver.log"}, set(by_path))
        self.assertEqual("日志", by_path["driver.log"]["category"])  # 远端日志照收
        for a in by_path.values():
            self.assertEqual(a["task_id"], HADOOP_ID)        # ref 带 hadoop 编号
            self.assertEqual(a["id"], "task:%s:%s" % (HADOOP_ID, a["path"]))
        schemes = {a["scheme_name"] for a in result["artifacts"]
                   if a["source"] == "scheme"}
        self.assertEqual({"cleaning_rules.v1.json", "scoring_scheme.v1.json"},
                         schemes)
        self.assertTrue(self._added(sub))
        self.assertTrue(self.mcp.calls_for("get_task_result"))

    def test_remote_falls_back_to_list_task_files(self):
        self.mcp.responses["get_task_result"] = {
            "ok": False, "error": {"code": "TASK_NOT_FINISHED",
                                   "message": "任务未完成"}}
        self.mcp.responses["list_task_files"] = {
            "ok": True, "task_id": HADOOP_ID, "files": [
                {"path": "status.json", "kind": "json"},
                {"path": "driver.log", "kind": "text"}]}
        result = self._start_long_task(tid=HADOOP_ID)
        paths = {a["path"] for a in result["artifacts"]
                 if a["source"] == "task_file"}
        self.assertEqual({"status.json", "driver.log"}, paths)
        self.assertTrue(self.mcp.calls_for("list_task_files"))


class TestRemoteCategoryAndTitlePassThrough(unittest.TestCase):
    """远端产物：保留远端的 category / title（远端词汇优先），缺失类别才按路径派生。

    正文所述不一致：远端结果信封里的 `artifacts[]` 已带 `category`（数据 / 隔离 /
    报告 / 指标 / 元数据 / 日志 / 其它），此前却被丢弃、按路径重算成 Agent 自有词汇
    （报告 / 评分 / 证据 / 文件 / 依据 / 其它），前端徽标因此大多落到默认配色。
    """

    def _collect(self, artifacts, task_id="T"):
        client = FakeRemoteClient({
            "get_task_result": {"ok": True, "task_id": task_id,
                                "artifacts": artifacts},
        })
        return artifacts_ref.collect_task_artifacts(task_id, client=client)

    def test_remote_category_and_title_are_preserved(self):
        refs = self._collect([
            {"id": "task:T:metrics/before.json", "title": "清洗前评分（远端）",
             "category": "指标", "kind": "json",
             "path": "metrics/before.json", "size": 2},
            {"id": "task:T:cleaned/v1/ratings.dat", "title": "清洗后数据",
             "category": "数据", "kind": "dat",
             "path": "cleaned/v1/ratings.dat", "size": 10},
            {"id": "task:T:jobs/etl.log", "title": "ETL 作业日志",
             "category": "日志", "kind": "text",
             "path": "jobs/etl.log", "size": 3},
        ])
        by_path = {a["path"]: a for a in refs}
        # 远端「指标」得以保留，而不是 Agent 按路径派生的「评分」
        self.assertEqual("指标", by_path["metrics/before.json"]["category"])
        self.assertEqual("清洗前评分（远端）",
                         by_path["metrics/before.json"]["title"])
        self.assertEqual("数据", by_path["cleaned/v1/ratings.dat"]["category"])
        self.assertEqual("清洗后数据", by_path["cleaned/v1/ratings.dat"]["title"])
        self.assertEqual("日志", by_path["jobs/etl.log"]["category"])
        self.assertEqual("ETL 作业日志", by_path["jobs/etl.log"]["title"])
        for a in refs:
            self.assertEqual("T", a["task_id"])

    def test_remote_entry_without_category_falls_back_to_path(self):
        refs = self._collect([
            {"id": "task:T:metrics/before.json", "title": "清洗前评分",
             "kind": "json", "path": "metrics/before.json", "size": 2},
        ])
        self.assertEqual(1, len(refs))
        # 缺失 category → 按路径派生（保持 Agent 既有词汇）
        self.assertEqual("评分", refs[0]["category"])
        # 标题仍按远端透传
        self.assertEqual("清洗前评分", refs[0]["title"])

    def test_remote_entry_without_title_falls_back_to_path(self):
        refs = self._collect([
            {"id": "task:T:metrics/before.json", "category": "指标",
             "kind": "json", "path": "metrics/before.json", "size": 2},
        ])
        self.assertEqual(1, len(refs))
        self.assertEqual("指标", refs[0]["category"])
        # 缺失 title → 按路径给友好标题
        self.assertEqual("清洗前评分", refs[0]["title"])

    def test_remote_logs_are_kept_not_filtered(self):
        """远端结果信封的 `artifacts[]` 已是产品清单（含日志），不再叠加内部文件过滤。"""
        refs = self._collect([
            {"id": "task:T:driver.log", "title": "driver.log",
             "category": "日志", "kind": "text", "path": "driver.log", "size": 5},
            {"id": "task:T:logs/publish.log", "title": "publish.log",
             "category": "日志", "kind": "text",
             "path": "logs/publish.log", "size": 5},
        ])
        by_path = {a["path"]: a for a in refs}
        self.assertEqual({"driver.log", "logs/publish.log"}, set(by_path))
        for a in refs:
            self.assertEqual("日志", a["category"])

    def test_remote_logs_entry_is_kept_with_hadoop_id(self):
        """远端 `logs/` 下的日志条目照收：类别透传「日志」，`task_id` 取 hadoop 编号。"""
        client = FakeRemoteClient({"get_task_result": {
            "ok": True, "task_id": HADOOP_ID, "artifacts": [
                {"id": "task:%s:logs/driver.log" % HADOOP_ID,
                 "title": "driver.log", "category": "日志", "kind": "text",
                 "path": "logs/driver.log", "size": 5}]}})
        refs = artifacts_ref.collect_task_artifacts(HADOOP_ID, client=client)
        self.assertEqual(1, len(refs))
        self.assertEqual("logs/driver.log", refs[0]["path"])
        self.assertEqual("日志", refs[0]["category"])
        self.assertEqual(HADOOP_ID, refs[0]["task_id"])
        self.assertEqual("task:%s:logs/driver.log" % HADOOP_ID, refs[0]["id"])

    def test_remote_title_survives_disambiguation(self):
        """同名去歧义不再用路径标题覆盖远端透传的标题。"""
        refs = self._collect([
            {"id": "task:T:cleaned/v1/ratings.dat", "title": "清洗后数据",
             "category": "数据", "kind": "dat",
             "path": "cleaned/v1/ratings.dat", "size": 10},
        ])
        out = artifacts_ref.disambiguate_titles(refs)
        self.assertEqual("清洗后数据", out[0]["title"])
        self.assertEqual("数据", out[0]["category"])

    def test_remote_title_disambiguation_is_idempotent(self):
        a = artifacts_ref.build_task_artifact(
            "20260101-000000-aaaaaa", "cleaned/v1/ratings.dat",
            title="清洗后数据")
        b = artifacts_ref.build_task_artifact(
            "20260102-000000-bbbbbb", "cleaned/v1/ratings.dat",
            title="清洗后数据")
        once = artifacts_ref.disambiguate_titles([a, b])
        twice = artifacts_ref.disambiguate_titles(once)
        self.assertEqual([x["title"] for x in once],
                         [x["title"] for x in twice])
        self.assertNotEqual(once[0]["title"], once[1]["title"])


class TestRemoteDegradedCompletion(ArtifactTestBase):
    """无模型收尾：计数与五维分取自远端结果（probe 落进长任务记录）。"""

    def _provider_factory(self):
        from agent.llm import ModelError, NOT_CONFIGURED

        def no_model():
            self.provider_calls["n"] += 1
            raise ModelError(NOT_CONFIGURED, "大模型未配置或配置不完整")
        return no_model

    def _mcp_client(self):
        self.mcp = FakeRemoteClient({
            "get_task_status": {"ok": True, "status": "succeeded", "stage": "成功",
                                "progress_percent": 100},
            "get_task_result": remote_result_envelope(),
            "list_schemes": {"ok": True, "schemes": [
                {"name": "cleaning_rules.v1.json"}]},
        })
        return self.mcp

    def test_degraded_message_includes_remote_counts_and_scores(self):
        from agent.runtime.long_task import RemoteTaskProbe

        first = self._start_long_task(tid=HADOOP_ID)
        self.assertTrue(first["ok"], first)
        probe = RemoteTaskProbe(self.mcp)
        finished = self.rt.poll_long_tasks(probe)
        self.assertTrue(finished, "远端探针应把任务推进到终态")

        turn = finished[0]["turn"]
        self.assertTrue(turn["ok"], turn)
        self.assertTrue(turn.get("degraded"))
        self.assertIn("后台任务已完成", turn["final"])
        self.assertIn("0.88", turn["final"])                 # 远端真实综合分
        self.assertIn("50000", turn["final"])                # 远端真实隔离计数
        paths = {a["path"] for a in turn["artifacts"]
                 if a["source"] == "task_file"}
        self.assertIn("report.md", paths)


class TestUnconfiguredClientDegrades(ArtifactTestBase):
    """未接远端（client None）：优雅降级、不崩、无任务产物、仍给完成说明。"""

    def _provider_factory(self):
        from agent.llm import ModelError, NOT_CONFIGURED

        def no_model():
            self.provider_calls["n"] += 1
            raise ModelError(NOT_CONFIGURED, "大模型未配置或配置不完整")
        return no_model

    def test_no_client_degrades_without_artifacts(self):
        first = self._start_long_task(tid=HADOOP_ID)
        agent_task_id = first["long_tasks"][0]["agent_task_id"]
        out = self.rt.long_tasks.complete(
            self.cid, agent_task_id,
            result={"counts": {"input_rows": 10, "output_rows": 9,
                               "quarantined": 1}, "note": "完成"},
            facts={})
        self.assertTrue(out["resumed"])
        turn = out["turn"]
        self.assertTrue(turn["ok"], turn)
        self.assertTrue(turn.get("degraded"))
        self.assertIn("后台任务已完成", turn["final"])
        self.assertEqual(
            [], [a for a in turn["artifacts"] if a["source"] == "task_file"])


if __name__ == "__main__":
    unittest.main()
