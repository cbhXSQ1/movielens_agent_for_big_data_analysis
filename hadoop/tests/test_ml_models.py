# -*- coding: utf-8 -*-
"""tests/test_ml_models.py —— 六种算法的单元测试（人造数据、可手算的小规模）。

覆盖点：训练能跑通、指标字段齐全且取值范围合理、两种算法同口径（同一份画像与同一个 k）、
超参白名单与上下界、降维的"前后对比"字段完整。

缺依赖时整个模块跳过（系统解释器没有 numpy / scikit-learn）：
    PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_ml_models
"""

import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_REPO_ROOT, "hadoop"), os.path.join(_REPO_ROOT, "hadoop", "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import numpy as np
    import ml_fixtures
    from ml import config, datasets, features, classification, clustering, reduction
    from ml.errors import TrainingError
    HAVE_ML = True
    _IMPORT_ERROR = ""
except Exception as exc:                      # noqa: BLE001
    HAVE_ML = False
    _IMPORT_ERROR = str(exc)

SKIP_REASON = ("缺少机器学习依赖（numpy / scikit-learn）："
               "PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_ml_models"
               "；导入错误：%s" % _IMPORT_ERROR)


def _built(repo):
    users, movies, ratings = ml_fixtures.load_fixture(repo)
    built = features.build_classifier_matrix(ratings, users, movies, repo["t1"])
    built["t1_epoch"] = repo["t1"]
    built["t2_epoch"] = repo["t2"]
    return built


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class ClassificationTest(unittest.TestCase):

    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=200, n_movies=60, n_ratings=4000)
        self.repo = self._ctx.__enter__()
        self.built = _built(self.repo)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def _metrics_are_well_formed(self, metrics, split_sizes):
        for key in ("f1", "auc", "precision", "recall", "pr_auc", "positive_rate"):
            self.assertGreaterEqual(metrics[key], 0.0, key)
            self.assertLessEqual(metrics[key], 1.0, key)
            # 数据契约：零到一之间的小数、四位
            self.assertEqual(round(metrics[key], 4), metrics[key], key)
        self.assertEqual(sum(metrics["confusion_matrix"].values()), split_sizes)
        self.assertIn(metrics["evaluated_on"], ("test", "valid"))
        self.assertIsInstance(metrics["feature_importance"], list)
        self.assertTrue(metrics["leakage"]["policy"])

    def test_naive_bayes_metrics(self):
        model, encoding, eff, _ = classification.train("naive_bayes", self.built, {}, None)
        metrics = classification.evaluate(model, self.built, eff, None)
        self._metrics_are_well_formed(metrics, metrics["split_sizes"][metrics["evaluated_on"]])
        self.assertEqual(metrics["split_sizes"]["train"] + metrics["split_sizes"]["valid"]
                         + metrics["split_sizes"]["test"],
                         int(self.built["y"].size))
        self.assertIn("分箱", encoding["encoding"])
        self.assertTrue(metrics["feature_importance"])
        # 混淆矩阵的行数之和应等于评估集里"正类样本数"（tp+fn 就是真实正类数）
        mask = self.built["ts"] > (self.repo["t2"] if metrics["evaluated_on"] == "test"
                                   else self.repo["t1"])
        positives = int(self.built["y"][mask].sum())
        self.assertEqual(metrics["confusion_matrix"]["tp"] + metrics["confusion_matrix"]["fn"],
                         positives)

    def test_random_forest_metrics(self):
        model, encoding, eff, importances = classification.train(
            "random_forest", self.built, {"n_estimators": 15}, None)
        metrics = classification.evaluate(model, self.built, eff, None)
        self._metrics_are_well_formed(metrics, metrics["split_sizes"][metrics["evaluated_on"]])
        self.assertEqual(eff["n_estimators"], 15)
        self.assertIn("n_jobs", encoding)
        self.assertEqual(encoding["n_jobs"], 1)   # 并行在本机被拒，如实回显为 1

    def test_threshold_changes_reported_result_only(self):
        model, _enc, eff, _ = classification.train(
            "random_forest", self.built, {"n_estimators": 10, "threshold": 0.9}, None)
        metrics = classification.evaluate(model, self.built, eff, None)
        self.assertEqual(metrics["threshold"], 0.9)
        # 阈值提高 → 判为正类的数量减少（tp+fp 变小）
        strict = metrics["confusion_matrix"]["tp"] + metrics["confusion_matrix"]["fp"]
        model2, _e2, eff2, _ = classification.train(
            "random_forest", self.built, {"n_estimators": 10, "threshold": 0.2}, None)
        loose = classification.evaluate(model2, self.built, eff2, None)
        loose_count = loose["confusion_matrix"]["tp"] + loose["confusion_matrix"]["fp"]
        self.assertLessEqual(strict, loose_count)

    def test_unknown_hyperparam_is_rejected(self):
        with self.assertRaises(TrainingError) as ctx:
            classification.train("naive_bayes", self.built, {"nope": 1}, None)
        self.assertEqual(ctx.exception.code, "USAGE")
        self.assertIn("nope", str(ctx.exception))

    def test_hyperparam_bounds(self):
        for params in ({"n_estimators": 1}, {"n_estimators": 9999},
                       {"max_depth": 1}, {"threshold": 0.99}, {"alpha": 100}):
            with self.assertRaises(TrainingError) as ctx:
                classification.train(
                    "random_forest" if "n_estimators" in params or "max_depth" in params
                    else "naive_bayes", self.built, params, None)
            self.assertEqual(ctx.exception.code, "USAGE", params)

    def test_positive_rate_matches_holdout(self):
        """正类比例必须来自评估集本身（不是全局，也不是训练集）。"""
        model, _enc, eff, _ = classification.train("naive_bayes", self.built, {}, None)
        metrics = classification.evaluate(model, self.built, eff, None)
        mask = self.built["ts"] > (self.repo["t2"] if metrics["evaluated_on"] == "test"
                                   else self.repo["t1"])
        expected = float(self.built["y"][mask].mean())
        self.assertAlmostEqual(metrics["positive_rate"], round(expected, 4), places=4)


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class ClusteringTest(unittest.TestCase):

    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=240, n_movies=60, n_ratings=4000)
        self.repo = self._ctx.__enter__()
        users, movies, ratings = ml_fixtures.load_fixture(self.repo)
        self.prof = features.build_user_profile(ratings, movies, self.repo["t1"])
        self.X, self.std_params = features.standardize(self.prof["matrix"])

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_kmeans_and_hierarchical_share_the_same_k(self):
        k = 3
        model_a, labels_a, eff_a = clustering.train("kmeans", self.X, k, {}, None)
        model_b, labels_b, eff_b = clustering.train("hierarchical", self.X, k, {}, None)
        self.assertEqual(len(set(labels_a.tolist())), k)
        self.assertEqual(len(set(labels_b.tolist())), k)
        self.assertIn("random_state", eff_a)
        self.assertNotIn("max_iter", eff_b)        # 层次聚类没有迭代上限这一项

    def test_metrics_fields_and_profile_labels(self):
        k = 3
        model, labels, eff = clustering.train("kmeans", self.X, k, {}, None)
        metrics = clustering.metrics(self.X, labels, k, self.prof["raw_behaviors"],
                                     self.prof["global_genre_share"])
        self.assertEqual(metrics["k"], k)
        self.assertGreaterEqual(metrics["silhouette"], -1.0)
        self.assertLessEqual(metrics["silhouette"], 1.0)
        self.assertEqual(len(metrics["clusters"]), k)
        total = 0
        for cluster in metrics["clusters"]:
            total += cluster["size"]
            self.assertIsInstance(cluster["label"], str)
            self.assertTrue(cluster["label"])
            self.assertIsInstance(cluster["top_genres"], list)
            self.assertLessEqual(len(cluster["top_genres"]), 3)
            for key in ("activity", "avg_rating", "rating_std", "recency"):
                self.assertIn(key, cluster["behavior"])
        self.assertEqual(total, self.X.shape[0])

    def test_k_selection_reports_candidates(self):
        k, info = clustering.select_k(self.X, candidates=(2, 3, 4))
        self.assertIn(k, (2, 3, 4))
        self.assertEqual(sorted(item["k"] for item in info["candidates"]), [2, 3, 4])
        for item in info["candidates"]:
            self.assertGreaterEqual(item["silhouette"], -1.0)
            self.assertLessEqual(item["silhouette"], 1.0)

    def test_labels_are_reproducible(self):
        _, first, _ = clustering.train("kmeans", self.X, 3, {}, None)
        _, second, _ = clustering.train("kmeans", self.X, 3, {}, None)
        self.assertEqual(first.tolist(), second.tolist())      # 固定种子 → 结果确定


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class ReductionTest(unittest.TestCase):

    def setUp(self):
        self._ctx = ml_fixtures.offline_repo(n_users=240, n_movies=60, n_ratings=4000)
        self.repo = self._ctx.__enter__()
        users, movies, ratings = ml_fixtures.load_fixture(self.repo)
        self.prof = features.build_user_profile(ratings, movies, self.repo["t1"])
        self.X, _ = features.standardize(self.prof["matrix"])

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_pca_reports_variance_and_reconstruction(self):
        model, reduced, detail = reduction.train("pca", self.X, 4, {}, None)
        self.assertEqual(reduced.shape[1], 4)
        self.assertEqual(len(detail["explained_variance_ratio"]), 4)
        self.assertAlmostEqual(sum(detail["explained_variance_ratio"]),
                               float(sum(model.explained_variance_ratio_)), places=3)
        self.assertGreaterEqual(detail["reconstruction_error"], 0.0)
        self.assertIn("fit_seconds", detail["resource"])
        if os.name == "nt":
            # Windows 无 resource 模块：内存峰值取不到，只给 fit_seconds（见 reduction._peak_mb）
            self.assertNotIn("mem_mb", detail["resource"], "取不到就不该给")
        else:
            self.assertIn("mem_mb", detail["resource"])

    def test_nmf_has_no_explained_variance(self):
        model, reduced, detail = reduction.train("nmf", self.X, 4, {}, None)
        self.assertEqual(reduced.shape[1], 4)
        self.assertIsNone(detail["explained_variance_ratio"])   # 合同要求给 null
        self.assertGreaterEqual(detail["reconstruction_error"], 0.0)
        self.assertIn("shift", detail)                          # 非负平移参数要留下来

    def test_compare_reports_before_after_and_agreement(self):
        model, reduced, _detail = reduction.train("pca", self.X, 4, {}, None)
        comparison, labels_before, labels_after = reduction.compare(self.X, reduced, 3)
        for side in ("before", "after"):
            self.assertEqual(comparison[side]["k"], 3)
            self.assertIn("silhouette", comparison[side])
            self.assertIn("davies_bouldin", comparison[side])
        self.assertGreaterEqual(comparison["agreement"]["ari"], -1.0)
        self.assertLessEqual(comparison["agreement"]["ari"], 1.0)
        self.assertGreaterEqual(comparison["agreement"]["nmi"], 0.0)
        self.assertLessEqual(comparison["agreement"]["nmi"], 1.0)
        self.assertEqual(labels_before.shape, labels_after.shape)

    def test_component_choice_respects_bounds(self):
        with self.assertRaises(TrainingError):
            reduction.choose_components(self.X, 1)              # 小于下界
        with self.assertRaises(TrainingError):
            reduction.choose_components(self.X, 999)            # 大于上界
        chosen, info = reduction.choose_components(self.X, None)
        self.assertGreaterEqual(chosen, config.COMPONENTS_MIN)
        self.assertLessEqual(chosen, self.X.shape[1] - 1)


