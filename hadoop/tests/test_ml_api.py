# -*- coding: utf-8 -*-
"""tests/test_ml_api.py —— 对外入口的集成测试（离线人造数据，不依赖集群）。

覆盖：结果字段与数据契约一致、落盘与索引、预测的成功与失败路径、参数校验、
"集群聚合"扩展点的如实报错、以及同一输入的可复现性。

缺依赖时跳过（系统解释器没有 numpy / scikit-learn）：
    PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_ml_api
"""

import json
import os
import sys
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_REPO_ROOT, "hadoop"), os.path.join(_REPO_ROOT, "hadoop", "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import numpy as np
    import ml_fixtures
    from ml import api, config
    from ml.errors import TrainingError
    HAVE_ML = True
    _IMPORT_ERROR = ""
except Exception as exc:                      # noqa: BLE001
    HAVE_ML = False
    _IMPORT_ERROR = str(exc)

SKIP_REASON = ("缺少机器学习依赖（numpy / scikit-learn）："
               "PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_ml_api"
               "；导入错误：%s" % _IMPORT_ERROR)


def _run(repo, task_type, algorithm, params=None, **kwargs):
    return api.run_training(task_type, algorithm, params or {}, repo["repo_root"],
                            progress=None, **kwargs)


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class TrainingResultTest(unittest.TestCase):
    """训练返回的字段与数据契约 §3.1 对齐，且落盘产物与清单一致。"""

    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=200, n_movies=60, n_ratings=4000)
        self.repo = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def _assert_contract_fields(self, result, task_type, algorithm):
        for key in ("model_id", "status", "status_label", "status_cn", "task_type",
                    "algorithm", "data_version", "time_boundaries", "params", "metrics",
                    "artifacts", "created_at", "started_at", "finished_at"):
            self.assertIn(key, result, key)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["status_label"], "成功")
        self.assertEqual(result["status_cn"], "成功")
        self.assertEqual(result["task_type"], task_type)
        self.assertEqual(result["algorithm"], algorithm)
        self.assertTrue(result["model_id"].startswith("ml-"))
        self.assertEqual(set(result["time_boundaries"]), {"T1", "T2"})
        self.assertIsInstance(result["metrics"], dict)
        self.assertIsInstance(result["artifacts"], list)

    def test_classification_result_and_artifacts(self):
        result = _run(self.repo, "classification", "naive_bayes")
        self._assert_contract_fields(result, "classification", "naive_bayes")
        roles = [item["role"] for item in result["artifacts"]]
        self.assertIn("model", roles)
        self.assertIn("meta", roles)
        for item in result["artifacts"]:
            self.assertEqual(set(["id", "title", "kind", "role", "path", "size"]),
                             set(item.keys()))
            full = os.path.join(self.repo["ml_root"], result["model_id"], item["path"])
            self.assertTrue(os.path.isfile(full), item["path"])
            self.assertEqual(item["size"], os.path.getsize(full), item["path"])
        # 元数据 sidecar 要能读出，且与返回一致
        meta = api.get_model(result["model_id"], self.repo["repo_root"])["model"]
        self.assertEqual(meta["task_type"], "classification")
        self.assertEqual(meta["algorithm"], "naive_bayes")
        self.assertIn("feature_spec", meta)
        self.assertIn("leakage", meta["feature_spec"])

    def test_clustering_result_writes_profile_and_labels(self):
        result = _run(self.repo, "clustering", "kmeans", {"k": 3})
        self._assert_contract_fields(result, "clustering", "kmeans")
        self.assertEqual(result["params"]["k"], 3)
        roles = sorted(item["role"] for item in result["artifacts"])
        self.assertEqual(roles, ["labels", "meta", "model", "profile"])
        labels_file = [i for i in result["artifacts"] if i["role"] == "labels"][0]
        self.assertEqual(labels_file["kind"], "csv")
        with open(os.path.join(self.repo["ml_root"], result["model_id"], "labels.csv"),
                  encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines() if line]
        self.assertEqual(lines[0], "user_id,cluster_id")
        self.assertGreater(len(lines) - 1, 0)
        # 三列都在 0..k-1 之间
        for line in lines[1:]:
            _uid, cluster_id = line.split(",")
            self.assertGreaterEqual(int(cluster_id), 0)
            self.assertLess(int(cluster_id), 3)

    def test_reduction_result_reports_comparison(self):
        result = _run(self.repo, "dimensionality_reduction", "pca", {"n_components": 4})
        self._assert_contract_fields(result, "dimensionality_reduction", "pca")
        metrics = result["metrics"]
        self.assertEqual(metrics["n_components"], 4)
        self.assertEqual(len(metrics["explained_variance_ratio"]), 4)
        for side in ("before", "after"):
            self.assertIn("silhouette", metrics["comparison"][side])
            self.assertIn("davies_bouldin", metrics["comparison"][side])
        self.assertIn("ari", metrics["comparison"]["agreement"])
        self.assertIn("nmi", metrics["comparison"]["agreement"])
        roles = sorted(item["role"] for item in result["artifacts"])
        self.assertEqual(roles, ["coords", "meta", "model"])

    def test_index_lists_all_models_newest_first(self):
        first = _run(self.repo, "classification", "naive_bayes")
        second = _run(self.repo, "clustering", "kmeans", {"k": 2})
        listed = api.list_models(self.repo["repo_root"])["models"]
        self.assertEqual(len(listed), 2)
        ids = [item["model_id"] for item in listed]
        self.assertIn(first["model_id"], ids)
        self.assertIn(second["model_id"], ids)
        for item in listed:
            self.assertEqual(set(item), {"model_id", "task_type", "algorithm", "data_version",
                                         "created_at", "metrics_summary",
                                         "version_fingerprint"})

    def test_same_params_give_same_fingerprint(self):
        a = _run(self.repo, "classification", "naive_bayes", {"alpha": 1.0})
        b = _run(self.repo, "classification", "naive_bayes", {"alpha": 1.0})
        self.assertEqual(a["version_fingerprint"], b["version_fingerprint"])
        c = _run(self.repo, "classification", "naive_bayes", {"alpha": 2.0})
        self.assertNotEqual(a["version_fingerprint"], c["version_fingerprint"])


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class PredictionTest(unittest.TestCase):

    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=200, n_movies=60, n_ratings=4000)
        self.repo = self._ctx.__enter__()
        self.model = _run(self.repo, "classification", "naive_bayes")
        self.cluster_model = _run(self.repo, "clustering", "kmeans", {"k": 2})

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_prediction_success(self):
        out = api.predict_rating(self.model["model_id"], 1, 2, self.repo["repo_root"])
        self.assertEqual(set(out), {"model_id", "user_id", "movie_id", "prediction",
                                    "model_version"})
        self.assertEqual(out["user_id"], "1")          # 编号按字符串回显
        self.assertEqual(out["movie_id"], "2")
        self.assertIn(out["prediction"]["label"], (0, 1))
        self.assertGreaterEqual(out["prediction"]["probability"], 0.0)
        self.assertLessEqual(out["prediction"]["probability"], 1.0)
        self.assertEqual(out["prediction"]["threshold"], 0.5)
        self.assertTrue(out["model_version"])

    def test_prediction_is_reproducible(self):
        a = api.predict_rating(self.model["model_id"], 3, 4, self.repo["repo_root"])
        b = api.predict_rating(self.model["model_id"], 3, 4, self.repo["repo_root"])
        self.assertEqual(a, b)

    def test_unknown_model(self):
        with self.assertRaises(TrainingError) as ctx:
            api.predict_rating("ml-does-not-exist", 1, 2, self.repo["repo_root"])
        self.assertEqual(ctx.exception.code, "MODEL_NOT_FOUND")
        self.assertIn("ml-does-not-exist", ctx.exception.extra.get("model_id", ""))

    def test_non_classification_model_cannot_predict(self):
        with self.assertRaises(TrainingError) as ctx:
            api.predict_rating(self.cluster_model["model_id"], 1, 2, self.repo["repo_root"])
        self.assertEqual(ctx.exception.code, "USAGE")
        self.assertIn("分类", str(ctx.exception))

    def test_unknown_user_or_movie(self):
        for user_id, movie_id, param in ((999999, 2, "user_id"), (1, 999999, "movie_id")):
            with self.assertRaises(TrainingError) as ctx:
                api.predict_rating(self.model["model_id"], user_id, movie_id,
                                   self.repo["repo_root"])
            self.assertEqual(ctx.exception.code, "USAGE")
            self.assertEqual(ctx.exception.extra.get("param"), param)

    def test_non_integer_ids(self):
        with self.assertRaises(TrainingError) as ctx:
            api.predict_rating(self.model["model_id"], "abc", 2, self.repo["repo_root"])
        self.assertEqual(ctx.exception.code, "USAGE")


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class ParameterValidationTest(unittest.TestCase):

    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=120, n_movies=40, n_ratings=2000)
        self.repo = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_unknown_task_or_algorithm(self):
        for task_type, algorithm in (("nope", "naive_bayes"),
                                     ("classification", "kmeans"),
                                     ("clustering", "pca")):
            with self.assertRaises(TrainingError) as ctx:
                _run(self.repo, task_type, algorithm)
            self.assertEqual(ctx.exception.code, "USAGE")
            self.assertIn(ctx.exception.extra.get("param"), ("task_type", "algorithm"))

    def test_k_bounds(self):
        for k in (1, 51):
            with self.assertRaises(TrainingError) as ctx:
                _run(self.repo, "clustering", "kmeans", {"k": k})
            self.assertEqual(ctx.exception.code, "USAGE")
            self.assertEqual(ctx.exception.extra.get("param"), "k")

    def test_k_must_be_smaller_than_users(self):
        with self.assertRaises(TrainingError) as ctx:
            _run(self.repo, "clustering", "kmeans", {"k": 5000})
        self.assertEqual(ctx.exception.code, "USAGE")

    def test_classification_rejects_k(self):
        with self.assertRaises(TrainingError) as ctx:
            _run(self.repo, "classification", "naive_bayes", {"k": 3})
        self.assertEqual(ctx.exception.code, "USAGE")

    def test_components_bounds(self):
        for n in (1, 999):
            with self.assertRaises(TrainingError) as ctx:
                _run(self.repo, "dimensionality_reduction", "pca", {"n_components": n})
            self.assertEqual(ctx.exception.code, "USAGE")

    def test_cluster_aggregation_is_an_honest_error(self):
        """特征来源配成集群聚合时，必须明确报"尚未接入"，绝不静默退化成本机计算。"""
        with mock.patch.dict(os.environ, {config.ENV_FEATURE_SOURCE: "cluster"}):
            with self.assertRaises(TrainingError) as ctx:
                _run(self.repo, "classification", "naive_bayes")
        self.assertEqual(ctx.exception.code, "TASK_FAILED")
        self.assertIn("cluster_aggregation", json.dumps(ctx.exception.extra, ensure_ascii=False))

    def test_feature_source_default_is_local(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(config.ENV_FEATURE_SOURCE, None)
            self.assertEqual(config.feature_source(), "local")


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class GetModelTest(unittest.TestCase):
    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=120, n_movies=40, n_ratings=2000)
        self.repo = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_get_model_missing(self):
        with self.assertRaises(TrainingError) as ctx:
            api.get_model("ml-nope", self.repo["repo_root"])
        self.assertEqual(ctx.exception.code, "MODEL_NOT_FOUND")

    def test_get_model_has_fresh_meta_artifact(self):
        result = _run(self.repo, "classification", "naive_bayes")
        meta = api.get_model(result["model_id"], self.repo["repo_root"])["model"]
        meta_items = [item for item in meta["artifacts"] if item["role"] == "meta"]
        self.assertEqual(len(meta_items), 1)
        self.assertEqual(meta_items[0]["kind"], "json")
        full = os.path.join(self.repo["ml_root"], result["model_id"], "meta.json")
        self.assertEqual(meta_items[0]["size"], os.path.getsize(full))


if __name__ == "__main__":
    unittest.main()
