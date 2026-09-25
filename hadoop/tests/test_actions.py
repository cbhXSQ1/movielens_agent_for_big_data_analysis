# -*- coding: utf-8 -*-
"""M1c 测试：engine/actions.py —— 修复策略、resolve 策略、隔离记录。

TDD：本文件先于实现编写，初始应全部失败。

记录（record）规范形状（pipeline 与 Streaming 作业共同遵守）：:

    {
      "fields":      {字段名: 字符串值},
      "raw_line":    str,   # 原始行，用于确定性排序与隔离
      "line_no":     int,   # 1-based 原始行号
      "source_file": str,
    }

隔离记录字段见 config/cleaning_rules.v1.json 的 quarantine_record.fields：
  source_file / line_no / raw_line / rule_id / stage / reason / task_id / processed_at
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.actions import (apply_fix, make_quarantine_record,  # noqa: E402
                            make_record, resolve_records, valid_score)
from engine.config_loader import ConfigError, load_schemes  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
RULES_PATH = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING_PATH = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")


def rule(rules, rid):
    for r in rules["rules"]:
        if r["id"] == rid:
            return r
    raise KeyError(rid)


class TestMakeQuarantineRecord(unittest.TestCase):
    def test_all_config_fields_present(self):
        rec = make_quarantine_record("ratings.dat", 13,
                                     "5::1722::2::978246108::EXTRA_FIELD",
                                     "P3", "parse", "字段数异常", "T-1")
        self.assertEqual(
            ["source_file", "line_no", "raw_line", "rule_id", "stage", "reason",
             "task_id", "processed_at"],
            list(rec.keys()))
        self.assertEqual("ratings.dat", rec["source_file"])
        self.assertEqual(13, rec["line_no"])
        self.assertEqual("P3", rec["rule_id"])
        self.assertEqual("parse", rec["stage"])
        self.assertEqual("字段数异常", rec["reason"])
        self.assertEqual("T-1", rec["task_id"])

    def test_processed_at_defaults_to_iso8601_utc(self):
        rec = make_quarantine_record("x", 1, "l", "P1", "parse", "r", "T")
        self.assertRegex(rec["processed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_processed_at_can_be_pinned_for_determinism(self):
        """重跑可比对时需要钉住时间戳（黄金测试/哈希对账用）。"""
        rec = make_quarantine_record("x", 1, "l", "P1", "parse", "r", "T",
                                     processed_at="2026-09-24T00:00:00Z")
        self.assertEqual("2026-09-24T00:00:00Z", rec["processed_at"])


class TestFixStrategies(unittest.TestCase):
    """逐个修复策略（cleaning_rules.v1.说明.md §8）。"""

    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES_PATH, SCORING_PATH)

    def _fix(self, rid, values, raw_line=None):
        r = rule(self.s.rules, rid)
        rec = make_record(values, raw_line=raw_line or "::".join(values.values()),
                          line_no=1, source_file="t.dat")
        out, applied = apply_fix(rec, r, self.s.rules)
        return out["fields"], applied

    # ---- strip（M2）----
    def test_strip(self):
        f, applied = self._fix("M2", {"MovieID": "1518", "Title": "  Breakdown (1997)  ",
                                      "Genres": "Action"})
        self.assertEqual("Breakdown (1997)", f["Title"])
        self.assertEqual(["strip"], applied)

    def test_strip_does_not_touch_other_fields(self):
        f, _ = self._fix("M2", {"MovieID": " 1518 ", "Title": " x ", "Genres": " Action "})
        self.assertEqual(" 1518 ", f["MovieID"])
        self.assertEqual(" Action ", f["Genres"])

    # ---- decode_html_entity（P1）----
    def test_decode_html_entity(self):
        f, applied = self._fix("P1", {
            "MovieID": "3845",
            "Title": "And God Created Woman (Et Dieu&#8230;Créa la Femme) (1956)",
            "Genres": "Drama"})
        self.assertNotIn("&#", f["Title"])
        self.assertIn("decode_html_entity", applied)

    def test_decode_html_entity_result_is_iso8859_1_encodable(self):
        """输出必须是 ISO-8859-1 可编码的（C-ENC 约定）。

        '&#8230;' 解成 U+2026 '…' 后无法用 ISO-8859-1 表示（本数据唯一一例），
        故策略须做可编码性归一：'…' → '...'。
        """
        f, _ = self._fix("P1", {
            "MovieID": "3845",
            "Title": "And God Created Woman (Et Dieu&#8230;Créa la Femme) (1956)",
            "Genres": "Drama"})
        f["Title"].encode("iso-8859-1")  # 不抛异常即通过
        self.assertEqual("And God Created Woman (Et Dieu...Créa la Femme) (1956)",
                         f["Title"])

    # ---- decode_double_encoding（P1）----
    def test_decode_double_encoding_mojibake(self):
        f, applied = self._fix("P1", {"MovieID": "332",
                                      "Title": "LÃ©on / AmÃ©lie (1994)",
                                      "Genres": "Horror|Sci-Fi"})
        self.assertEqual("Léon / Amélie (1994)", f["Title"])
        self.assertIn("decode_double_encoding", applied)

    def test_decode_double_encoding_leaves_correct_latin1_alone(self):
        """已正确的 Latin-1 文本不得被二次解码破坏。"""
        for title in ("Le Mépris (1963)", "Seventh Heaven (Le Septième ciel) (1997)",
                      "Misérables, Les (1995)"):
            with self.subTest(title=title):
                f, _ = self._fix("P1", {"MovieID": "1", "Title": title, "Genres": "Drama"})
                self.assertEqual(title, f["Title"])

    def test_decode_double_encoding_result_is_encodable(self):
        f, _ = self._fix("P1", {"MovieID": "332", "Title": "LÃ©on / AmÃ©lie (1994)",
                                "Genres": "Drama"})
        f["Title"].encode("iso-8859-1")

    # ---- timestamp_ms_to_s（R4）----
    def test_timestamp_ms_to_s(self):
        f, applied = self._fix("R4", {"UserID": "20", "MovieID": "1375", "Rating": "3",
                                      "Timestamp": "1009669071000"})
        self.assertEqual("1009669071", f["Timestamp"])
        self.assertEqual(["timestamp_ms_to_s"], applied)

    def test_timestamp_ms_to_s_leaves_seconds_alone(self):
        f, _ = self._fix("R4", {"UserID": "1", "MovieID": "1", "Rating": "3",
                                "Timestamp": "978246108"})
        self.assertEqual("978246108", f["Timestamp"])

    def test_timestamp_ms_to_s_floor_division(self):
        f, _ = self._fix("R4", {"UserID": "1", "MovieID": "1", "Rating": "3",
                                "Timestamp": "1009669115999"})
        self.assertEqual("1009669115", f["Timestamp"])

    # ---- zip_plus4_truncate（U3）----
    def test_zip_plus4_truncate(self):
        f, applied = self._fix("U3", {"UserID": "4746", "Gender": "M", "Age": "50",
                                      "Occupation": "1", "Zip-code": "19087-3622"})
        self.assertEqual("19087", f["Zip-code"])
        self.assertIn("zip_plus4_truncate", applied)

    def test_zip_plus4_truncate_leaves_valid_zip_alone(self):
        f, _ = self._fix("U3", {"UserID": "1", "Gender": "M", "Age": "25",
                                "Occupation": "1", "Zip-code": "05401"})
        self.assertEqual("05401", f["Zip-code"])

    def test_zip_plus4_truncate_preserves_leading_zero(self):
        f, _ = self._fix("U3", {"UserID": "1", "Gender": "M", "Age": "25",
                                "Occupation": "1", "Zip-code": "01904-1355"})
        self.assertEqual("01904", f["Zip-code"])

    # ---- blank_invalid_field（U2 / U3）----
    def test_blank_invalid_field_users_attributes(self):
        """U2：非法属性置空并保留用户（不删记录）。"""
        f, applied = self._fix("U2", {"UserID": "1678", "Gender": "F", "Age": "18",
                                      "Occupation": "99", "Zip-code": "97070"})
        self.assertEqual("", f["Occupation"])
        self.assertEqual("F", f["Gender"])
        self.assertEqual("18", f["Age"])
        self.assertIn("blank_invalid_field", applied)

    def test_blank_invalid_field_multiple_invalid(self):
        f, _ = self._fix("U2", {"UserID": "3633", "Gender": "X", "Age": "30",
                                "Occupation": "18", "Zip-code": "60441"})
        self.assertEqual("", f["Gender"])
        self.assertEqual("", f["Age"])
        self.assertEqual("18", f["Occupation"])

    def test_u3_bad_zip_gets_blanked_after_truncate_fails(self):
        f, applied = self._fix("U3", {"UserID": "418", "Gender": "F", "Age": "25",
                                      "Occupation": "3", "Zip-code": "ABCDE"})
        self.assertEqual("", f["Zip-code"])
        self.assertIn("blank_invalid_field", applied)

    def test_u3_short_zip_gets_blanked(self):
        for z in ("12", "1234", "123456"):
            with self.subTest(zip=z):
                f, _ = self._fix("U3", {"UserID": "1", "Gender": "M", "Age": "25",
                                        "Occupation": "1", "Zip-code": z})
                self.assertEqual("", f["Zip-code"])

    # ---- 通用行为 ----
    def test_record_level_strategies_are_noops_in_apply_fix(self):
        """prefer_valid_record/keep_first/field_level_merge 属 resolve 阶段，
        apply_fix 里不得报错（它们出现在 M4/U5 的 fix.strategies 中）。"""
        r = rule(self.s.rules, "M4")
        rec = make_record({"MovieID": "1", "Title": "A (1990)", "Genres": "Drama"},
                          raw_line="1::A (1990)::Drama", line_no=1, source_file="m.dat")
        out, _ = apply_fix(rec, r, self.s.rules)
        self.assertEqual("A (1990)", out["fields"]["Title"])

    def test_unknown_strategy_raises(self):
        r = {"fix": {"strategies": ["not_a_strategy"]}, "detect": {"field": "Title"}}
        rec = make_record({"Title": "x"}, raw_line="x", line_no=1, source_file="t")
        with self.assertRaises(ConfigError):
            apply_fix(rec, r, self.s.rules)

    def test_apply_fix_does_not_mutate_input(self):
        r = rule(self.s.rules, "M2")
        rec = make_record({"MovieID": "1", "Title": "  x  ", "Genres": "Drama"},
                          raw_line="1::  x  ::Drama", line_no=1, source_file="m.dat")
        apply_fix(rec, r, self.s.rules)
        self.assertEqual("  x  ", rec["fields"]["Title"], "apply_fix 不应修改入参记录")

    def test_apply_fix_returns_rule_stage_metadata_untouched(self):
        r = rule(self.s.rules, "M2")
        rec = make_record({"MovieID": "1", "Title": " x ", "Genres": "Drama"},
                          raw_line="L", line_no=7, source_file="m.dat")
        out, _ = apply_fix(rec, r, self.s.rules)
        self.assertEqual("L", out["raw_line"])
        self.assertEqual(7, out["line_no"])
        self.assertEqual("m.dat", out["source_file"])


# U5 / M4 的字段合法性规格（由 M2 的 pipeline 从配置构造；此处手写等价版本以测 resolve）
USER_SPECS = [
    {"field": "Gender", "weight": 1, "condition": {"op": "not_null", "field": "Gender"}},
    {"field": "Age", "weight": 1, "condition": {"op": "not_null", "field": "Age"}},
    {"field": "Occupation", "weight": 1, "condition": {"op": "not_null", "field": "Occupation"}},
    {"field": "Zip-code", "weight": 1,
     "condition": {"op": "regex_match", "field": "Zip-code", "pattern": "^\\d{5}$"}},
]


def urec(uid, g, a, o, z, line_no=1):
    raw = "::".join([uid, g, a, o, z])
    return make_record({"UserID": uid, "Gender": g, "Age": a, "Occupation": o,
                        "Zip-code": z}, raw_line=raw, line_no=line_no,
                       source_file="users.dat")


#: resolve 时字段合法性规格里的 set_ref 需要 reference_domains 才能求值
#: （与 pipeline 传给 operators.evaluate 的 ctx 同源，取自配置）
CTX = {"reference_domains": {
    "genres": ["Action", "Adventure", "Animation", "Children's", "Comedy", "Crime",
               "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "Musical",
               "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western"],
}}


class TestResolveRecords(unittest.TestCase):

    # ---- 无重复 ----
    def test_single_record_kept(self):
        r = urec("1", "F", "1", "10", "48067")
        kept, dropped = resolve_records([r], {"value": "prefer_valid_then_field_merge"},
                                        USER_SPECS, CTX)
        self.assertEqual("1", kept["fields"]["UserID"])
        self.assertEqual([], dropped)

    # ---- 完全相同的副本按去重处理 ----
    def test_identical_records_dedupe_to_one(self):
        r1 = urec("1", "F", "1", "10", "48067", line_no=1)
        r2 = urec("1", "F", "1", "10", "48067", line_no=2)
        kept, dropped = resolve_records([r2, r1], {"value": "prefer_valid_then_field_merge"},
                                        USER_SPECS, CTX)
        self.assertEqual(1, len(dropped))
        self.assertEqual("48067", kept["fields"]["Zip-code"])

    # ---- prefer_valid_then_field_merge ----
    def test_unique_winner_kept(self):
        """一条属性非法（已被置空）→ 另一条得分更高，唯一胜出。"""
        good = urec("418", "F", "25", "3", "54016", line_no=2)
        bad = urec("418", "F", "25", "3", "", line_no=1)
        kept, dropped = resolve_records([bad, good],
                                        {"value": "prefer_valid_then_field_merge"},
                                        USER_SPECS, CTX)
        self.assertEqual("54016", kept["fields"]["Zip-code"])
        self.assertEqual(1, len(dropped))

    def test_tie_triggers_field_level_merge(self):
        """并列 → 字段级合并：一致的字段保留，冲突的字段置空。

        两个邮编都合法（各 4 分，真并列）但取值不同 → 合并后该字段置空，
        其余一致的字段保留。
        """
        a = urec("3165", "M", "35", "7", "11111", line_no=1)
        b = urec("3165", "M", "35", "7", "60611", line_no=2)
        kept, dropped = resolve_records([a, b],
                                        {"value": "prefer_valid_then_field_merge"},
                                        USER_SPECS, CTX)
        self.assertEqual("M", kept["fields"]["Gender"])
        self.assertEqual("35", kept["fields"]["Age"])
        self.assertEqual("7", kept["fields"]["Occupation"])
        self.assertEqual("", kept["fields"]["Zip-code"], "冲突字段应置空")
        self.assertEqual(1, len(dropped))

    def test_invalid_zip_loses_to_valid_zip_without_merge(self):
        """得分不同就不是并列：合法邮编那条唯一胜出（不触发合并）。"""
        bad = urec("3165", "M", "35", "7", "12", line_no=1)      # 邮编非法 → 3 分
        good = urec("3165", "M", "35", "7", "60611", line_no=2)  # 邮编合法 → 4 分
        kept, dropped = resolve_records([bad, good],
                                        {"value": "prefer_valid_then_field_merge"},
                                        USER_SPECS, CTX)
        self.assertEqual("60611", kept["fields"]["Zip-code"])
        self.assertEqual(1, len(dropped))

    def test_tie_merge_with_differing_gender(self):
        a = urec("2314", "F", "25", "1", "13210", line_no=1)
        b = urec("2314", "M", "56", "1", "13210", line_no=2)
        kept, _ = resolve_records([a, b], {"value": "prefer_valid_then_field_merge"},
                                  USER_SPECS, CTX)
        self.assertEqual("", kept["fields"]["Gender"])
        self.assertEqual("", kept["fields"]["Age"])
        self.assertEqual("1", kept["fields"]["Occupation"])
        self.assertEqual("13210", kept["fields"]["Zip-code"])

    # ---- prefer_valid（M4，电影）----
    MOVIE_SPECS = [
        {"field": "Title", "weight": 2, "condition": {"op": "not_null", "field": "Title"}},
        {"field": "Title", "weight": 1,
         "condition": {"op": "not", "args": [{"op": "contains_any", "field": "Title",
                                              "values": ["[20XX]"]}]}},
        {"field": "Title", "weight": 1,
         "condition": {"op": "not", "args": [{"op": "contains_any", "field": "Title",
                                              "values": ["Ã", "Â", "â€"]}]}},
        {"field": "Genres", "weight": 1,
         "condition": {"op": "token_in_set", "field": "Genres", "sep": "|",
                       "set_ref": "genres"}},
    ]

    def _mrec(self, mid, title, genres, line_no=1):
        raw = "::".join([mid, title, genres])
        return make_record({"MovieID": mid, "Title": title, "Genres": genres},
                           raw_line=raw, line_no=line_no, source_file="movies.dat")

    def test_prefer_valid_picks_higher_score(self):
        good = self._mrec("1805", "Wild Things (1998)", "Crime|Drama|Mystery|Thriller", 1)
        bad = self._mrec("1805", "Movie with malformed year [20XX]",
                         "Crime|Drama|Mystery|Thriller", 2)
        kept, dropped = resolve_records([bad, good], {"value": "prefer_valid"}, self.MOVIE_SPECS, CTX)
        self.assertEqual("Wild Things (1998)", kept["fields"]["Title"])
        self.assertEqual(1, len(dropped))

    def test_prefer_valid_tie_picks_lexicographically_smallest_raw_line(self):
        """真正的并列必须确定性：取原始行字典序最小者（plan.md §5.1 确定性要求）。

        构造真实并列（各 4 分，满分 5）：
          A: Title 'AAA (1990)' 非空(+2) 无[20XX](+1) 无乱码(+1) 类型非法(+0) = 4
          C: Title 'BBB [20XX]' 非空(+2) 有[20XX](+0) 无乱码(+1) 类型合法(+1) = 4
        raw_line 上 '1::AAA...' < '1::BBB...'，故应恒保留 A，与入参顺序无关。
        """
        a = self._mrec("1", "AAA (1990)", "UnknownGenre", 1)
        c = self._mrec("1", "BBB [20XX]", "Drama", 2)
        self.assertEqual(4, valid_score(a, self.MOVIE_SPECS, CTX))
        self.assertEqual(4, valid_score(c, self.MOVIE_SPECS, CTX))
        for order in ([a, c], [c, a]):
            with self.subTest(order=[x["line_no"] for x in order]):
                kept, dropped = resolve_records(order, {"value": "prefer_valid"},
                                                self.MOVIE_SPECS, CTX)
                self.assertEqual("AAA (1990)", kept["fields"]["Title"])
                self.assertEqual(1, len(dropped))

    def test_prefer_valid_higher_score_beats_smaller_raw_line(self):
        """得分优先于字典序：高分行即使 raw_line 更大也必须胜出。"""
        low = self._mrec("1", "AAA [20XX]", "UnknownGenre", 1)   # 2+0+1+0 = 3
        high = self._mrec("1", "ZZZ (1990)", "Drama", 2)         # 2+1+1+1 = 5
        kept, _ = resolve_records([low, high], {"value": "prefer_valid"},
                                  self.MOVIE_SPECS, CTX)
        self.assertEqual("ZZZ (1990)", kept["fields"]["Title"])

    # ---- quarantine_all ----
    def test_quarantine_all_drops_everything(self):
        a = urec("1", "F", "1", "10", "48067", line_no=1)
        b = urec("1", "M", "56", "10", "48067", line_no=2)
        kept, dropped = resolve_records([a, b], {"value": "quarantine_all"}, USER_SPECS, CTX)
        self.assertIsNone(kept)
        self.assertEqual(2, len(dropped))

    # ---- keep_first ----
    def test_keep_first_uses_lexicographically_smallest_raw_line(self):
        a = urec("1", "F", "1", "10", "99999", line_no=5)
        b = urec("1", "F", "1", "10", "11111", line_no=1)
        kept, dropped = resolve_records([a, b], {"value": "keep_first"}, USER_SPECS, CTX)
        self.assertEqual("11111", kept["fields"]["Zip-code"])
        self.assertEqual(1, len(dropped))

    # ---- 确定性 ----
    def test_result_is_independent_of_input_order(self):
        recs = [
            urec("9", "M", "25", "1", "11111", line_no=3),
            urec("9", "M", "25", "1", "22222", line_no=1),
            urec("9", "M", "25", "1", "33333", line_no=2),
        ]
        import itertools
        outs = set()
        for perm in itertools.permutations(recs):
            kept, dropped = resolve_records(list(perm),
                                            {"value": "prefer_valid_then_field_merge"},
                                            USER_SPECS, CTX)
            outs.add((kept["fields"]["Zip-code"], len(dropped)))
        self.assertEqual(1, len(outs), "重跑/乱序必须得到同一结果，实际 %s" % outs)

    def test_unknown_policy_value_raises(self):
        a = urec("1", "F", "1", "10", "48067")
        with self.assertRaises(ConfigError):
            resolve_records([a], {"value": "no_such_policy"}, USER_SPECS, CTX)

    def test_dropped_records_carry_reason(self):
        a = urec("1", "F", "1", "10", "54016", line_no=1)
        b = urec("1", "F", "1", "10", "", line_no=2)
        _, dropped = resolve_records([a, b], {"value": "prefer_valid_then_field_merge"},
                                     USER_SPECS, CTX)
        self.assertEqual(1, len(dropped))
        self.assertIn("reason", dropped[0])
        self.assertIn("raw_line", dropped[0])


if __name__ == "__main__":
    unittest.main()
