# -*- coding: utf-8 -*-
"""M2 测试：engine/pipeline.py —— fixture 集成测试（plan.md §7.2）。

用 tests/fixtures/ 的小数据跑完整流程，断言 cleaned 三表、counts、标记数与落盘结构。
期望文件由 build_fixtures.py 手写生成，不由本测试或实现反推。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config_loader import load_schemes  # noqa: E402
from engine.pipeline import (TABLE_SCHEMAS, parse_record,  # noqa: E402
                            read_raw_table, run_local)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")

PINNED = "2026-09-24T00:00:00Z"


def read_iso(path):
    with io.open(path, "r", encoding="iso-8859-1", newline="") as fh:
        return fh.read()


def load_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_jsonl(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


class TestReadAndParse(unittest.TestCase):
    def test_empty_lines_skipped_but_line_numbers_preserved(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.dat")
            with io.open(p, "w", encoding="iso-8859-1", newline="\n") as fh:
                fh.write(u"a::1\n\nb::2\n\n")
            self.assertEqual([(1, "a::1"), (3, "b::2")], read_raw_table(p))
        finally:
            shutil.rmtree(d)

    def test_crlf_tolerated(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.dat")
            with io.open(p, "wb") as fh:
                fh.write(b"a::1\r\nb::2\r\n")
            self.assertEqual([(1, "a::1"), (2, "b::2")], read_raw_table(p))
        finally:
            shutil.rmtree(d)

    def test_parse_record_returns_none_on_field_count_mismatch(self):
        self.assertIsNone(parse_record("1::2", "movies"))
        self.assertEqual({"MovieID": "1", "Title": "T", "Genres": "Drama"},
                         parse_record("1::T::Drama", "movies"))

    def test_missing_file_raises(self):
        with self.assertRaises(IOError):
            read_raw_table(os.path.join(FIXTURES, "nope.dat"))


class PipelineFixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES, SCORING)
        cls.out = tempfile.mkdtemp(prefix="ml-fixture-out-")
        cls.stats = run_local(os.path.join(FIXTURES, "raw"), cls.s, cls.out,
                              "T-FIXTURE", processed_at=PINNED)
        cls.expected = load_json(os.path.join(FIXTURES, "expected", "counts.json"))
        cls.version = cls.s.rules["data_version"]["id"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.out, *parts)

    # ---- cleaned 三表 ----

    def test_cleaned_tables_match_hand_written_expectations(self):
        for table in ("users", "movies", "ratings"):
            with self.subTest(table=table):
                got = read_iso(self.path("cleaned", self.version, "%s.dat" % table))
                want = read_iso(os.path.join(FIXTURES, "expected", "cleaned",
                                             "%s.dat" % table))
                self.assertEqual(want, got)

    def test_cleaned_files_are_iso8859_1_with_correct_field_counts(self):
        for table, schema in TABLE_SCHEMAS.items():
            with self.subTest(table=table):
                text = read_iso(self.path("cleaned", self.version, "%s.dat" % table))
                self.assertTrue(text.endswith("\n"))
                for line in text.split("\n"):
                    if line == "":
                        continue
                    self.assertEqual(len(schema), len(line.split("::")))

    def test_cleaned_records_sorted_by_business_key(self):
        text = read_iso(self.path("cleaned", self.version, "ratings.dat"))
        keys = [tuple(int(x) for x in l.split("::")[0:1] + [l.split("::")[1],
                                                            l.split("::")[3]])
                for l in text.split("\n") if l]
        self.assertEqual(sorted(keys), keys)

    # ---- counts ----

    def test_counts_match_expectations(self):
        self.assertEqual(self.expected["counts"], self.stats["counts"])

    def test_marks_match_expectations(self):
        self.assertEqual(self.expected["marks"], self.stats["rule_hits"]["marks"])

    def test_quarantine_by_rule_is_settled(self):
        """by_rule 是结算后计数，且只含 action=quarantine 的规则。"""
        by = self.stats["counts"]["quarantine"]["by_rule"]
        self.assertNotIn("R6", by, "R6 属去重，不进隔离区")
        self.assertNotIn("M4", by)
        self.assertNotIn("U5", by)
        self.assertEqual(sum(by.values()), self.stats["counts"]["quarantine"]["total"])

    # ---- 隔离记录 ----

    def test_quarantine_records_have_all_contract_fields(self):
        fields = self.s.rules["quarantine_record"]["fields"]
        path = self.path("quarantine", self.version, "ratings.dat")
        rows = load_jsonl(path)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(fields, list(r.keys()))
            self.assertEqual("ratings.dat", r["source_file"])
            self.assertEqual("T-FIXTURE", r["task_id"])
            self.assertEqual(PINNED, r["processed_at"])
            self.assertIsInstance(r["line_no"], int)

    def test_quarantine_line_numbers_point_at_the_original_line(self):
        path = self.path("quarantine", self.version, "ratings.dat")
        raw = [l for _, l in read_raw_table(
            os.path.join(FIXTURES, "raw", "ratings.dat"))]
        for rec in load_jsonl(path):
            self.assertEqual(raw[rec["line_no"] - 1], rec["raw_line"])

    def test_every_quarantine_row_is_attributable_to_one_rule(self):
        d = self.path("quarantine", self.version)
        for table in TABLE_SCHEMAS:
            with self.subTest(table=table):
                rows = load_jsonl(os.path.join(d, "%s.dat" % table))
                self.assertEqual(len(rows), len(set(r["line_no"] for r in rows)),
                                 "同一行不应被两条隔离规则重复计入")

    # ---- 落盘结构 ----

    def test_output_layout(self):
        for rel in ("cleaned/%s/ratings.dat" % self.version,
                    "quarantine/%s/ratings.dat" % self.version,
                    "quarantine/%s/quarantine_summary.json" % self.version,
                    "metrics/before.json", "metrics/after.json",
                    "metrics/composite.json", "stats.json", "metadata.json"):
            with self.subTest(rel=rel):
                self.assertTrue(os.path.isfile(self.path(*rel.split("/"))), rel)

    def test_metadata_carries_version_and_boundaries(self):
        meta = load_json(self.path("metadata.json"))
        self.assertEqual("T-FIXTURE", meta["task_id"])
        self.assertEqual(self.s.rule_version, meta["rule_version"])
        self.assertEqual(self.s.rule_hash, meta["rule_hash"])
        self.assertEqual(self.s.policy_version, meta["policy_version"])
        self.assertEqual(self.s.t1, meta["T1"])
        self.assertEqual(self.s.t2, meta["T2"])
        self.assertEqual(self.stats["counts"]["input"], meta["input_counts"])
        self.assertEqual(self.stats["counts"]["output"], meta["output_counts"])

    # ---- 评分 ----

    def test_after_scores_reach_full_on_repairable_dimensions(self):
        after = load_json(self.path("metrics", "after.json"))
        m = after["metrics"]
        self.assertEqual(100.0, m["S2"], "乱码与 HTML 实体都应清干净")
        self.assertEqual(100.0, m["S3"], "毫秒时间戳应已统一为秒")
        self.assertEqual(100.0, m["U1"])
        self.assertEqual(100.0, m["A1"])

    def test_before_worse_than_after_on_injected_problems(self):
        before = load_json(self.path("metrics", "before.json"))
        after = load_json(self.path("metrics", "after.json"))
        self.assertLess(before["metrics"]["S2"], after["metrics"]["S2"])
        self.assertLess(before["metrics"]["C3"], after["metrics"]["C3"])
        self.assertLess(before["composite"], after["composite"])

    def test_composite_json_has_delta(self):
        c = load_json(self.path("metrics", "composite.json"))
        self.assertEqual({"before", "after", "delta"}, set(c))
        self.assertAlmostEqual(c["after"]["composite"] - c["before"]["composite"],
                               c["delta"]["composite"], places=2)

    # ---- 确定性与幂等 ----

    def test_rerun_is_byte_identical(self):
        out2 = tempfile.mkdtemp(prefix="ml-fixture-out2-")
        try:
            run_local(os.path.join(FIXTURES, "raw"), self.s, out2, "T-FIXTURE",
                      processed_at=PINNED)
            for table in TABLE_SCHEMAS:
                with self.subTest(table=table):
                    a = read_iso(self.path("cleaned", self.version, "%s.dat" % table))
                    b = read_iso(os.path.join(out2, "cleaned", self.version,
                                              "%s.dat" % table))
                    self.assertEqual(a, b)
            a = load_json(self.path("stats.json"))
            b = load_json(os.path.join(out2, "stats.json"))
            self.assertEqual(a["counts"], b["counts"])
            self.assertEqual(a["scores"], b["scores"])
        finally:
            shutil.rmtree(out2, ignore_errors=True)


class TestEmptyInput(unittest.TestCase):
    """"干净的空输入" 不应崩溃（分母为 0 时按「无适用记录」记满分）。"""

    def test_empty_raw_dir(self):
        s = load_schemes(RULES, SCORING)
        d = tempfile.mkdtemp()
        try:
            for name in ("ratings.dat", "users.dat", "movies.dat"):
                with io.open(os.path.join(d, name), "w", encoding="iso-8859-1"):
                    pass
            out = tempfile.mkdtemp()
            try:
                st = run_local(d, s, out, "T-EMPTY", processed_at=PINNED)
                self.assertEqual({"ratings": 0, "users": 0, "movies": 0},
                                 st["counts"]["output"])
                self.assertEqual(0, st["counts"]["quarantine"]["total"])
            finally:
                shutil.rmtree(out, ignore_errors=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
