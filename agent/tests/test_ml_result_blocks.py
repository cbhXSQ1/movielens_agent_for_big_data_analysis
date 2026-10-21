# -*- coding: utf-8 -*-
"""迭代二 ML 结果板块：远端 `metrics` 按 `task_type` 落到同名板块。

契约权威：`docs/hadoop/迭代二_机器学习_数据契约.md` §3.1–§3.4。
规则（加性、present-only，绝不造空壳）：
  * 成功信封带 `task_type` ∈ {classification, clustering, dimensionality_reduction}
    且带 `metrics` 时，`blocks[task_type]` = 远端 `metrics` 原样；
  * `params` / `artifacts` / `time_boundaries` 同样按存在即透传；
  * 缺席时不建板块（既不给 null，也不给空 dict）；
  * 既有板块键（task_status / score_comparison / data_volume / quarantine_samples
    / report / limitations）一个不少。
"""
import os
import sys
import unittest

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, REPO_ROOT)

from agent.runtime import long_task, offline  # noqa: E402


CLASSIFICATION_METRICS = {
    "f1": 0.8123, "auc": 0.9012, "precision": 0.80, "recall": 0.75,
    "pr_auc": 0.78, "threshold": 0.5,
    "confusion_matrix": {"tp": 10, "fp": 2, "tn": 8, "fn": 3},
    "positive_rate": 0.34,
    "feature_importance": [{"feature": "avg_rating", "importance": 0.31}],
    "split_sizes": {"train": 100, "valid": 20, "test": 30},
    "leakage": {"policy": "time_split", "verified_on": "2001-12-31"},
}
CLUSTERING_METRICS = {
    "k": 5, "silhouette": 0.4321, "davies_bouldin": 0.98,
    "clusters": [{"cluster_id": 0, "size": 42, "label": "高活跃",
                  "top_genres": ["Action", "Sci-Fi"],
                  "behavior": {"activity": 0.9, "avg_rating": 4.1,
                               "rating_std": 0.6, "recency": 0.5}}],
}
DIM_REDUCTION_METRICS = {
    "n_components": 8,
    "explained_variance_ratio": [0.4, 0.2],
    "reconstruction_error": 0.012,
    "resource": {"fit_seconds": 3.2, "mem_mb": 128},
    "comparison": {"before": {"silhouette": 0.3, "davies_bouldin": 1.1, "k": 5},
                   "after": {"silhouette": 0.4, "davies_bouldin": 0.9, "k": 5},
                   "agreement": {"ari": 0.8, "nmi": 0.7}},
}
METRICS_BY_TYPE = {
    "classification": CLASSIFICATION_METRICS,
    "clustering": CLUSTERING_METRICS,
    "dimensionality_reduction": DIM_REDUCTION_METRICS,
}

PARAMS = {"algorithm": "random_forest", "max_depth": 8}
ARTIFACTS = [{"id": "ml-1:model.joblib", "kind": "joblib", "role": "model",
              "path": "model.joblib", "size": 1024}]
BOUNDARIES = {"T1": "2001-12-31T23:59:59Z", "T2": "2002-06-30T23:59:59Z"}


def _record(status="succeeded"):
    return {"agent_task_id": "LT-0001", "hadoop_task_id": "ml-1",
            "name": "训练分类器", "summary": "训练分类器", "status": status,
            "stage": "成功", "progress_percent": 100,
            "estimated_seconds": 600, "updated_at": 1.0}


class TestMLMetricsBlocks(unittest.TestCase):
    def test_each_task_type_lands_in_same_named_block(self):
        """三类 task_type：metrics 原样落到同名板块。"""
        for task_type, metrics in METRICS_BY_TYPE.items():
            with self.subTest(task_type=task_type):
                result = {"task_type": task_type, "metrics": metrics}
                blocks = long_task.result_payload(_record(), result)["blocks"]
                self.assertIn(task_type, blocks)
                self.assertEqual(metrics, blocks[task_type])

    def test_absent_metrics_no_block(self):
        """带 task_type 但无 metrics → 不建板块（不造空壳）。"""
        for task_type in METRICS_BY_TYPE:
            with self.subTest(task_type=task_type):
                blocks = long_task.result_payload(
                    _record(), {"task_type": task_type})["blocks"]
                self.assertNotIn(task_type, blocks)

    def test_absent_task_type_no_block(self):
        """有 metrics 但无 task_type → 不臆造板块。"""
        blocks = long_task.result_payload(
            _record(), {"metrics": CLASSIFICATION_METRICS})["blocks"]
        for task_type in METRICS_BY_TYPE:
            self.assertNotIn(task_type, blocks)

    def test_unknown_task_type_no_block(self):
        """未知 task_type → 不落到任何 ML 板块。"""
        blocks = long_task.result_payload(
            _record(), {"task_type": "regression",
                        "metrics": {"r2": 0.5}})["blocks"]
        self.assertNotIn("regression", blocks)

    def test_params_and_artifacts_passed_through(self):
        """params / artifacts / time_boundaries 存在即透传。"""
        result = {"params": PARAMS, "artifacts": ARTIFACTS,
                  "time_boundaries": BOUNDARIES}
        blocks = long_task.result_payload(_record(), result)["blocks"]
        self.assertEqual(PARAMS, blocks["params"])
        self.assertEqual(ARTIFACTS, blocks["artifacts"])
        self.assertEqual(BOUNDARIES, blocks["time_boundaries"])

    def test_absent_params_artifacts_omitted(self):
        """params / artifacts 缺席 → 板块里不出现。"""
        blocks = long_task.result_payload(
            _record(), {"task_type": "clustering",
                        "metrics": CLUSTERING_METRICS})["blocks"]
        self.assertNotIn("params", blocks)
        self.assertNotIn("artifacts", blocks)

    def test_existing_block_keys_unchanged(self):
        """加性改动：既有板块键一个不少。"""
        result = {"scores": {"after": {"Accurate": 0.9}},
                  "counts": {"input_rows": 1},
                  "quarantine_samples": [{"line_no": 1}],
                  "report": "…报告…", "limitations": ["隔离 ≠ 修复"],
                  "task_type": "classification", "metrics": CLASSIFICATION_METRICS}
        blocks = long_task.result_payload(_record(), result)["blocks"]
        for key in ("task_status", "score_comparison", "data_volume",
                    "quarantine_samples", "report", "limitations"):
            self.assertIn(key, blocks)
        self.assertEqual(CLASSIFICATION_METRICS, blocks["classification"])


class TestOfflineDelegatesSameMapping(unittest.TestCase):
    def test_offline_result_uses_same_blocks(self):
        """离线直调信封走同一映射，无重复实现。"""
        envelope = {"ok": True, "task_id": "ml-1", "status": "succeeded",
                    "task_type": "clustering", "metrics": CLUSTERING_METRICS,
                    "params": PARAMS, "time_boundaries": BOUNDARIES}
        blocks = offline.result_payload(
            "c1", "cluster_users", {"algorithm": "kmeans"},
            envelope)["blocks"]
        self.assertEqual(CLUSTERING_METRICS, blocks["clustering"])
        self.assertEqual(PARAMS, blocks["params"])
        self.assertEqual(BOUNDARIES, blocks["time_boundaries"])

    def test_offline_absent_metrics_no_block(self):
        envelope = {"ok": True, "task_id": "ml-1", "status": "succeeded",
                    "task_type": "clustering"}
        blocks = offline.result_payload(
            "c1", "cluster_users", {"algorithm": "kmeans"},
            envelope)["blocks"]
        self.assertNotIn("clustering", blocks)


if __name__ == "__main__":
    unittest.main()
