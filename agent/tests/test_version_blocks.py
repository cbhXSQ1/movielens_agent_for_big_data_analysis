# -*- coding: utf-8 -*-
"""结果板块：版本与时间边界（接口文档 §9.2）——有则透传、无则不给（绝不编造）。

覆盖两条结果构造路：
  * 长任务结果构造（`long_task.result_payload`）：终态时把远端信封里的
    `versions` / `version_hashes` / `versions_source` / `time_boundaries` 原样放进板块；
  * 离线直调结果构造（`offline.result_payload`）：走同一映射，同样透传。

铁律：字段缺席时**不造空壳**（既不给 null，也不给空 dict）。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import long_task, offline  # noqa: E402


VERSIONS = {"data": "ml1m-clean-v1", "rule": "rules-v2",
            "scoring": "score-v3", "policy": "policy-v1"}
HASHES = {"rule": "a" * 64, "scoring": "b" * 64, "policy": "c" * 64}
BOUNDARIES = {"T1": "2001-12-31T23:59:59Z", "T2": "2002-06-30T23:59:59Z"}


def _record(status="succeeded"):
    return {"agent_task_id": "LT-0001", "hadoop_task_id": "H-1",
            "name": "数据清洗", "summary": "数据清洗", "status": status,
            "stage": "成功", "progress_percent": 100,
            "estimated_seconds": 30, "updated_at": 1.0}


class TestLongTaskVersionBlocks(unittest.TestCase):
    def test_versions_and_boundaries_passed_through(self):
        """信封带版本与时间边界 → 板块原样透传（含出处）。"""
        result = {"versions": VERSIONS, "version_hashes": HASHES,
                  "versions_source": "metadata", "time_boundaries": BOUNDARIES}
        blocks = long_task.result_payload(_record(), result)["blocks"]
        self.assertEqual(VERSIONS, blocks["versions"])
        self.assertEqual(BOUNDARIES, blocks["time_boundaries"])
        self.assertEqual("metadata", blocks["versions_source"])

    def test_absent_versions_omitted_not_fabricated(self):
        """信封没有这些字段 → 板块里就不出现，不拿 null / 空字典顶替。"""
        blocks = long_task.result_payload(
            _record(), {"counts": {"input_rows": 1}})["blocks"]
        self.assertNotIn("versions", blocks)
        self.assertNotIn("time_boundaries", blocks)
        self.assertNotIn("versions_source", blocks)

    def test_existing_block_keys_unchanged(self):
        """加性改动：既有板块键一个不少。"""
        result = {"scores": {"after": {"Accurate": 0.9}},
                  "counts": {"input_rows": 1}, "versions": VERSIONS,
                  "time_boundaries": BOUNDARIES}
        blocks = long_task.result_payload(_record(), result)["blocks"]
        for key in ("task_status", "score_comparison", "data_volume"):
            self.assertIn(key, blocks)


class TestOfflineVersionBlocks(unittest.TestCase):
    def test_offline_payload_passes_versions_through(self):
        """离线直调信封同样透传版本与时间边界（走同一映射）。"""
        envelope = {"ok": True, "task_id": "H-1", "status": "succeeded",
                    "versions": VERSIONS, "version_hashes": HASHES,
                    "versions_source": "result", "time_boundaries": BOUNDARIES}
        blocks = offline.result_payload(
            "c1", "get_task_result", {"task_id": "H-1"}, envelope)["blocks"]
        self.assertEqual(VERSIONS, blocks["versions"])
        self.assertEqual(BOUNDARIES, blocks["time_boundaries"])
        self.assertEqual("result", blocks["versions_source"])

    def test_offline_absent_versions_omitted(self):
        """离线直调信封没有这些字段 → 板块里不出现。"""
        envelope = {"ok": True, "task_id": "H-1", "status": "succeeded"}
        blocks = offline.result_payload(
            "c1", "get_task_result", {"task_id": "H-1"}, envelope)["blocks"]
        self.assertNotIn("versions", blocks)
        self.assertNotIn("time_boundaries", blocks)
        self.assertNotIn("versions_source", blocks)


if __name__ == "__main__":
    unittest.main()
