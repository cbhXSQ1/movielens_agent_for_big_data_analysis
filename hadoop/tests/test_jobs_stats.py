# -*- coding: utf-8 -*-
"""M3c 测试：jobs/stats_marks.py —— 统计与标记作业（plan §5.1 作业 9）。

两个趟都要测：
  * `--source raw-ratings`：R9 的输入必须是**原始**数据（非法评分已被 R2 隔离，
    清洗结果里数不出来）。这里构造 1000+ 条非法评分来真正跨过 min_matches 阈值，
    而不是让它恒为空。
  * `--source cleaned`：R8 / M8 / X3。期望值直接取自 fixture 的手推结果：
    同名组 1 个（'Three Wishes (1995)' → ID 9,10）、
    同用户同电影多时间戳冲突 2 对（(1,1) 因 R4 修复毫秒而多出一个时间戳、
    (3,1) 两个时间戳）、从未被评分的用户 7 个 / 电影 10 个。
"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.pipeline import TABLE_FILES, read_raw_table  # noqa: E402

from test_jobs_dim import (counters, lines_of, numbered, run_job,  # noqa: E402
                           shuffle, _resolve_input)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")


class StatsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")

    def cleaned_input(self):
        """清洗后的 ratings + users + movies 混在一起（作业按字段集合自描述分派）。"""
        parts = []
        # ratings：validate → dedupe → cross
        v, err, rc = run_job("ratings_validate.py", ["--mode", "keep"],
                             numbered(self.raw, "ratings"))
        assert rc == 0, err
        mapped, err, rc = run_job("ratings_dedupe.py", [], v)
        assert rc == 0, err
        r, err, rc = run_job("ratings_dedupe.py", ["--reduce", "--mode", "keep"],
                             shuffle(mapped))
        assert rc == 0, err
        # 这里刻意不做 X1/X2：fixture 的 999 号孤儿不影响 R8/M8/X3 的期望值，
        # 但会让「从未被评分」的口径变复杂，故直接用去重后的评分表。
        parts.append(r)
        for table in ("users", "movies"):
            mid = _resolve_input(table, numbered(self.raw, table))
            out, err, rc = run_job("%s_resolve.py" % table,
                                   ["--reduce", "--mode", "keep"], shuffle(mid))
            assert rc == 0, err
            if table == "movies":
                resid, err, rc = run_job("movies_residual.py", ["--mode", "keep"], out)
                assert rc == 0, err
                out = resid
            parts.append(out)
        return "".join(parts)


class TestCleanedSide(StatsCase):
    @classmethod
    def setUpClass(cls):
        super(TestCleanedSide, cls).setUpClass()
        text = cls.cleaned_input(cls)
        mapped, err, rc = run_job("stats_marks.py", ["--source", "cleaned"], text)
        assert rc == 0, err
        out, err, rc = run_job("stats_marks.py", ["--reduce", "--source", "cleaned"],
                               shuffle(mapped))
        assert rc == 0, err
        cls.out, cls.err = out, err
        cls.tags = dict((json.loads(l)["tag"], json.loads(l)) for l in lines_of(out))

    def test_emits_three_tags(self):
        self.assertEqual({"M8", "R8", "X3"}, set(self.tags))

    def test_m8_finds_the_same_title_group(self):
        groups = self.tags["M8"]["groups"]
        self.assertEqual(["Three Wishes (1995)"], sorted(groups))
        self.assertEqual(["10", "9"], groups["Three Wishes (1995)"])

    def test_r8_finds_multi_timestamp_pairs(self):
        conflicts = self.tags["R8"]["conflicts"]
        # (1,1)：R4 把毫秒修成秒后与原来的秒值并存 → 两个时间戳
        # (3,1)：fixture 刻意注入的两个时间戳
        self.assertEqual(["1::1", "3::1"], sorted(conflicts))
        self.assertEqual(2, len(conflicts["1::1"]))
        self.assertEqual(2, len(conflicts["3::1"]))
        self.assertEqual(2, counters(self.err)[("groups", "R8")], "冲突对 2 个")

    def test_x3_reports_never_rated_objects(self):
        x3 = self.tags["X3"]
        # 清洗后用户 1..10，其中 1/2/3 有评分 → 7 个从未评分
        self.assertEqual(7, len(x3["users_never_rated"]))
        # 清洗后电影 14 部，其中 1/2/3/13 有评分 → 10 部从未评分
        self.assertEqual(10, len(x3["movies_never_rated"]))
        c = counters(self.err)
        self.assertEqual(7, c[("groups", "X3_users")])
        self.assertEqual(10, c[("groups", "X3_movies")])

    def test_marker_counters(self):
        c = counters(self.err)
        self.assertEqual(1, c[("groups", "M8")], "同名组 1 个")
        self.assertEqual(2, c[("marks", "M8")], "被标记的电影 2 部")


class TestRawRatingsSide(StatsCase):
    def test_r9_threshold_is_actually_exercised(self):
        """构造 1000 条非法评分，确保真的跨过 min_matches，而不是恒为空。"""
        base = read_raw_table(os.path.join(self.raw, TABLE_FILES["ratings"]))
        rows = [(n, raw) for n, raw in base]
        # 用户 7 再补 1200 条非法评分（评分 'five'）
        for i in range(1200):
            rows.append((10 ** 6 + i, "7::1::five::978824268"))
        text = "".join("%d\t%s\n" % (n, raw) for n, raw in rows)
        # R9 直接读 driver 物化的原始行 —— 不经过任何清洗作业，
        # 因为非法评分会在 R2 被隔离，清洗后就没得数了。
        mapped, err2, rc2 = run_job("stats_marks.py", ["--source", "raw-ratings"], text)
        self.assertEqual(0, rc2, err2)
        out, err3, rc3 = run_job("stats_marks.py", ["--reduce", "--source", "raw-ratings"],
                                 shuffle(mapped))
        self.assertEqual(0, rc3, err3)
        obj = json.loads(lines_of(out)[0])
        self.assertEqual("R9", obj["tag"])
        self.assertEqual(1000, obj["min_matches"])
        self.assertIn("7", obj["counts"])
        self.assertGreaterEqual(obj["counts"]["7"], 1200)
        self.assertEqual(1, counters(err3)[("groups", "R9")])
        self.assertEqual(1, counters(err3)[("detail", "R9_matched_users")])

    def test_unknown_source_fails_loudly(self):
        out, err, rc = run_job("stats_marks.py", ["--source", "bogus"], "")
        self.assertNotEqual(0, rc)
        self.assertIn("--source", err)

    def test_unrecognised_record_shape_fails_loudly(self):
        bad = json.dumps({"n": 1, "raw": "x", "f": {"MovieID": "1"}}, ensure_ascii=True,
                         sort_keys=True, separators=(",", ":"))
        out, err, rc = run_job("stats_marks.py", ["--source", "cleaned"], bad + "\n")
        self.assertNotEqual(0, rc)
        self.assertIn("ConfigError", err)


if __name__ == "__main__":
    unittest.main()
