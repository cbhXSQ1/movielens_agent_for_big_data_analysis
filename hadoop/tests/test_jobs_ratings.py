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

from test_jobs_dim import (counters, k_lines, kinds, lines_of,  # noqa: E402
                           numbered, q_lines, run_job, shuffle,
                           shuffle_within_keys, _merge)


class TestRatingsValidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.text = numbered(cls.raw, "ratings")

    def test_single_pass_complementary_and_quarantine_by_rule(self):
        out, err, rc = run_job("ratings_validate.py", [], self.text)
        self.assertEqual(0, rc, err)
        k, q = k_lines(out), q_lines(out)
        self.assertEqual(len(lines_of(self.text)), len(k) + len(q))
        c = counters(err)
        # fixture：R2 5 条（0/6/3.5/five/空）、R1 2 条、R3 1 条（'five'）、
        # R5 2 条（-1 / 2100 年）、P2 1 条、P3 1 条
        self.assertEqual(5, c[("quarantine", "R2")])
        self.assertEqual(2, c[("quarantine", "R1")])
        self.assertEqual(1, c[("quarantine", "R3")])
        self.assertEqual(2, c[("quarantine", "R5")])
        self.assertEqual(1, c[("quarantine", "P2")])
        self.assertEqual(1, c[("quarantine", "P3")])
        q_raws = [json.loads(x)["raw_line"] for x in q]
        self.assertNotIn("1::1::4::1009669071000", q_raws,
                         "毫秒那条已被 R4 修好，不该被 R5 隔离")

    def test_r4_fix_counted_and_ms_becomes_seconds(self):
        out, err, rc = run_job("ratings_validate.py", [], self.text)
        self.assertEqual(0, rc, err)
        self.assertEqual(1, counters(err)[("fix", "R4_ms")])
        ts = [json.loads(x)["f"]["Timestamp"] for x in k_lines(out)]
        self.assertIn("1009669071", ts, "毫秒时间戳应被 R4 修成秒")
        self.assertNotIn("1009669071000", ts)


class TestRatingsDedupe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.text = numbered(cls.raw, "ratings")

    def _validate_pass(self):
        out, err, rc = run_job("ratings_validate.py", [], self.text)
        assert rc == 0, err
        return out

    def test_dedupe_removes_extra_copies(self):
        """单趟 dedupe：K 去重（保留 1 条）、副本走 D 流。"""
        mid = self._validate_pass()
        kept_records = len(k_lines(mid))
        mapped, err, rc = run_job("ratings_dedupe.py", [], mid)
        self.assertEqual(0, rc, err)
        out, err2, rc2 = run_job("ratings_dedupe.py", ["--reduce"], shuffle(mapped))
        self.assertEqual(0, rc2, err2)
        # fixture 里 (1,1,978824268) 有三行内容完全相同 → 去重保留 1 条
        self.assertEqual(kept_records - 2, len(k_lines(out)))
        self.assertEqual(2, len(kinds(out)["D"]))
        self.assertEqual(2, counters(err2)[("dedupe", "ratings")])
        for payload in kinds(out)["D"]:
            r = json.loads(payload)
            self.assertEqual("R6", r["rule_id"])
            self.assertEqual("ratings.dat", r["source_file"])
        # R6 属去重：不得报隔离计数器（契约 counts.quarantine.by_rule 不含 R6）
        self.assertNotIn(("quarantine", "R6"), counters(err2))

    def test_r7_is_structurally_zero_after_r6(self):
        """R6 已把每个业务键收敛成一条，R7（同键不同 Rating）因此不可能命中。"""
        mapped, _, _ = run_job("ratings_dedupe.py", [], self._validate_pass())
        out, err, rc = run_job("ratings_dedupe.py", ["--reduce"], shuffle(mapped))
        self.assertEqual(0, rc, err)
        self.assertNotIn(("quarantine", "R7"), counters(err))
        self.assertEqual({"R6"}, set(json.loads(x)["rule_id"]
                                     for x in kinds(out)["D"]))

    def test_reduce_result_independent_of_value_order(self):
        """组内 value 逆序不得改变结果（plan §5.1 的确定性要求）。"""
        mid = self._validate_pass()
        mapped, _, _ = run_job("ratings_dedupe.py", [], mid)
        a, _, _ = run_job("ratings_dedupe.py", ["--reduce"], shuffle(mapped))
        b, _, _ = run_job("ratings_dedupe.py", ["--reduce"], shuffle_within_keys(mapped))
        key = lambda t: sorted(json.dumps(json.loads(l)["f"], sort_keys=True)
                               for l in k_lines(t))
        self.assertEqual(key(a), key(b))


