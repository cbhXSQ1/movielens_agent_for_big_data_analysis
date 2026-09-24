# -*- coding: utf-8 -*-
"""M3b 测试：评分表 Streaming 作业的本地 stdin→stdout 行为（plan §7.2、§9.5）。

与 test_jobs_dim.py 同一套路：**子进程真实走管道**，shuffle 在测试内模拟。
重点验证三件容易出错的事：
  1. 阶段顺序（R4 必须先于 R5，否则 R5 会把毫秒值翻倍误判）
  2. 双模式互补 + 计数器落在正确的趟
  3. 跨表作业拿不到维表时必须报错，不能把孤儿当有效引用
"""
import io
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.pipeline import TABLE_FILES, read_raw_table  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
JOBS = os.path.join(REPO_ROOT, "hadoop", "jobs")
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")
PY = sys.executable or "python3"

from test_jobs_dim import (counters, lines_of, numbered,  # noqa: E402
                           run_job, shuffle, shuffle_within_keys,
                           _merge, _resolve_input)


class TestRatingsValidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.text = numbered(cls.raw, "ratings")

    def test_keep_quarantine_complementary(self):
        keep, _, _ = run_job("ratings_validate.py", ["--mode", "keep"], self.text)
        quar, _, _ = run_job("ratings_validate.py", ["--mode", "quarantine"], self.text)
        self.assertEqual(len(lines_of(self.text)),
                         len(lines_of(keep)) + len(lines_of(quar)))

    def test_quarantine_by_rule(self):
        quar, err, rc = run_job("ratings_validate.py", ["--mode", "quarantine"],
                                self.text)
        self.assertEqual(0, rc, err)
        c = counters(err)
        # fixture 里刻意注入了：R2 5 条（0/6/3.5/five/空）、R1 2 条（空 UserID/MovieID）、
        # R3 1 条（'five'）、R5 2 条（-1 / 2100 年）、P2 1 条（逗号）、P3 1 条（多字段）
        self.assertEqual(5, c[("quarantine", "R2")])
        self.assertEqual(2, c[("quarantine", "R1")])
        self.assertEqual(1, c[("quarantine", "R3")])
        self.assertEqual(2, c[("quarantine", "R5")])
        self.assertEqual(1, c[("quarantine", "P2")])
        self.assertEqual(1, c[("quarantine", "P3")])
        self.assertEqual(12, sum(v for k, v in c.items() if k[0] == "quarantine"))

    def test_r4_fix_counted_and_ms_becomes_seconds(self):
        keep, err, rc = run_job("ratings_validate.py", ["--mode", "keep"], self.text)
        self.assertEqual(0, rc, err)
        self.assertEqual(1, counters(err)[("fix", "R4_ms")])
        ts = [json.loads(l)["f"]["Timestamp"] for l in lines_of(keep)]
        self.assertIn("1009669071", ts, "毫秒时间戳应被 R4 修成秒")
        self.assertNotIn("1009669071000", ts)

    def test_r5_does_not_fire_on_the_repaired_millisecond_value(self):
        """R4 先于 R5：修复后的值落在合法区间，不该被 R5 隔离。"""
        out, err, rc = run_job("ratings_validate.py", ["--mode", "quarantine"], self.text)
        self.assertEqual(0, rc, err)
        quarantined_lines = []
        for rec in [json.loads(l) for l in lines_of(out)]:
            quarantined_lines.append(rec["raw_line"])
        self.assertNotIn("1::1::4::1009669071000", quarantined_lines)
        self.assertEqual(2, counters(err)[("quarantine", "R5")],
                         "只有 -1 与 2100 年两条该被 R5 隔离；毫秒那条已被 R4 修好")


