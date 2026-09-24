# -*- coding: utf-8 -*-
"""M2 黄金测试：全量 ml-1m 本地 runner（plan.md §7.3）。

这里断言的全部数字都来自 plan.md §7.3 与 docs/hadoop/agent-interface.md §4.5，
是**外部给定**的验收基线，不由实现产生。

原始数据目录按以下顺序定位：
  1. 环境变量 ML_RAW_DIR
  2. hadoop/scripts/env.sh 里的默认路径
  3. 常见候选路径
找不到就 skip（例如 CI 上没有数据），但要显式说明原因，绝不静默通过。

全量单次约 1 分钟；本模块跑两遍（第二遍用于幂等性验证）。
"""
import hashlib
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

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
RULES = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")

DATA_CANDIDATES = [
    os.environ.get("ML_RAW_DIR"),
    "/home/ubuntu/movielensdata/raw/ml-1m/ml-1m",
    os.path.expanduser("~/data/raw/ml-1m"),
    os.path.expanduser("~/data/raw/ml-1m/ml-1m"),
]


def load_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def find_raw_dir():
    for d in DATA_CANDIDATES:
        if d and all(os.path.isfile(os.path.join(d, f)) for f in TABLE_FILES.values()):
            return d
    return None


RAW_DIR = find_raw_dir()

# plan.md §7.3 黄金基线
GOLDEN_COUNTS = {"ratings": 1000209, "users": 6040, "movies": 3883}
GOLDEN_INPUT = {"ratings_lines": 1150241, "users_lines": 6946, "movies_lines": 4465}
GOLDEN_BY_RULE = {"P2": 6075, "P3": 7606, "R1": 6752, "R2": 43885, "R3": 3375,
                  "R5": 12003, "X1": 10502, "X2": 10502, "M1": 58, "U1": 72}
GOLDEN_DEDUPE = {"ratings": 49510, "movies": 454, "users": 726}
GOLDEN_FIX = {"P1_text": 48, "R4_ms": 13503, "M2_strip": 47, "U3_zip_plus4": 73}

GOLDEN_METRICS_BEFORE = {
    "A1": 96.14, "A2": 99.70, "A3": 98.20, "A4": 97.56, "A5": 97.36, "A6": 97.63,
    "C1": 99.70, "C2": 97.65, "C3": 98.82, "U1": 90.81, "U2": 92.50, "U3": 89.50,
    "F1": 97.46, "F2": 100.0, "S1": 98.82, "S2": 98.91, "S3": 98.81, "S4": 68.20,
}
GOLDEN_METRICS_AFTER = {
    "A1": 100.0, "A2": 100.0, "A3": 98.60, "A4": 100.0, "A5": 100.0, "A6": 100.0,
    "C1": 99.99, "C2": 99.99, "C3": 100.0, "U1": 100.0, "U2": 100.0, "U3": 100.0,
    "F1": 100.0, "F2": 100.0, "S1": 100.0, "S2": 100.0, "S3": 100.0, "S4": 100.0,
}
GOLDEN_DIMS_BEFORE = {"Accurate": 97.71, "Complete": 98.82, "Unique": 90.90,
                      "Up-to-date": 98.22, "Consistent": 89.65, "composite": 95.07}
GOLDEN_DIMS_AFTER = {"Accurate": 99.79, "Complete": 99.99, "Unique": 100.0,
                     "Up-to-date": 100.0, "Consistent": 100.0, "composite": 99.95}
GOLDEN_DELTA = {"Accurate": 2.08, "Complete": 1.17, "Unique": 9.10,
                "Up-to-date": 1.78, "Consistent": 10.35, "composite": 4.88}


@unittest.skipUnless(RAW_DIR is not None,
                     "未找到 ml-1m 原始数据（设 ML_RAW_DIR 指向含三个 .dat 的目录）")
