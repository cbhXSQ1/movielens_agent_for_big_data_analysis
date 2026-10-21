# -*- coding: utf-8 -*-
"""tests/test_ml_features.py —— 特征层的单元测试（重点：防时间泄漏）。

系统解释器没有 numpy / scikit-learn，所以整个模块在缺依赖时**按条件跳过**并写明怎么跑：
    PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_ml_features
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
    from ml import config, datasets, features
    HAVE_ML = True
    _IMPORT_ERROR = ""
except Exception as exc:                      # noqa: BLE001 —— 缺依赖就是跳过，不是失败
    HAVE_ML = False
    _IMPORT_ERROR = str(exc)

SKIP_REASON = ("缺少机器学习依赖（numpy / scikit-learn）：用仓库内独立环境跑，"
               "例如 PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_ml_features"
               "；导入错误：%s" % _IMPORT_ERROR)


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class PrefixAggregateTest(unittest.TestCase):
    """前缀聚合：每条样本只看到它之前的数据（防泄漏的核心）。"""

    def _ratings(self):
        return {"user": np.array([1, 1, 1, 2], dtype=np.int64),
                "movie": np.array([10, 20, 10, 10], dtype=np.int64),
                "rating": np.array([5.0, 3.0, 4.0, 5.0], dtype=np.float32),
                "ts": np.array([100, 200, 300, 400], dtype=np.int64)}

    def test_first_records_have_no_history(self):
        r = self._ratings()
        user_avg, user_count, movie_avg, movie_count = features.prefix_aggregates(
            r["user"], r["movie"], r["rating"], r["ts"])
        self.assertTrue(np.isnan(user_avg[0]))
        self.assertEqual(int(user_count[0]), 0)
        self.assertTrue(np.isnan(movie_avg[0]))
        self.assertEqual(int(movie_count[0]), 0)

    def test_history_is_point_in_time(self):
        r = self._ratings()
        user_avg, user_count, movie_avg, movie_count = features.prefix_aggregates(
            r["user"], r["movie"], r["rating"], r["ts"])
        # 第 2 条（用户 1 的第 2 次）：只看得到第 1 条的 5 分
        self.assertAlmostEqual(float(user_avg[1]), 5.0)
        self.assertEqual(int(user_count[1]), 1)
        # 第 3 条：用户 1 前两条是 5 与 3 → 均分 4
        self.assertAlmostEqual(float(user_avg[2]), 4.0)
        self.assertEqual(int(user_count[2]), 2)
        # 第 4 条（用户 2 第一次，电影 10 前两条是 5 与 4）→ 用户无历史、电影有
        self.assertTrue(np.isnan(user_avg[3]))
        self.assertAlmostEqual(float(movie_avg[3]), 4.5)
        self.assertEqual(int(movie_count[3]), 2)

    def test_future_rows_do_not_change_history(self):
        """把"未来"的记录改得再多，历史样本的特征也必须一字不变。"""
        base = {"user": np.array([1, 1, 2, 2, 1], dtype=np.int64),
                "movie": np.array([9, 9, 9, 8, 9], dtype=np.int64),
                "rating": np.array([5.0, 4.0, 1.0, 2.0, 3.0], dtype=np.float32),
                "ts": np.array([10, 20, 30, 40, 50], dtype=np.int64)}
        before = features.prefix_aggregates(base["user"], base["movie"],
                                            base["rating"], base["ts"])

        # 只改最后一条（时间最晚）的评分，前面四条的历史特征不该受影响
        tampered = dict(base)
        tampered["rating"] = base["rating"].copy()
        tampered["rating"][-1] = 1.0
        after = features.prefix_aggregates(tampered["user"], tampered["movie"],
                                           tampered["rating"], tampered["ts"])
        for i in range(4):
            self.assertTrue(np.isnan(before[0][i]) and np.isnan(after[0][i])
                            or np.isclose(before[0][i], after[0][i]), "user_avg 第 %d 条变了" % i)
            self.assertEqual(int(before[1][i]), int(after[1][i]))
            self.assertTrue(np.isnan(before[2][i]) and np.isnan(after[2][i])
                            or np.isclose(before[2][i], after[2][i]), "movie_avg 第 %d 条变了" % i)
            self.assertEqual(int(before[3][i]), int(after[3][i]))

    def test_shifting_all_timestamps_keeps_relative_history(self):
        r = self._ratings()
        a = features.prefix_aggregates(r["user"], r["movie"], r["rating"], r["ts"])
        b = features.prefix_aggregates(r["user"], r["movie"], r["rating"], r["ts"] + 10 ** 6)
        for i in range(4):
            self.assertTrue(np.allclose(np.nan_to_num(a[i]), np.nan_to_num(b[i])))


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class SplitMaskTest(unittest.TestCase):
    def test_train_valid_test_are_disjoint_and_cover(self):
        ts = np.array([10, 20, 30, 40], dtype=np.int64)
        train, valid, test = features.split_masks(ts, 20, 30)
        self.assertEqual(train.tolist(), [True, True, False, False])
        self.assertEqual(valid.tolist(), [False, False, True, False])
        self.assertEqual(test.tolist(), [False, False, False, True])
        self.assertFalse((train & valid).any())


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class ParseTest(unittest.TestCase):
    """解析要宽容：真实数据里有缺属性的用户（不能当成不存在）、有坏行（要计数）。"""

    def test_users_with_missing_attributes_stay_users(self):
        with ml_fixtures.offline_repo(n_users=40, n_movies=10, n_ratings=120) as repo:
            users, bad, partial = datasets.parse_users(
                os.path.join(repo["data_dir"], datasets.TABLE_FILES["users"]))
        self.assertEqual(bad, 0)
        self.assertEqual(len(users), 40)          # 缺属性的用户仍在
        self.assertGreater(partial, 0)            # 且被单独计数
        missing = [u for u, attrs in users.items() if attrs["gender"] is None]
        self.assertTrue(missing)
        self.assertIsNone(users[missing[0]]["age"])

    def test_bad_lines_are_counted_not_silently_dropped(self):
        import tempfile

        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "movies.dat")
        with open(path, "w", encoding="iso-8859-1") as fh:
            fh.write("1::Good (2000)::Comedy\n")
            fh.write("2::Broken\n")               # 字段数不对
            fh.write("not-a-number::Title::Drama\n")
        movies, bad = datasets.parse_movies(path)
        self.assertEqual(bad, 2)
        self.assertEqual(list(movies), [1])


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class BinTest(unittest.TestCase):
    def test_quantile_edges_and_binning(self):
        values = np.arange(100, dtype=np.float64)
        edges = features.fit_bin_edges(values, bins=10)
        self.assertEqual(len(edges), 9)           # 10 箱 → 9 条内边界
        bins = features.apply_bins(values, edges)
        self.assertEqual(int(bins.min()), 0)
        self.assertEqual(int(bins.max()), 9)
        self.assertEqual(bins.shape[0], 100)

    def test_nan_is_handled(self):
        edges = features.fit_bin_edges(np.array([1.0, 2.0, 3.0]), bins=2)
        out = features.apply_bins(np.array([np.nan, 2.0]), edges)
        self.assertEqual(out.shape[0], 2)
        self.assertTrue(int(out[0]) >= 0)


@unittest.skipUnless(HAVE_ML, SKIP_REASON)
class UserProfileTest(unittest.TestCase):
    def test_profile_shape_and_normalization(self):
        with ml_fixtures.offline_repo() as repo:
            users, movies, ratings = ml_fixtures.load_fixture(repo)
            prof = features.build_user_profile(ratings, movies, repo["t1"])
        matrix = prof["matrix"]
        self.assertEqual(matrix.shape[1], config.PROFILE_DIM)      # 18 类型 + 4 行为
        shares = matrix[:, :len(config.GENRES)]
        sums = shares.sum(axis=1)
        # 有评分的用户，类型占比应归一到 1（无评分的用户不会出现在画像里）
        self.assertTrue(np.allclose(sums, 1.0, atol=1e-4))
        self.assertEqual(prof["train_window"]["users"], matrix.shape[0])
        self.assertGreater(prof["train_window"]["ratings"], 0)
        self.assertEqual(prof["global_genre_share"].shape, (len(config.GENRES),))

    def test_profile_uses_only_train_window(self):
        with ml_fixtures.offline_repo() as repo:
            users, movies, ratings = ml_fixtures.load_fixture(repo)
            prof = features.build_user_profile(ratings, movies, repo["t1"])
            # 把训练期之后的评分全部改成"另一个用户、另一个电影"，画像不该有任何变化
            later = ratings["ts"] > repo["t1"]
            tampered = {k: ratings[k].copy() for k in ("user", "movie", "rating", "ts")}
            tampered["user"][later] = 9999
            tampered["movie"][later] = 999
            tampered["rating"][later] = 1.0
            prof2 = features.build_user_profile(tampered, movies, repo["t1"])
        self.assertTrue(np.allclose(prof["matrix"], prof2["matrix"]))
        self.assertEqual(prof["user_ids"].tolist(), prof2["user_ids"].tolist())

    def test_standardize_keeps_shares_and_z_scores_behaviors(self):
        matrix = np.array([[0.5, 0.5], [0.2, 0.8], [0.9, 0.1]], dtype=np.float32)
        # 用 2 列当 1 个类型维度 + 1 个行为维度，便于手算
        out, params = features.standardize(matrix, n_genres=1)
        self.assertTrue(np.allclose(out[:, 0], matrix[:, 0]))          # 占比列不动
        self.assertAlmostEqual(float(out[:, 1].mean()), 0.0, places=5)  # 行为列均值为 0
        again = features.apply_standardize(matrix, params, n_genres=1)
        self.assertTrue(np.allclose(out, again))


if __name__ == "__main__":
    unittest.main()
