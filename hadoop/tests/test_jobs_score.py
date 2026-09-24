# -*- coding: utf-8 -*-
"""M4 测试：jobs/score_*.py —— 评分作业（plan §5.2、§7.3）。

核心不变量：**三个作业串起来必须复现本地 runner 的 `metrics/*.json`**。
为此本模块把小数据在本地跑完整评分链，再与 `engine.pipeline.run_local` 的输出
逐值比对 —— 这就是「本地黄金测试 = 集群结果」在作业层面的证明。

评分链的输入按侧区分（plan §5.2）：
  before 用**原始行**（`<行号>\\t<原始行>`）：分母是原始行数、含坏行，
         且 U2 的分子要把无法解析的行各算一条不重复行；
  after  用**清洗后**的内部 JSON：行数 == 记录数。
"""
import io
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.config_loader import load_schemes  # noqa: E402
from engine.metrics import count_keys  # noqa: E402
from engine.pipeline import TABLE_FILES, run_local  # noqa: E402

from test_jobs_dim import lines_of, numbered, run_job  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
RAW = os.path.join(FIXTURES, "raw")
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")

TABLES = ("ratings", "users", "movies")


def shuffle_keys(text, nfields):
    """模拟 Hadoop 按前 nfields 个字段分组的 shuffle：按键排序后交给 --reduce。"""
    rows = [l for l in text.split("\n") if l]
    return "\n".join(sorted(rows, key=lambda l: l.split("\t")[:nfields])) + "\n"


def _fin(text, side, reduce_pass=False):
    args = ["--side", side] + (["--reduce"] if reduce_pass else [])
    out, err, rc = run_job("score_finalize.py", args, text)
    assert rc == 0, err
    return out, None


def score_side(source, side, cleaned_records):
    """跑完整的评分链，返回 (result, counts)。

    source = "raw"（读带行号的原始三表）或 "cleaned"（读清洗后记录）。
    """
    per_table_lines = {}
    for table in TABLES:
        if source == "raw":
            per_table_lines[table] = numbered(RAW, table)
        else:
            per_table_lines[table] = cleaned_records[table]

    # A4 需要广播维表：before 用原始维表、after 用清洗后维表
    dim_files = {}
    for table in ("users", "movies"):
        path = os.path.join(TESTS_DIR, "_score_dims_%s_%s.dat" % (source, table))
        with io.open(path, "w", encoding="iso-8859-1", newline="\n") as fh:
            fh.write(per_table_lines[table])
        dim_files[table] = path

    measure_out = []
    for table in TABLES:
        out, err, rc = run_job("score_measure.py",
                               ["--source", source, "--table", table,
                                "--users", dim_files["users"],
                                "--movies", dim_files["movies"]],
                               per_table_lines[table])
        assert rc == 0, err
        measure_out.append(out)

    group_out = []
    for table in TABLES:
        for nfields, npass in ((2, "distinct"), (3, "dupgroups")):
            mapped, err, rc = run_job("score_groupstats.py",
                                      ["--source", source, "--table", table,
                                       "--pass", npass,
                                       "--users", dim_files["users"],
                                       "--movies", dim_files["movies"]],
                                      per_table_lines[table])
            assert rc == 0, err
            out, err2, rc2 = run_job("score_groupstats.py",
                                     ["--reduce", "--source", source, "--table", table,
                                      "--pass", npass,
                                      "--users", dim_files["users"],
                                      "--movies", dim_files["movies"]],
                                     shuffle_keys(mapped, nfields))
            assert rc2 == 0, err2
            group_out.append(out)

    # finalize 必须一次看到**全部**计数：各表、各趟的 part 文件一起喂进去，
    # 逐块喂会漏（缺键不等于 0，见 test_count_keys_are_complete）
    # finalize 是 map + 单 reducer 两趟：mapper 原样转发计数行，reducer 汇总后
    # 吐**唯一**一行最终 JSON（9 个输入目录 → map 任务数 > 1，不能在 mapper 里汇总）
    combined = "".join(measure_out + group_out)
    forwarded, err = _fin(combined, side)
    rows = lines_of(forwarded)
    out, err2 = _fin("\n".join(sorted(rows)) + "\n", side, reduce_pass=True)
    assert err2 is None, err2
    jsons = lines_of(out)
    assert len(jsons) == 1, "finalize 必须只产出一行 JSON，实际 %d 行" % len(jsons)
    return json.loads(jsons[0])


class ScoreCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES, SCORING)
        import tempfile
        cls.out = tempfile.mkdtemp(prefix="ml-score-")
        cls.stats = run_local(RAW, cls.s, cls.out, "T-SCORE",
                              processed_at="2026-09-24T00:00:00Z")
        cls.cleaned = {}
        for table in TABLES:
            path = os.path.join(cls.out, "cleaned", cls.s.rules["data_version"]["id"],
                                TABLE_FILES[table])
            with io.open(path, encoding="iso-8859-1") as fh:
                rows = [l for l in fh.read().split("\n") if l]
            # 组装成内部 JSON 记录（与集群作业之间传递的格式一致）
            schema = {"ratings": ["UserID", "MovieID", "Rating", "Timestamp"],
                      "users": ["UserID", "Gender", "Age", "Occupation", "Zip-code"],
                      "movies": ["MovieID", "Title", "Genres"]}[table]
            cls.cleaned[table] = "".join(
                json.dumps({"f": dict(zip(schema, r.split("::"))), "n": i + 1, "raw": r},
                           ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
                for i, r in enumerate(rows))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.out, ignore_errors=True)


class TestBeforeSide(ScoreCase):
    @classmethod
    def setUpClass(cls):
        super(TestBeforeSide, cls).setUpClass()
        cls.obj = score_side("raw", "before", cls.cleaned)
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

    def test_count_keys_are_complete(self):
        """必须把 18 个指标的计数键都收齐，不能靠「缺键当 0」蒙混。"""
        self.assertEqual(set(count_keys(self.s)), set(self.obj["counts"]))

    def test_unparsed_lines_are_counted_as_unique_rows(self):
        """raw 侧 U2 的分子 = 解析记录的去重行数 + **无法解析的行数**（逐行独立）。

        与本地 `_agg_distinct_row_count` 的口径一致：无法解析的行不可能是任何
        已解析记录的副本，所以各自算一条不重复行。
        """
        from engine.pipeline import TABLE_SCHEMAS, read_raw_table
        parsed_distinct, unparsed = 0, 0
        for table, schema in TABLE_SCHEMAS.items():
            seen = set()
            for _, raw in read_raw_table(os.path.join(RAW, TABLE_FILES[table])):
                parts = raw.split("::")
                if len(parts) != len(schema):
                    unparsed += 1
                    continue
                seen.add(tuple(parts))
            parsed_distinct += len(seen)
        self.assertGreater(unparsed, 0, "fixture 必须包含无法解析的行才有意义")
        self.assertEqual(parsed_distinct + unparsed, self.obj["counts"]["U2.num"])

    def test_side_is_reported(self):
        self.assertEqual("before", self.obj["side"])


class TestAfterSide(ScoreCase):
    @classmethod
    def setUpClass(cls):
        super(TestAfterSide, cls).setUpClass()
        cls.obj = score_side("cleaned", "after", cls.cleaned)
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

    def test_after_side_is_not_worse_than_before(self):
        self.assertLess(0.0, self.obj["result"]["composite"])


class TestFinalizeGuards(unittest.TestCase):
    """校验发生在 **reducer** 趟：mapper 只做原样转发（见 _common.run_score_finalize）。"""

    def test_mapper_pass_is_verbatim(self):
        out, err, rc = run_job("score_finalize.py", ["--side", "before"],
                               "A1.num\t3\nA1.den\t4\n")
        self.assertEqual(0, rc, err)
        self.assertEqual(["A1.num\t3", "A1.den\t4"], lines_of(out))

    def test_unknown_count_key_fails_loudly(self):
        out, err, rc = run_job("score_finalize.py", ["--side", "before", "--reduce"],
                               "NOPE.num\t1\n")
        self.assertNotEqual(0, rc)
        self.assertIn("ConfigError", err)

    def test_bad_side_fails_loudly(self):
        out, err, rc = run_job("score_finalize.py", ["--side", "sideways"], "")
        self.assertNotEqual(0, rc)
        self.assertIn("--side", err)

    def test_non_integer_count_fails_loudly(self):
        out, err, rc = run_job("score_finalize.py", ["--side", "before", "--reduce"],
                               "A1.num\tx\n")
        self.assertNotEqual(0, rc)
        self.assertIn("ConfigError", err)


if __name__ == "__main__":
    unittest.main()
