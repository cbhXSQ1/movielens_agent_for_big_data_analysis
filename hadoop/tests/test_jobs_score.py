# -*- coding: utf-8 -*-
"""M4 测试：jobs/score_all.py —— 评分作业（D-014 合并版）。plan §5.2、§7.3。

核心不变量：**score_all 的 mapper ×3 表 + 单 reducer 必须复现本地 runner 的
metrics/*.json**。测试把每侧三张表的 mapper 输出拼起来喂给 reducer，
断言只产出一行 JSON 且与 run_local 的 before/after 逐值一致。

before 用原始行（分母含坏行，U2 分子要数「无法解析的行」），after 用清洗后 K 流。
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
from engine.pipeline import TABLE_FILES, run_local  # noqa: E402

from test_jobs_dim import lines_of, numbered, run_job, shuffle  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RAW = os.path.join(FIXTURES, "raw")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")
TABLES = ("ratings", "users", "movies")


def score_side(source, cleaned_records):
    """score_all：三表 mapper → 单 reducer → 一行最终 JSON。"""
    dims = {}
    for table in ("users", "movies"):
        path = os.path.join(TESTS_DIR, "_score_dims_%s.jsonl" % table)
        if source == "raw":
            # before 侧维表 = 原始表（物化行号形态）
            with io.open(path, "w", encoding="iso-8859-1", newline="\n") as fh:
                fh.write(numbered(RAW, table))
        else:
            with io.open(path, "w", encoding="iso-8859-1", newline="\n") as fh:
                fh.write(cleaned_records[table])
        dims[table] = path

    mapper_out = []
    for table in TABLES:
        if source == "raw":
            data = numbered(RAW, table)
        else:
            data = cleaned_records[table]
        out, err, rc = run_job("score_all.py",
                               ["--source", source, "--table", table,
                                "--users", dims["users"],
                                "--movies", dims["movies"]], data)
        assert rc == 0, err
        mapper_out.append(out)

    combined = "".join(mapper_out)
    out, err, rc = run_job("score_all.py", ["--reduce", "--source", source], combined)
    assert rc == 0, err
    jsons = lines_of(out)
    assert len(jsons) == 1, "score_all 必须只产出一行 JSON，实际 %d 行" % len(jsons)
    return json.loads(jsons[0])


class ScoreAllCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES, SCORING)
        cls.out = tempfile.mkdtemp(prefix="ml-score-")
        cls.stats = run_local(RAW, cls.s, cls.out, "T-SCORE",
                              processed_at="2026-09-24T00:00:00Z")
        cls.cleaned = {}
        for table in TABLES:
            path = os.path.join(cls.out, "cleaned",
                                cls.s.rules["data_version"]["id"], TABLE_FILES[table])
            with io.open(path, encoding="iso-8859-1") as fh:
                rows = [l for l in fh.read().split("\n") if l]
            schema = {"ratings": ["UserID", "MovieID", "Rating", "Timestamp"],
                      "users": ["UserID", "Gender", "Age", "Occupation", "Zip-code"],
                      "movies": ["MovieID", "Title", "Genres"]}[table]
            cls.cleaned[table] = "".join(
                "K\t" + json.dumps({"f": dict(zip(schema, r.split("::"))),
                                    "n": i + 1, "raw": r},
                                   ensure_ascii=True, sort_keys=True,
                                   separators=(",", ":")) + "\n"
                for i, r in enumerate(rows))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)
        for t in TABLES:
            for table in ("users", "movies"):
                p = os.path.join(TESTS_DIR, "_score_dims_%s.jsonl" % table)
                if os.path.isfile(p):
                    os.remove(p)


class TestBeforeSide(ScoreAllCase):
    @classmethod
    def setUpClass(cls):
        super(TestBeforeSide, cls).setUpClass()
        cls.obj = score_side("raw", cls.cleaned)
        with io.open(os.path.join(cls.out, "metrics", "before.json"),
                     encoding="utf-8") as fh:
            cls.local = json.load(fh)

    def test_metrics_match_local_runner(self):
        got = self.obj["result"]["metrics"]
        for k, v in self.local["metrics"].items():
            with self.subTest(metric=k):
                self.assertAlmostEqual(v, got[k], places=2)

    def test_dimensions_and_composite_match(self):
        self.assertEqual(self.local["dimensions"], self.obj["result"]["dimensions"])
        self.assertEqual(self.local["composite"], self.obj["result"]["composite"])

    def test_unparsed_lines_are_counted_as_unique_rows(self):
        """raw 侧 U2 的分子必须包含无法解析的行（逐行独立）。"""
        from engine.pipeline import read_raw_table
        parsed_distinct, unparsed = 0, 0
        for table, schema in (("ratings", ["UserID", "MovieID", "Rating", "Timestamp"]),
                              ("users", ["UserID", "Gender", "Age", "Occupation",
                                         "Zip-code"]),
                              ("movies", ["MovieID", "Title", "Genres"])):
            seen = set()
            for _, raw in read_raw_table(os.path.join(RAW, TABLE_FILES[table])):
                parts = raw.split("::")
                if len(parts) != len(schema):
                    unparsed += 1
                    continue
                seen.add(tuple(parts))
            parsed_distinct += len(seen)
        self.assertGreater(unparsed, 0)
        self.assertEqual(parsed_distinct + unparsed, self.obj["counts"]["U2.num"])


class TestAfterSide(ScoreAllCase):
    @classmethod
    def setUpClass(cls):
        super(TestAfterSide, cls).setUpClass()
        cls.obj = score_side("cleaned", cls.cleaned)
        with io.open(os.path.join(cls.out, "metrics", "after.json"),
                     encoding="utf-8") as fh:
            cls.local = json.load(fh)

    def test_metrics_match_local_runner(self):
        got = self.obj["result"]["metrics"]
        for k, v in self.local["metrics"].items():
            with self.subTest(metric=k):
                self.assertAlmostEqual(v, got[k], places=2)

    def test_dimensions_and_composite_match(self):
        self.assertEqual(self.local["dimensions"], self.obj["result"]["dimensions"])
        self.assertEqual(self.local["composite"], self.obj["result"]["composite"])

    def test_scores_improved_after_cleaning(self):
        self.assertGreater(self.obj["result"]["composite"],
                           0.9)


class TestScoreAllGuards(unittest.TestCase):
    def test_unknown_reducer_line_fails_loudly(self):
        out, err, rc = run_job("score_all.py", ["--reduce", "--source", "raw"],
                               "Z\tbogus\n")
        self.assertNotEqual(0, rc)
        self.assertIn("ConfigError", err)

    def test_bad_source_fails_loudly(self):
        out, err, rc = run_job("score_all.py", ["--source", "sideways",
                                                "--table", "users"], "")
        self.assertNotEqual(0, rc)
        self.assertIn("--source", err)

    def test_mapper_requires_table_on_cluster_path(self):
        out, err, rc = run_job("score_all.py", ["--source", "raw"], "")
        self.assertNotEqual(0, rc)
        self.assertIn("--table", err)


if __name__ == "__main__":
    unittest.main()
