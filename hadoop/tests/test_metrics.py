# -*- coding: utf-8 -*-
"""M2 测试：engine/metrics.py —— 评分公式（plan.md §7.1「评分公式：手算小样例」）。

小样例全部**手算**，期望值写在断言里，不由实现反推。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config_loader import ConfigError, load_schemes  # noqa: E402
from engine.metrics import (composite_score, compute_metrics,  # noqa: E402
                            dimension_scores, finalize)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")

R_SCHEMA = ["UserID", "MovieID", "Rating", "Timestamp"]
U_SCHEMA = ["UserID", "Gender", "Age", "Occupation", "Zip-code"]
M_SCHEMA = ["MovieID", "Title", "Genres"]

TS_MIN = 956703932
TS_MAX = 1046476799
DAY = 86400


def r(uid, mid, rating, ts):
    return {"UserID": uid, "MovieID": mid, "Rating": rating, "Timestamp": ts}


def u(uid, g, a, o, z):
    return {"UserID": uid, "Gender": g, "Age": a, "Occupation": o, "Zip-code": z}


def m(mid, title, genres):
    return {"MovieID": mid, "Title": title, "Genres": genres}


def dataset(ratings, users, movies, r_lines=None, u_lines=None, m_lines=None):
    return {"tables": {
        "ratings": {"parsed": ratings, "schema": R_SCHEMA,
                    "raw_lines": len(ratings) if r_lines is None else r_lines},
        "users": {"parsed": users, "schema": U_SCHEMA,
                  "raw_lines": len(users) if u_lines is None else u_lines},
        "movies": {"parsed": movies, "schema": M_SCHEMA,
                   "raw_lines": len(movies) if m_lines is None else m_lines},
    }}


class MetricsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES, SCORING)

    def ctx(self):
        """A4 用 ref_exists，必须提供 dim_keys（与作业侧广播维表同一契约）。"""
        return {"reference_domains": self.s.rules["reference_domains"],
                "dim_keys": {"users": set(["1", "2"]), "movies": set(["1", "2"])}}


class TestHandComputedSample(MetricsBase):
    """4 条评分 / 2 个用户 / 2 部电影的小样例，逐指标手算。"""

    def setUp(self):
        self.ratings = [r("1", "1", "5", "1000000000"),
                        r("1", "2", "0", "1000000000"),
                        r("2", "1", "3", "1000000000"),
                        r("2", "2", "4", "1000000000")]
        self.users = [u("1", "F", "25", "10", "12345"),
                      u("2", "M", "", "10", "12345")]
        self.movies = [m("1", "A (1995)", "Drama"),
                       m("2", "B (1996)", "Drama|Bad")]
        self.ds = dataset(self.ratings, self.users, self.movies,
                          r_lines=6, u_lines=2, m_lines=3)
        self.mv = compute_metrics(self.ds, self.s, self.ctx())

    def test_a1(self):
        # 3 条 Rating 在 1..5 内（'0' 非法）/ 4 条解析记录
        self.assertAlmostEqual(3 / 4.0, self.mv["A1"])

    def test_a2(self):
        self.assertAlmostEqual(1.0, self.mv["A2"])

    def test_a3(self):
        # u1 三个字段全合法；u2 Age='' 非法 → 5 / (3*2)
        self.assertAlmostEqual(5 / 6.0, self.mv["A3"])

    def test_a4(self):
        # dim_keys 覆盖全部 4 条评分的 UserID/MovieID
        self.assertAlmostEqual(1.0, self.mv["A4"])

    def test_a5(self):
        self.assertAlmostEqual(1.0, self.mv["A5"])

    def test_a6(self):
        # token：Drama / Drama / Bad → 2/3
        self.assertAlmostEqual(2 / 3.0, self.mv["A6"])

    def test_c1(self):
        # 非空字段 31 / 总字段 32（u2 的 Age 为空）
        self.assertAlmostEqual(31 / 32.0, self.mv["C1"])

    def test_c2(self):
        # 完整记录：ratings 4 + users 1 + movies 2 = 7；分母是原始行数 6+2+3
        self.assertAlmostEqual(7 / 11.0, self.mv["C2"])

    def test_c3(self):
        # 解析成功 4+2+2 = 8 / 11
        self.assertAlmostEqual(8 / 11.0, self.mv["C3"])

    def test_u1(self):
        self.assertAlmostEqual(1.0, self.mv["U1"])

    def test_u2(self):
        # 不重复行 = 各表 distinct(4,2,2) + 无法解析的行(2,0,1) = 11 / 11
        self.assertAlmostEqual(1.0, self.mv["U2"])

    def test_u3(self):
        self.assertAlmostEqual(1.0, self.mv["U3"])

    def test_f1_and_s_metrics(self):
        self.assertAlmostEqual(1.0, self.mv["F1"])
        self.assertAlmostEqual(8 / 11.0, self.mv["S1"])
        self.assertAlmostEqual(1.0, self.mv["S2"])
        self.assertAlmostEqual(1.0, self.mv["S3"])
        self.assertAlmostEqual(1.0, self.mv["S4"])


class TestRatioNumeratorIsSubsetOfDenominator(MetricsBase):
    """S3：分子必须落在分母总体内，否则比率可能超过 1。"""

    def test_non_int_timestamp_excluded_from_numerator(self):
        ratings = [r("1", "1", "5", "1000000000"),
                   r("1", "2", "4", "1000000000"),
                   r("2", "1", "3", "1000000000"),
                   r("2", "2", "2", "1000000000"),
                   r("2", "2", "2", "five")]      # 非整数时间戳
        ds = dataset(ratings, [u("1", "F", "25", "10", "12345")],
                     [m("1", "A (1995)", "Drama")])
        mv = compute_metrics(ds, self.s, self.ctx())
        # 分母 = is_int(Timestamp) 的 4 条；分子里不得混入 'five' 那条
        self.assertAlmostEqual(1.0, mv["S3"])

    def test_ratio_never_exceeds_one(self):
        ratings = [r("1", "1", "5", "1000000000000"),
                   r("2", "1", "5", "1000000000001"),
                   r("1", "2", "5", "five")]
        ds = dataset(ratings, [u("1", "F", "25", "10", "12345")],
                     [m("1", "A (1995)", "Drama"), m("2", "B (1996)", "Drama")])
        mv = compute_metrics(ds, self.s, self.ctx())
        self.assertLessEqual(mv["S3"], 1.0)
        # 两条整数里 2 条都是毫秒 → 分子 0 / 分母 2
        self.assertAlmostEqual(0.0, mv["S3"])


class TestUnparsedLinesCountAsUniqueRows(MetricsBase):
    """U2：无法解析的行不是任何记录的副本，必须计入「不重复行」。"""

    def test_bad_lines_are_unique_not_duplicates(self):
        ds = dataset([r("1", "1", "5", "1000000000")],
                     [u("1", "F", "25", "10", "12345")],
                     [m("1", "A (1995)", "Drama")],
                     r_lines=4, u_lines=2, m_lines=3)
        mv = compute_metrics(ds, self.s, self.ctx())
        # distinct(1,1,1) + unparsed(3,1,2) = 9 / raw 9
        self.assertAlmostEqual(1.0, mv["U2"])

    def test_true_duplicates_still_penalised(self):
        ds = dataset([r("1", "1", "5", "1000000000"),
                      r("1", "1", "5", "1000000000")],
                     [], [])
        mv = compute_metrics(ds, self.s, self.ctx())
        # distinct 1 / 2 行
        self.assertAlmostEqual(0.5, mv["U2"])


class TestFreshness(MetricsBase):
    """F2：以数据集声明的时间上界为基准，30 天内满分、180 天及以上 0 分。"""

    def _f2(self, ts):
        ds = dataset([r("1", "1", "5", str(ts))], [], [])
        return compute_metrics(ds, self.s, self.ctx())["F2"]

    def test_within_full_score_window(self):
        self.assertAlmostEqual(1.0, self._f2(TS_MAX - 10 * DAY))

    def test_half_score_at_midpoint(self):
        # gap=105 天 → 1 - (105-30)/150 = 0.5
        self.assertAlmostEqual(0.5, self._f2(TS_MAX - 105 * DAY))

    def test_zero_at_zero_score_window(self):
        self.assertAlmostEqual(0.0, self._f2(TS_MAX - 200 * DAY))

    def test_out_of_range_values_ignored(self):
        # 2100 年的时间戳超出声明范围，不参与新鲜度（那是 F1 的职责）
        ds = dataset([r("1", "1", "5", str(TS_MAX - 10 * DAY)),
                      r("2", "1", "5", "4102444800")], [], [])
        self.assertAlmostEqual(1.0, compute_metrics(
            ds, self.s, self.ctx())["F2"])


class TestAggregationAndRounding(MetricsBase):
    def test_dimension_weighted_mean(self):
        mv = {m["id"]: 1.0 for d in self.s.scoring["dimensions"] for m in d["metrics"]}
        mv["A1"] = 0.0
        dims = dimension_scores(mv, self.s)
        # Accurate: A1 权重 0.25 → 0.75 * 100
        self.assertAlmostEqual(75.0, dims["Accurate"])
        self.assertAlmostEqual(100.0, dims["Complete"])

    def test_composite_is_weighted_mean_of_dimensions(self):
        dims = {"Accurate": 100.0, "Complete": 0.0, "Unique": 0.0,
                "Up-to-date": 0.0, "Consistent": 0.0}
        self.assertAlmostEqual(25.0, composite_score(dims, self.s))

    def test_finalize_scales_to_0_100_and_rounds_to_2(self):
        mv = {m["id"]: 1 / 3.0 for d in self.s.scoring["dimensions"] for m in d["metrics"]}
        out = finalize(mv, self.s)
        self.assertEqual(33.33, out["metrics"]["A1"])
        self.assertEqual(33.33, out["dimensions"]["Accurate"])
        self.assertEqual(33.33, out["composite"])
        self.assertEqual(18, len(out["metrics"]))

    def test_all_dimension_and_metric_ids_present(self):
        mv = compute_metrics(dataset([], [], []), self.s, self.ctx())
        self.assertEqual(18, len(mv))
        dims = dimension_scores(mv, self.s)
        self.assertEqual({"Accurate", "Complete", "Unique", "Up-to-date", "Consistent"},
                         set(dims))


class TestErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES, SCORING)

    def test_missing_table_raises(self):
        with self.assertRaises(ConfigError):
            compute_metrics({"tables": {"ratings": {"parsed": [], "schema": R_SCHEMA}}},
                            self.s, {})

    def test_unknown_agg_raises(self):
        from engine.metrics import _eval_side
        bad = {"measure": "ratio",
               "numerator": {"agg": "no_such_agg", "table": "ratings"},
               "denominator": {"agg": "count", "on": "parsed_records", "table": "ratings"}}
        with self.assertRaises(ConfigError):
            _eval_side(dataset([], [], []), bad, {})

    def test_unknown_measure_raises(self):
        from engine.metrics import _eval_side
        with self.assertRaises(ConfigError):
            _eval_side(dataset([], [], []), {"measure": "nonsense"}, {})

    def test_missing_dim_keys_raises(self):
        # A4 依赖 ctx['dim_keys']：拿不到维表就必须报错，不能静默当 0
        with self.assertRaises(ConfigError):
            compute_metrics(dataset([], [], []), self.s, {})

    def test_zero_denominator_scores_full(self):
        # 空数据集：分母为 0 视为「无适用记录」，记满分（与参考原型一致）
        mv = compute_metrics(dataset([], [], []), self.s, {
            "dim_keys": {"users": set(), "movies": set()},
            "reference_domains": self.s.rules["reference_domains"]})
        self.assertAlmostEqual(1.0, mv["U3"])
        self.assertAlmostEqual(1.0, mv["S4"])


if __name__ == "__main__":
    unittest.main()