class TestRatingsCross(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = os.path.join(FIXTURES, "raw")
        cls.dims = os.path.join(TESTS_DIR, "_dims")
        if not os.path.isdir(cls.dims):
            os.makedirs(cls.dims)
        # 用维表单趟链产出与集群同形的**带标签**内部 JSONL（load_dim_keys 只认 K 流）
        for table in ("users", "movies"):
            text = numbered(cls.raw, table)
            out, err, rc = run_job("%s_normalize.py" % table, [], text)
            assert rc == 0, err
            mapped, err, rc = run_job("%s_resolve.py" % table, [], out)
            assert rc == 0, err
            out, err, rc = run_job("%s_resolve.py" % table, ["--reduce"], shuffle(mapped))
            assert rc == 0, err
            with io.open(os.path.join(cls.dims, "%s.jsonl" % table), "w",
                         encoding="iso-8859-1", newline="\n") as fh:
                fh.write(out)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dims, ignore_errors=True)

    def _validate_dedupe(self):
        text = numbered(self.raw, "ratings")
        v, err, rc = run_job("ratings_validate.py", [], text)
        assert rc == 0, err
        mapped, err2, rc2 = run_job("ratings_dedupe.py", [], v)
        assert rc2 == 0, err2
        out, err3, rc3 = run_job("ratings_dedupe.py", ["--reduce"], shuffle(mapped))
        assert rc3 == 0, err3
        return out

    def test_x1_x2_quarantine_orphans(self):
        mid = self._validate_dedupe()
        args = ["--users", os.path.join(self.dims, "users.jsonl"),
                "--movies", os.path.join(self.dims, "movies.jsonl")]
        keep, err, rc = run_job("ratings_cross.py", args, mid)
        self.assertEqual(0, rc, err)
        rows = [json.loads(x) for x in q_lines(keep)]
        by_rule = {}
        for r in rows:
            by_rule[r["rule_id"]] = by_rule.get(r["rule_id"], 0) + 1
        # 单趟设计的 cross 输出携带整条评分链的 Q 流（前面阶段的隔离被原样转发）
        # + 本阶段的 X1/X2：fixture 共 14 条隔离
        self.assertEqual({"P2": 1, "P3": 1, "R1": 2, "R2": 5, "R3": 1,
                          "R5": 2, "X1": 1, "X2": 1}, by_rule)
        self.assertEqual(14, len(rows))
        # 总量守恒：输入 = K 11 + Q 12 + D 2 = 25 行；输出 = K 9 + Q 14 + D 2 = 25 行
        self.assertEqual(len(lines_of(mid)),
                         len(k_lines(keep)) + len(q_lines(keep)) + len(kinds(keep)["D"]))

    def test_missing_dim_table_fails_loudly(self):
        """没有维表就必须报错 —— 「不知道」不能当作「引用有效」。"""
        mid = self._validate_dedupe()
        out, err, rc = run_job("ratings_cross.py",
                               ["--users", os.path.join(self.dims, "users.jsonl")], mid)
        self.assertNotEqual(0, rc)
        self.assertIn("movies", err)

    def test_empty_dim_table_fails_loudly(self):
        mid = self._validate_dedupe()
        empty = os.path.join(self.dims, "empty.jsonl")
        with io.open(empty, "w", encoding="iso-8859-1"):
            pass
        out, err, rc = run_job("ratings_cross.py",
                               ["--users", empty,
                                "--movies", os.path.join(self.dims, "movies.jsonl")], mid)
        self.assertNotEqual(0, rc)
        # 诊断信息可能含中文，stderr 用 backslashreplace 兜底会被转义成 \uXXXX；
        # 因此断言 ASCII 部分（表名与文件路径）。
        self.assertIn("ConfigError", err)
        self.assertIn("users", err)
        self.assertIn("empty.jsonl", err)


if __name__ == "__main__":
    unittest.main()