class TestRatingsDedupe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.text = numbered(cls.raw, "ratings")

    def _validate_keep(self):
        out, err, rc = run_job("ratings_validate.py", ["--mode", "keep"], self.text)
        assert rc == 0, err
        return out

    def test_dedupe_removes_extra_copies(self):
        mid = self._validate_keep()
        kept_records = len(lines_of(mid))
        mapped, err, rc = run_job("ratings_dedupe.py", [], mid)
        self.assertEqual(0, rc, err)
        out, err2, rc2 = run_job("ratings_dedupe.py", ["--reduce", "--mode", "keep"],
                                 shuffle(mapped))
        self.assertEqual(0, rc2, err2)
        # fixture 里 (1,1,978824268) 有三行内容完全相同 → 去重保留 1 条
        self.assertEqual(kept_records - 2, len(lines_of(out)))
        self.assertEqual(2, counters(err2)[("dedupe", "ratings")])

    def test_quarantine_pass_emits_the_dropped_copies(self):
        mapped, _, _ = run_job("ratings_dedupe.py", [], self._validate_keep())
        out, err, rc = run_job("ratings_dedupe.py", ["--reduce", "--mode", "quarantine"],
                               shuffle(mapped))
        self.assertEqual(0, rc, err)
        rows = [json.loads(l) for l in lines_of(out)]
        self.assertEqual(2, len(rows))
        for r in rows:
            self.assertEqual("R6", r["rule_id"])
            self.assertEqual("ratings.dat", r["source_file"])
        # R6 属去重：不得报隔离计数器（契约 counts.quarantine.by_rule 不含 R6）
        c = counters(err)
        self.assertNotIn(("quarantine", "R6"), c)
        # 两趟分工：quarantine 趟只报隔离命中，dedupe 计数由 keep 趟负责。
        # 因此这里用「隔离趟输出的行数 == keep 趟报的 dedupe 数」交叉验证两趟一致。
        _k, keep_err, _rc = run_job("ratings_dedupe.py", ["--reduce", "--mode", "keep"],
                                    shuffle(mapped))
        self.assertEqual(counters(keep_err)[("dedupe", "ratings")], len(rows))

    def test_r7_is_structurally_zero_after_r6(self):
        """R6 已把每个业务键收敛成一条，R7（同键不同 Rating）因此不可能命中。"""
        mapped, _, _ = run_job("ratings_dedupe.py", [], self._validate_keep())
        out, err, rc = run_job("ratings_dedupe.py", ["--reduce", "--mode", "quarantine"],
                               shuffle(mapped))
        self.assertEqual(0, rc, err)
        self.assertNotIn(("quarantine", "R7"), counters(err))
        rule_ids = set(json.loads(l)["rule_id"] for l in lines_of(out))
        self.assertEqual({"R6"}, rule_ids)

    def test_mapper_rejects_quarantine_mode(self):
        mapped, err, rc = run_job("ratings_dedupe.py", ["--mode", "quarantine"],
                                  self._validate_keep())
        self.assertNotEqual(0, rc)
        self.assertIn("--reduce", err)

    def test_reduce_result_independent_of_value_order(self):
        """组内 value 逆序不得改变结果（plan §5.1 的确定性要求）。"""
        mid = self._validate_keep()
        mapped, _, _ = run_job("ratings_dedupe.py", [], mid)
        a, _, _ = run_job("ratings_dedupe.py", ["--reduce", "--mode", "keep"],
                          shuffle(mapped))
        b, _, _ = run_job("ratings_dedupe.py", ["--reduce", "--mode", "keep"],
                          shuffle_within_keys(mapped))
        key = lambda t: sorted(json.dumps(json.loads(l)["f"], sort_keys=True)
                               for l in lines_of(t))
        self.assertEqual(key(a), key(b))


class TestRatingsCross(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.dims = os.path.join(TESTS_DIR, "_dims")
        if not os.path.isdir(cls.dims):
            os.makedirs(cls.dims)
        # 用维表 keep 链产出与集群同形的内部 JSONL（users_resolve / movies_resolve）
        for table in ("users", "movies"):
            mid = _resolve_input(table, numbered(cls.raw, table))
            out, err, rc = run_job("%s_resolve.py" % table, ["--reduce", "--mode", "keep"],
                                   shuffle(mid))
            assert rc == 0, err
            with io.open(os.path.join(cls.dims, "%s.jsonl" % table), "w",
                         encoding="iso-8859-1", newline="\n") as fh:
                fh.write(out)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dims, ignore_errors=True)

    def _validate_dedupe_keep(self):
        text = numbered(self.raw, "ratings")
        v, err, rc = run_job("ratings_validate.py", ["--mode", "keep"], text)
        assert rc == 0, err
        mapped, err2, rc2 = run_job("ratings_dedupe.py", [], v)
        assert rc2 == 0, err2
        out, err3, rc3 = run_job("ratings_dedupe.py", ["--reduce", "--mode", "keep"],
                                 shuffle(mapped))
        assert rc3 == 0, err3
        return out

    def test_x1_x2_quarantine_orphans(self):
        mid = self._validate_dedupe_keep()
        args = ["--users", os.path.join(self.dims, "users.jsonl"),
                "--movies", os.path.join(self.dims, "movies.jsonl")]
        keep, err, rc = run_job("ratings_cross.py", ["--mode", "keep"] + args, mid)
        self.assertEqual(0, rc, err)
        quar, err2, rc2 = run_job("ratings_cross.py", ["--mode", "quarantine"] + args, mid)
        self.assertEqual(0, rc2, err2)
        rows = [json.loads(l) for l in lines_of(quar)]
        by_rule = {}
        for r in rows:
            by_rule[r["rule_id"]] = by_rule.get(r["rule_id"], 0) + 1
        # fixture：999 号用户与 999 号电影各一条孤儿评分
        self.assertEqual({"X1": 1, "X2": 1}, by_rule)
        self.assertEqual(len(lines_of(mid)), len(lines_of(keep)) + len(lines_of(quar)))

    def test_missing_dim_table_fails_loudly(self):
        """没有维表就必须报错 —— 「不知道」不能当作「引用有效」。"""
        mid = self._validate_dedupe_keep()
        out, err, rc = run_job("ratings_cross.py",
                               ["--mode", "keep",
                                "--users", os.path.join(self.dims, "users.jsonl")], mid)
        self.assertNotEqual(0, rc)
        self.assertIn("movies", err)

    def test_empty_dim_table_fails_loudly(self):
        mid = self._validate_dedupe_keep()
        empty = os.path.join(self.dims, "empty.jsonl")
        with io.open(empty, "w", encoding="iso-8859-1"):
            pass
        out, err, rc = run_job("ratings_cross.py",
                               ["--mode", "keep", "--users", empty,
                                "--movies", os.path.join(self.dims, "movies.jsonl")], mid)
        self.assertNotEqual(0, rc)
        # 诊断信息可能含中文，stderr 用 backslashreplace 兜底会被转义成 \uXXXX；
        # 因此断言 ASCII 部分（表名与文件路径）。
        self.assertIn("ConfigError", err)
        self.assertIn("users", err)
        self.assertIn("empty.jsonl", err)


if __name__ == "__main__":
    unittest.main()