class GoldenTest(unittest.TestCase):
    """全量跑一次，断言 §7.3 全表。"""

    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES, SCORING)
        cls.version = cls.s.rules["data_version"]["id"]
        cls.out = tempfile.mkdtemp(prefix="ml-golden-")
        cls.stats = run_local(RAW_DIR, cls.s, cls.out, "T-GOLDEN",
                              processed_at="2026-09-24T00:00:00Z")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    # ---- 清洗规模 ----

    def test_cleaned_sizes(self):
        self.assertEqual(GOLDEN_COUNTS, self.stats["counts"]["output"])

    def test_input_line_counts(self):
        self.assertEqual(GOLDEN_INPUT, self.stats["counts"]["input"])

    def test_quarantine_by_rule(self):
        self.assertEqual(GOLDEN_BY_RULE, self.stats["counts"]["quarantine"]["by_rule"])

    def test_quarantine_total(self):
        self.assertEqual(100830, self.stats["counts"]["quarantine"]["total"])

    def test_dedupe_counts(self):
        self.assertEqual(GOLDEN_DEDUPE, self.stats["counts"]["dedupe"])

    def test_fix_counts(self):
        self.assertEqual(GOLDEN_FIX, self.stats["counts"]["fix"])

    # ---- 逐条规则的命中数（§7.3 的明细行）----

    def test_rule_level_targets(self):
        by = self.stats["counts"]["quarantine"]["by_rule"]
        d = self.stats["counts"]["dedupe"]
        f = self.stats["counts"]["fix"]
        self.assertEqual(6752, by["R1"])          # 空 UserID 3,376 + 空 MovieID 3,376
        self.assertEqual(43885, by["R2"])         # 0:8252 / 6:8252 / 3.5:13503 / five:10502 / 空:3376
        self.assertEqual(3375, by["R3"])
        self.assertEqual(12003, by["R5"])
        self.assertEqual(13503, f["R4_ms"])       # R4 修复
        self.assertEqual(49510, d["ratings"])     # R6 去重
        self.assertEqual(10502, by["X1"])
        self.assertEqual(10502, by["X2"])
        self.assertEqual(58, by["M1"])            # M1 移除
        self.assertEqual(454, d["movies"])        # M4 移除
        self.assertEqual(72, by["U1"])            # U1 移除
        self.assertEqual(726, d["users"])         # U5 移除
        self.assertEqual(48, f["P1_text"])        # P1
        self.assertEqual(6075, by["P2"])          # P2
        self.assertEqual(7606, by["P3"])          # P3

    # ---- 五维与综合分 ----

    def test_metrics_before(self):
        got = load_json(os.path.join(self.out, "metrics", "before.json"))["metrics"]
        for k, v in GOLDEN_METRICS_BEFORE.items():
            with self.subTest(metric=k):
                self.assertAlmostEqual(v, got[k], places=2)

    def test_metrics_after(self):
        got = load_json(os.path.join(self.out, "metrics", "after.json"))["metrics"]
        for k, v in GOLDEN_METRICS_AFTER.items():
            with self.subTest(metric=k):
                self.assertAlmostEqual(v, got[k], places=2)

    def test_dimension_scores_and_composite(self):
        scores = self.stats["scores"]
        for side, want in (("before", GOLDEN_DIMS_BEFORE), ("after", GOLDEN_DIMS_AFTER)):
            for k, v in want.items():
                with self.subTest(side=side, dim=k):
                    self.assertAlmostEqual(v, scores[side][k], places=2)

    def test_delta(self):
        for k, v in GOLDEN_DELTA.items():
            with self.subTest(dim=k):
                self.assertAlmostEqual(v, self.stats["scores"]["delta"][k], places=2)

    # ---- 其它约定 ----

    def test_time_split_boundaries(self):
        """T1/T2 切分出的 train/validation/test 规模（配置 data_version 声明）。"""
        import datetime
        t1 = int(datetime.datetime.strptime(self.s.t1, "%Y-%m-%dT%H:%M:%SZ")
                 .replace(tzinfo=datetime.timezone.utc).timestamp())
        t2 = int(datetime.datetime.strptime(self.s.t2, "%Y-%m-%dT%H:%M:%SZ")
                 .replace(tzinfo=datetime.timezone.utc).timestamp())
        path = os.path.join(self.out, "cleaned", self.version, "ratings.dat")
        train = val = test = 0
        with io.open(path, encoding="iso-8859-1") as fh:
            for line in fh:
                if not line.strip():
                    continue
                ts = int(line.split("::")[3])
                if ts <= t1:
                    train += 1
                elif ts <= t2:
                    val += 1
                else:
                    test += 1
        self.assertEqual(972815, train)
        self.assertEqual(14551, val)
        self.assertEqual(12843, test)

    def test_users_with_enough_train_ratings(self):
        """train 区间内评分 >= 20 的用户数（配置声明 6,019/6,040）。"""
        import collections
        import datetime
        t1 = int(datetime.datetime.strptime(self.s.t1, "%Y-%m-%dT%H:%M:%SZ")
                 .replace(tzinfo=datetime.timezone.utc).timestamp())
        path = os.path.join(self.out, "cleaned", self.version, "ratings.dat")
        c = collections.Counter()
        with io.open(path, encoding="iso-8859-1") as fh:
            for line in fh:
                if not line.strip():
                    continue
                parts = line.split("::")
                if int(parts[3]) <= t1:
                    c[parts[0]] += 1
        self.assertEqual(6019, sum(1 for n in c.values() if n >= 20))

    def test_movies_never_rated(self):
        self.assertEqual(177, self.stats["rule_hits"]["groups"]["X3_movies"])
        self.assertEqual(0, self.stats["rule_hits"]["groups"]["X3_users"])

    def test_fallback_checks_are_zero_on_clean_data(self):
        """M3/M6/M7/M9/R7 是兜底规则，清洗后应无有效命中。"""
        groups = self.stats["rule_hits"]["groups"]
        marks = self.stats["rule_hits"]["marks"]
        self.assertEqual(0, groups["R7"])
        self.assertEqual(0, groups["R8"])
        self.assertNotIn("M3", self.stats["counts"]["quarantine"]["by_rule"])
        self.assertNotIn("M6", marks)
        self.assertNotIn("M7", marks)

    def test_cleaned_files_are_reusable(self):
        """cleaned 三表必须每行字段数正确、可按 ISO-8859-1 读回。"""
        expected = {"ratings": 4, "users": 5, "movies": 3}
        for table, n in expected.items():
            with self.subTest(table=table):
                path = os.path.join(self.out, "cleaned", self.version,
                                    TABLE_FILES[table])
                with io.open(path, encoding="iso-8859-1") as fh:
                    rows = 0
                    for line in fh:
                        if line.strip() == "":
                            continue
                        self.assertEqual(n, len(line.rstrip("\n").split("::")))
                        rows += 1
                self.assertEqual(GOLDEN_COUNTS[table], rows)

    def test_idempotent_rerun_has_identical_content_hash(self):
        """同输入 + 同配置重跑：cleaned 三表内容哈希必须一致（接口 §2 幂等要求）。"""
        out2 = tempfile.mkdtemp(prefix="ml-golden2-")
        try:
            run_local(RAW_DIR, self.s, out2, "T-GOLDEN",
                      processed_at="2026-09-24T00:00:00Z")
            for table in ("ratings", "users", "movies"):
                with self.subTest(table=table):
                    h1 = _sha256(os.path.join(self.out, "cleaned", self.version,
                                              TABLE_FILES[table]))
                    h2 = _sha256(os.path.join(out2, "cleaned", self.version,
                                              TABLE_FILES[table]))
                    self.assertEqual(h1, h2)
        finally:
            shutil.rmtree(out2, ignore_errors=True)


def _sha256(path):
    h = hashlib.sha256()
    with io.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    unittest.main()
