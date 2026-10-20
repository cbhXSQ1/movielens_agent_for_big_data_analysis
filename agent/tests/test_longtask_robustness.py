# -*- coding: utf-8 -*-
"""长任务健壮性回归（2026-10-20 前端联调实测问题的后端侧修复）。

覆盖三项真实踩到的坑：
  1. ML 工具（训练/聚类/降维）异步受理回传的是 `model_id`（数据契约：
     模型编号 = 任务编号），早期只认 `task_id` → 登记出的长任务没有执行侧
     编号 → 探针永远查不到 → 任务卡在「运行中 0%」，还占着执行位让新任务
     发不出去（TASK_ALREADY_RUNNING）。现在 `new_record` 两个键都认。
  2. 失联任务无人收口：探针持续查无回执（None）且超过失联时长 → 按「已中止」
     终态化并注明原因（不冒充失败）；未超时不误杀。
  3. 用户手动中止：`abort()` 幂等收口 + HTTP 层尽力通知执行侧 `cancel_task`。

另覆盖对话首轮自动命名（`auto_title` + `update_conversation(title=…)`）。
"""
import os
import sys
import tempfile
import time
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime.long_task import (ABORTED, RUNNING, STALE_AFTER_SECONDS,  # noqa: E402
                                     LongTaskManager, new_record, task_view)
from agent.runtime.store import ConversationStore, auto_title  # noqa: E402
from agent.tests.stage3_helpers import (FakeToolBackend, make_provider,  # noqa: E402
                                        text_response, tool_response)
from agent.tests.test_stage4_longtask import LongTaskTestBase  # noqa: E402


class _NeverProbe(object):
    """执行侧永远查无回执（探针返回 None）。"""

    def probe(self, record):
        return None


class MlModelIdRegistrationTest(LongTaskTestBase):
    """ML 工具回传 model_id 时必须入账为 hadoop_task_id。"""

    def test_model_id_becomes_hadoop_task_id(self):
        backend = FakeToolBackend({"cluster_users": {
            "ok": True, "model_id": "ml-20261020-194352-ab12", "status": "queued"}})
        provider = make_provider([
            tool_response("cluster_users", {"algorithm": "kmeans"}),
            text_response("聚类任务已提交。"),
        ])
        result = self.rt.run_turn(self.cid, "对用户做聚类", provider=provider,
                                  tool_backend=backend, facts={})
        self.assertTrue(result["ok"], result)
        record = self.rt.long_tasks.tasks_of(self.cid)[-1]
        self.assertEqual("ml-20261020-194352-ab12", record["hadoop_task_id"])
        self.assertEqual(RUNNING, record["status"])

    def test_task_id_still_wins_for_cleaning_tools(self):
        record = new_record("LT-0001", "c0001", "start_cleaning_task", {},
                            {"task_id": "20261020-120000-abcd"})
        self.assertEqual("20261020-120000-abcd", record["hadoop_task_id"])


class StaleReapTest(LongTaskTestBase):
    """失联收口：超时按中止、注明原因；未超时不误杀。"""

    def _stale_record(self):
        record = new_record("LT-0002", self.cid, "cluster_users",
                            {"algorithm": "kmeans"}, {"model_id": "ml-x"})
        # 把更新时间拨到失联时长之前
        record["updated_at"] = time.time() - STALE_AFTER_SECONDS - 5
        return record

    def test_reap_aborts_stale_task_with_reason(self):
        record = self._stale_record()
        self.rt.long_tasks._save(self.cid, record)
        out = self.rt.long_tasks.poll(_NeverProbe())
        self.assertEqual([], out)                      # 中止不走完成/续写路径
        view = task_view(self.rt.long_tasks.get(self.cid, "LT-0002"))
        self.assertEqual(ABORTED, view["status"])
        self.assertIn("无回执", view["note"])

    def test_no_hadoop_id_reason_mentions_missing_id(self):
        record = new_record("LT-0003", self.cid, "cluster_users",
                            {"algorithm": "kmeans"}, {})
        record["updated_at"] = time.time() - STALE_AFTER_SECONDS - 5
        self.rt.long_tasks._save(self.cid, record)
        self.rt.long_tasks.poll(_NeverProbe())
        view = task_view(self.rt.long_tasks.get(self.cid, "LT-0003"))
        self.assertEqual(ABORTED, view["status"])
        self.assertIn("未回传任务编号", view["note"])

    def test_fresh_task_is_not_reaped(self):
        record = self._stale_record()
        record["updated_at"] = time.time()             # 刚更新过：不误杀
        self.rt.long_tasks._save(self.cid, record)
        self.rt.long_tasks.poll(_NeverProbe())
        view = task_view(self.rt.long_tasks.get(self.cid, "LT-0002"))
        self.assertEqual(RUNNING, view["status"])