if __name__ == "__main__":
    unittest.main()

@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class BundleGuardTest(unittest.TestCase):
    """模型包体积护栏：超限必须失败并说明怎么调小，不留巨型文件。

    背景（实测）：随机森林最初不限深度、叶子最小一条，一百二十棵树把模型包写到
    接近五Ｇ，预测加载直接超时、磁盘也被吃掉。护栏就是为了让这类失控当场失败。
    """

    def test_oversized_bundle_fails_with_hint(self):
        import io, os, tempfile
        from ml import api, config
        from ml.errors import TrainingError
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "model.joblib")
        with io.open(path, "wb") as fh:
            fh.write(b"x" * 4096)
        old = config.MAX_BUNDLE_MB
        config.MAX_BUNDLE_MB = 0            # 任何文件都超限
        try:
            with self.assertRaises(TrainingError) as cm:
                api.assert_bundle_within_limit(path, "ml-test-guard")
            self.assertEqual("TASK_FAILED", cm.exception.code)
            self.assertEqual("bundle_too_large", getattr(cm.exception, "extra", {}).get("reason"))
            self.assertIn("max_depth", cm.exception.message)
            self.assertFalse(os.path.exists(path), "超限的模型包必须被删掉，不能留在磁盘上")
        finally:
            config.MAX_BUNDLE_MB = old

    def test_normal_bundle_passes(self):
        import io, os, tempfile
        from ml import api
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "model.joblib")
        with io.open(path, "wb") as fh:
            fh.write(b"x" * 1024)
        api.assert_bundle_within_limit(path, "ml-test-ok")
        self.assertTrue(os.path.exists(path))


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class CalibratedDefaultsTest(unittest.TestCase):
    """随机森林的默认值必须与校准结论一致（校准记录见实现方案）。"""

    def test_random_forest_defaults(self):
        from ml import config
        spec = {k["key"]: k for k in config.HYPERPARAMS[("classification", "random_forest")]}
        self.assertEqual(spec["max_depth"]["default"], 20)
        self.assertEqual(spec["min_samples_leaf"]["default"], 50)
        self.assertEqual(spec["n_estimators"]["default"], 120)