class UserAbortTest(LongTaskTestBase):
    """用户手动中止：幂等 + 事件推送。"""

    def test_abort_finalizes_and_pushes(self):
        backend = FakeToolBackend({"start_cleaning_task": {
            "ok": True, "task_id": "2026-jd01", "status": "queued"}})
        provider = make_provider([
            tool_response("start_cleaning_task", {}),
            text_response("任务已提交。"),
        ])
        self.rt.run_turn(self.cid, "清洗数据", provider=provider,
                         tool_backend=backend, facts={})
        record = self.rt.long_tasks.tasks_of(self.cid)[-1]
        aid = record["agent_task_id"]

        sub = self.rt.open_stream(self.cid)
        view = self.rt.long_tasks.abort(self.cid, aid)
        self.assertEqual(ABORTED, view["status"])
        self.assertEqual("用户手动中止", view["note"])
        events = [e for e in sub.drain() if e.get("event") == "task_status"]
        self.assertTrue(events)

        # 幂等：再中止一次不报错、不改变状态
        again = self.rt.long_tasks.abort(self.cid, aid)
        self.assertEqual(ABORTED, again["status"])

    def test_abort_unknown_task_returns_none(self):
        self.assertIsNone(self.rt.long_tasks.abort(self.cid, "LT-9999"))


class AutoTitleTest(LongTaskTestBase):
    """对话首轮自动命名：标题空 → 用首条消息起名；起过名的不碰。"""

    def test_auto_title_helper(self):
        self.assertEqual("清洗 ml-1m 并评估五维质量", auto_title("清洗 ml-1m 并评估五维质量"))
        # 换行/连续空白折叠成单空格（标题是一行，不按行截断）
        self.assertEqual("帮我看看这份数据到底 第二行不计",
                         auto_title("帮我看看这份数据到底\n第二行不计"))
        long_text = "很" * 40
        titled = auto_title(long_text)
        self.assertTrue(titled.endswith("…"))
        self.assertEqual(len(titled), 25)               # 24 + 省略号
        self.assertEqual("", auto_title("   \n  "))

    def test_first_message_titles_untitled_conversation(self):
        provider = make_provider([text_response("好的。")])
        result = self.rt.run_turn(self.cid, "随机抽几条被隔离的评分记录",
                                  provider=provider, tool_backend=FakeToolBackend({}),
                                  facts={})
        self.assertTrue(result["ok"], result)
        conv = self.rt.store.get_conversation(self.cid)
        # setUp 建的对话带标题 → 不被打扰（这条其实校验「起过名的不碰」）
        self.assertEqual("长任务测试", conv["title"])

    def test_untitled_conversation_gets_titled(self):
        cid = self.rt.create_conversation()             # 无标题（前端新建对话就是这样）
        provider = make_provider([text_response("好的。")])
        self.rt.run_turn(cid, "为什么一致性只涨了 10 分", provider=provider,
                         tool_backend=FakeToolBackend({}), facts={})
        conv = self.rt.store.get_conversation(cid)
        self.assertEqual("为什么一致性只涨了 10 分", conv["title"])

    def test_offline_first_call_titles_conversation(self):
        cid = self.rt.create_conversation()
        backend = FakeToolBackend({"list_schemes": {
            "ok": True, "schemes": [], "total": 0, "total_available": 0}})
        result = self.rt.run_offline(cid, "list_schemes", {}, tool_backend=backend)
        self.assertTrue(result["ok"], result)
        conv = self.rt.store.get_conversation(cid)
        self.assertEqual("手动调用 list_schemes", conv["title"])


class UpdateConversationTitleTest(unittest.TestCase):
    """store.update_conversation 的 title 参数：非空才写、不掀翻 updated_at。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="store_title_")
        self.store = ConversationStore(os.path.join(self.tmp, "rt.sqlite"))
        self.addCleanup(self.store.close)
        self.cid = self.store.create_conversation(title="")

    def test_empty_title_is_written_and_empty_is_ignored(self):
        self.store.update_conversation(self.cid, title="第一轮问题")
        self.assertEqual("第一轮问题", self.store.get_conversation(self.cid)["title"])
        before = self.store.get_conversation(self.cid)["updated_at"]
        self.store.update_conversation(self.cid, title="")   # 空串：不碰
        self.assertEqual("第一轮问题", self.store.get_conversation(self.cid)["title"])
        self.assertEqual(before, self.store.get_conversation(self.cid)["updated_at"])

    def test_writing_title_touches_updated_at(self):
        before = self.store.get_conversation(self.cid)["updated_at"]
        time.sleep(0.01)
        self.store.update_conversation(self.cid, title=" renamed ")
        after = self.store.get_conversation(self.cid)
        self.assertEqual(" renamed ", after["title"])
        self.assertGreater(after["updated_at"], before)


if __name__ == "__main__":
    unittest.main()
