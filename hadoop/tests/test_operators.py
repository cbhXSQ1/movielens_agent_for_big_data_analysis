# -*- coding: utf-8 -*-
"""M1b 测试：engine/operators.py —— 31 个检测算子 + evaluate()。

TDD：本文件先于实现编写，初始应全部失败。

覆盖 plan.md §7.1 要求的边界：
  空值、'3.5'、'five'、毫秒阈值 100000000000、'Ã©'、ZIP+4、'[20XX]'
以及 cleaning_rules.v1.说明.md §7 每个算子的语义。

ctx 契约（作业侧与 pipeline 共同遵守）：
  reference_domains   dict   配置里的值域定义（set_ref 解析用）
  raw_line            str    原始行（field_count_ne / foreign_delimiter 用）
  table               str    表名（field_count_ne 的 expected 按表映射时用）
  dup_group_keys      set    本表中「存在重复」的业务键元组（duplicate_by）
  conflict_group_keys set    本表中「同键 value 冲突」的业务键元组（conflict_by）
  value_collision_values set 本表中「同一 value 对应 ≥min_keys 个 key」的 value（value_collision）
  dim_keys            dict   {dimension: 该维表 key_field 的取值集合}（ref_missing / ref_exists）
  ref_unused_keys     dict   {dimension: 从未被引用的 key 集合}（ref_unused）
  group_match_counts  dict   {组值: 满足 where 的条数}（group_anomaly）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import operators  # noqa: E402
from engine.config_loader import ConfigError  # noqa: E402
from engine.operators import OPERATORS, evaluate  # noqa: E402

RD = {
    "gender": ["M", "F"],
    "age": ["1", "18", "25", "35", "45", "50", "56"],
    "occupation": [str(i) for i in range(21)],
    "genres": ["Action", "Adventure", "Animation", "Children's", "Comedy", "Crime",
               "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "Musical",
               "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western"],
    "zip": {"pattern": "^\\d{5}$"},
    "timestamp": {"unit": "unix_seconds", "min": 956703932, "max": 1046476799},
    "title_year": {"pattern": "\\((\\d{4})\\)\\s*$", "min": 1900, "max": 2003},
}


def ctx(**kw):
    """构造一份最小可用 ctx（未指定的聚合上下文留空）。"""
    base = {
        "reference_domains": RD,
        "raw_line": "",
        "table": "ratings",
        "dup_group_keys": set(),
        "conflict_group_keys": set(),
        "value_collision_values": set(),
        "dim_keys": {"users": set(), "movies": set()},
        "ref_unused_keys": {"users": set(), "movies": set()},
        "group_match_counts": {},
    }
    base.update(kw)
    return base


def ev(expr, fields, **kw):
    return evaluate(expr, fields, ctx(**kw))


class TestOperatorRegistry(unittest.TestCase):
    """算子库必须是计划要求的 31 个，且与配置校验白名单一致。"""

    def test_operator_count_is_31(self):
        self.assertEqual(31, len(OPERATORS), sorted(OPERATORS))

    def test_registry_matches_config_whitelist(self):
        from engine.config_loader import OPS
        self.assertEqual(set(OPS), set(OPERATORS),
                         "算子库与配置校验白名单必须完全一致（封闭白名单）")

    def test_unknown_operator_raises_config_error(self):
        with self.assertRaises(ConfigError):
            evaluate({"op": "no_such_op", "field": "Rating"}, {"Rating": "3"}, ctx())


class TestLogic(unittest.TestCase):
    def test_and(self):
        e = {"op": "and", "args": [{"op": "is_int", "field": "Rating"},
                                   {"op": "in_range", "field": "Rating", "min": 1, "max": 5}]}
        self.assertTrue(ev(e, {"Rating": "3"}))
        self.assertFalse(ev(e, {"Rating": "9"}))

    def test_and_empty_args_is_true(self):
        self.assertTrue(ev({"op": "and", "args": []}, {}))

    def test_or(self):
        e = {"op": "or", "args": [{"op": "not_int", "field": "UserID"},
                                  {"op": "out_of_range", "field": "UserID", "min": 1}]}
        self.assertTrue(ev(e, {"UserID": ""}))
        self.assertTrue(ev(e, {"UserID": "0"}))
        self.assertFalse(ev(e, {"UserID": "42"}))

    def test_or_empty_args_is_false(self):
        self.assertFalse(ev({"op": "or", "args": []}, {}))

    def test_not(self):
        e = {"op": "not", "args": [{"op": "contains_any", "field": "Title", "values": ["Ã"]}]}
        self.assertTrue(ev(e, {"Title": "Léon"}))
        self.assertFalse(ev(e, {"Title": "LÃ©on"}))

    def test_nested_deep(self):
        e = {"op": "and", "args": [
            {"op": "or", "args": [{"op": "is_null", "field": "Title"},
                                  {"op": "is_null", "field": "Genres"}]},
            {"op": "not", "args": [{"op": "is_int", "field": "MovieID"}]}]}
        self.assertTrue(ev(e, {"Title": "", "Genres": "Drama", "MovieID": "abc"}))
        self.assertFalse(ev(e, {"Title": "", "Genres": "Drama", "MovieID": "5"}))

    def test_constant_true_expression(self):
        """scoring 配置里 denominator 有 `"where": true`，必须按恒真处理。"""
        self.assertTrue(evaluate(True, {"Rating": "3"}, ctx()))
        self.assertFalse(evaluate(False, {"Rating": "3"}, ctx()))


class TestNullOps(unittest.TestCase):
    def test_is_null_empty_string(self):
        self.assertTrue(ev({"op": "is_null", "field": "Title"}, {"Title": ""}))

    def test_is_null_missing_field_treated_as_null(self):
        self.assertTrue(ev({"op": "is_null", "field": "Nope"}, {}))

    def test_is_null_nonempty(self):
        self.assertFalse(ev({"op": "is_null", "field": "Title"}, {"Title": "x"}))

    def test_not_null(self):
        self.assertTrue(ev({"op": "not_null", "field": "Title"}, {"Title": "x"}))
        self.assertFalse(ev({"op": "not_null", "field": "Title"}, {"Title": ""}))


class TestIntOps(unittest.TestCase):
    def test_is_int_plain(self):
        self.assertTrue(ev({"op": "is_int", "field": "Rating"}, {"Rating": "3"}))

    def test_is_int_leading_zeros_ok(self):
        """邮编按字符串处理，但 '007' 本身仍是合法整数（C-ZIP 是处理约定，不是本算子职责）。"""
        self.assertTrue(ev({"op": "is_int", "field": "Zip"}, {"Zip": "007"}))

    def test_is_int_boundary_3_5_false(self):
        self.assertFalse(ev({"op": "is_int", "field": "Rating"}, {"Rating": "3.5"}))

    def test_is_int_boundary_five_false(self):
        self.assertFalse(ev({"op": "is_int", "field": "Rating"}, {"Rating": "five"}))

    def test_is_int_empty_false(self):
        self.assertFalse(ev({"op": "is_int", "field": "Rating"}, {"Rating": ""}))

    def test_is_int_negative_ok(self):
        """'-1' 是整数（R3 通过），由 R5 负责范围拒绝。"""
        self.assertTrue(ev({"op": "is_int", "field": "Timestamp"}, {"Timestamp": "-1"}))

    def test_is_int_rejects_underscore_and_space(self):
        """严格整数语义：'1_0' 与 ' 5' 都不是整数（Python 的 int() 会误收 '1_0'）。"""
        self.assertFalse(ev({"op": "is_int", "field": "X"}, {"X": "1_0"}))
        self.assertFalse(ev({"op": "is_int", "field": "X"}, {"X": " 5"}))

    def test_not_int(self):
        self.assertTrue(ev({"op": "not_int", "field": "Rating"}, {"Rating": "five"}))
        self.assertFalse(ev({"op": "not_int", "field": "Rating"}, {"Rating": "5"}))


class TestRangeOps(unittest.TestCase):
    def test_in_range_inclusive_bounds(self):
        e = {"op": "in_range", "field": "Rating", "min": 1, "max": 5}
        self.assertTrue(ev(e, {"Rating": "1"}))
        self.assertTrue(ev(e, {"Rating": "5"}))
        self.assertFalse(ev(e, {"Rating": "0"}))
        self.assertFalse(ev(e, {"Rating": "6"}))

    def test_in_range_min_only(self):
        e = {"op": "in_range", "field": "UserID", "min": 1}
        self.assertTrue(ev(e, {"UserID": "1"}))
        self.assertFalse(ev(e, {"UserID": "0"}))
        self.assertTrue(ev(e, {"UserID": "999999"}))

    def test_in_range_max_only(self):
        e = {"op": "in_range", "field": "MovieID", "max": 3952}
        self.assertTrue(ev(e, {"MovieID": "3952"}))
        self.assertFalse(ev(e, {"MovieID": "3953"}))

    def test_in_range_non_int_is_false(self):
        """F1 依赖此语义：不可解析的时间戳不算「落在范围内」。"""
        e = {"op": "in_range", "field": "Timestamp", "min": 956703932, "max": 1046476799}
        self.assertFalse(ev(e, {"Timestamp": ""}))
        self.assertFalse(ev(e, {"Timestamp": "five"}))

    def test_in_range_timestamp_milliseconds_out_of_range(self):
        """毫秒值 1009669071000 远大于 max → 不在范围内（由 R4 修复）。"""
        e = {"op": "in_range", "field": "Timestamp", "min": 956703932, "max": 1046476799}
        self.assertFalse(ev(e, {"Timestamp": "1009669071000"}))

    def test_out_of_range(self):
        e = {"op": "out_of_range", "field": "Timestamp", "min": 956703932, "max": 1046476799}
        self.assertTrue(ev(e, {"Timestamp": "-1"}))
        self.assertTrue(ev(e, {"Timestamp": "4102444800"}))
        self.assertFalse(ev(e, {"Timestamp": "978246108"}))

    def test_out_of_range_non_int_is_false(self):
        """非整数由 is_int/not_int 系规则负责（R3 先于 R5），此处不重复报。"""
        e = {"op": "out_of_range", "field": "Timestamp", "min": 1, "max": 5}
        self.assertFalse(ev(e, {"Timestamp": ""}))


class TestSetOps(unittest.TestCase):
    def test_in_set_gender(self):
        e = {"op": "in_set", "field": "Gender", "set_ref": "gender"}
        self.assertTrue(ev(e, {"Gender": "M"}))
        self.assertFalse(ev(e, {"Gender": "X"}))

    def test_in_set_age(self):
        e = {"op": "in_set", "field": "Age", "set_ref": "age"}
        self.assertTrue(ev(e, {"Age": "25"}))
        self.assertFalse(ev(e, {"Age": "30"}))

    def test_in_set_empty_false(self):
        e = {"op": "in_set", "field": "Occupation", "set_ref": "occupation"}
        self.assertFalse(ev(e, {"Occupation": ""}))
        self.assertTrue(ev(e, {"Occupation": "0"}))

    def test_not_in_set(self):
        e = {"op": "not_in_set", "field": "Occupation", "set_ref": "occupation"}
        self.assertTrue(ev(e, {"Occupation": "99"}))
        self.assertTrue(ev(e, {"Occupation": ""}))
        self.assertFalse(ev(e, {"Occupation": "20"}))

    def test_in_set_with_inline_values(self):
        e = {"op": "in_set", "field": "G", "values": ["M", "F"]}
        self.assertTrue(ev(e, {"G": "F"}))
        self.assertFalse(ev(e, {"G": "X"}))


class TestRegexOps(unittest.TestCase):
    def test_regex_match_is_fullmatch(self):
        e = {"op": "regex_match", "field": "Zip-code", "pattern": "^\\d{5}$"}
        self.assertTrue(ev(e, {"Zip-code": "05401"}))
        self.assertFalse(ev(e, {"Zip-code": "054011"}))
        self.assertFalse(ev(e, {"Zip-code": "ABCDE"}))

    def test_regex_mismatch_zip_plus4(self):
        """ZIP+4 不匹配 5 位数字 → 命中 U3（随后由 fix 截取前 5 位）。"""
        e = {"op": "regex_mismatch", "field": "Zip-code", "pattern": "^\\d{5}$"}
        self.assertTrue(ev(e, {"Zip-code": "19087-3622"}))
        self.assertTrue(ev(e, {"Zip-code": ""}))
        self.assertFalse(ev(e, {"Zip-code": "19087"}))

    def test_regex_leading_zero_preserved(self):
        """C-ZIP：邮编按字符串处理，'05401' 必须整串匹配（不能数值化成 5401）。"""
        e = {"op": "regex_match", "field": "Zip-code", "pattern": "^\\d{5}$"}
        self.assertTrue(ev(e, {"Zip-code": "05401"}))


class TestTextOps(unittest.TestCase):
    def test_contains_any_mojibake(self):
        e = {"op": "contains_any", "field": "Title", "values": ["Ã", "Â", "â€", "&#"]}
        self.assertTrue(ev(e, {"Title": "LÃ©on / AmÃ©lie (1994)"}))
        self.assertFalse(ev(e, {"Title": "Léon (1994)"}))

    def test_contains_any_html_entity(self):
        e = {"op": "contains_any", "field": "Title", "values": ["&#"]}
        self.assertTrue(ev(e, {"Title": "And God Created Woman (Et Dieu&#8230;Créa la Femme) (1956)"}))
        self.assertFalse(ev(e, {"Title": "Amelie (2001)"}))

    def test_contains_any_empty_field_false(self):
        e = {"op": "contains_any", "field": "Title", "values": ["Ã"]}
        self.assertFalse(ev(e, {"Title": ""}))

    def test_has_leading_trailing_space(self):
        e = {"op": "has_leading_trailing_space", "field": "Title"}
        self.assertTrue(ev(e, {"Title": "  Breakdown (1997)  "}))
        self.assertTrue(ev(e, {"Title": " x"}))
        self.assertFalse(ev(e, {"Title": "Breakdown (1997)"}))

    def test_has_leading_trailing_space_empty_is_false(self):
        self.assertFalse(ev({"op": "has_leading_trailing_space", "field": "Title"}, {"Title": ""}))

    def test_has_leading_trailing_space_inner_space_ok(self):
        e = {"op": "has_leading_trailing_space", "field": "Title"}
        self.assertFalse(ev(e, {"Title": "Women, The (1939)"}))


class TestYearOps(unittest.TestCase):
    E = {"op": "year_valid", "field": "Title", "pattern": "\\((\\d{4})\\)\\s*$",
         "min": 1900, "max": 2003}

    def test_year_valid_ok(self):
        self.assertTrue(ev(self.E, {"Title": "Toy Story (1995)"}))

    def test_year_valid_boundary_2003(self):
        self.assertTrue(ev(self.E, {"Title": "X (2003)"}))

    def test_year_valid_boundary_1900(self):
        self.assertTrue(ev(self.E, {"Title": "X (1900)"}))

    def test_year_valid_rejects_20xx_placeholder(self):
        """边界：'[20XX]' 无法解析出四位年份 → 非法。"""
        self.assertFalse(ev(self.E, {"Title": "Movie with malformed year [20XX]"}))

    def test_year_valid_rejects_empty_title(self):
        self.assertFalse(ev(self.E, {"Title": ""}))

    def test_year_valid_rejects_out_of_range_year(self):
        self.assertFalse(ev(self.E, {"Title": "X (1899)"}))
        self.assertFalse(ev(self.E, {"Title": "X (2004)"}))

    def test_year_valid_trailing_space_tolerated(self):
        """pattern 末尾有 \\s*$，故 '  Breakdown (1997)  ' 的年份仍可解析。"""
        self.assertTrue(ev(self.E, {"Title": "  Breakdown (1997)  "}))

    def test_year_invalid(self):
        e = dict(self.E, op="year_invalid")
        self.assertTrue(ev(e, {"Title": "Movie with malformed year [20XX]"}))
        self.assertTrue(ev(e, {"Title": ""}))
        self.assertFalse(ev(e, {"Title": "Toy Story (1995)"}))


class TestTimestampOps(unittest.TestCase):
    def test_timestamp_unit_ms_at_threshold(self):
        """边界：恰好等于阈值 100000000000 判定为毫秒（>=）。"""
        e = {"op": "timestamp_unit_ms", "field": "Timestamp", "threshold": 100000000000}
        self.assertTrue(ev(e, {"Timestamp": "100000000000"}))

    def test_timestamp_unit_ms_just_below_threshold(self):
        e = {"op": "timestamp_unit_ms", "field": "Timestamp", "threshold": 100000000000}
        self.assertFalse(ev(e, {"Timestamp": "99999999999"}))

    def test_timestamp_unit_ms_real_values(self):
        e = {"op": "timestamp_unit_ms", "field": "Timestamp", "threshold": 100000000000}
        self.assertTrue(ev(e, {"Timestamp": "1009669071000"}))
        self.assertFalse(ev(e, {"Timestamp": "978246108"}))

    def test_timestamp_unit_ms_non_int_false(self):
        """S3 分母是 is_int(Timestamp)，故不可解析值不算毫秒。"""
        e = {"op": "timestamp_unit_ms", "field": "Timestamp", "threshold": 100000000000}
        self.assertFalse(ev(e, {"Timestamp": ""}))
        self.assertFalse(ev(e, {"Timestamp": "five"}))


class TestStructureOps(unittest.TestCase):
    def test_field_count_ne_per_table(self):
        e = {"op": "field_count_ne", "sep": "::",
             "expected": {"ratings": 4, "users": 5, "movies": 3}}
        self.assertTrue(ev(e, {}, raw_line="22::587::2", table="ratings"))
        self.assertTrue(ev(e, {}, raw_line="5::1722::2::978246108::EXTRA", table="ratings"))
        self.assertFalse(ev(e, {}, raw_line="5::1722::2::978246108", table="ratings"))

    def test_field_count_ne_users_movies(self):
        e = {"op": "field_count_ne", "sep": "::",
             "expected": {"ratings": 4, "users": 5, "movies": 3}}
        self.assertTrue(ev(e, {}, raw_line="3645::M::35::17", table="users"))
        self.assertFalse(ev(e, {}, raw_line="1::F::1::10::48067", table="users"))
        self.assertTrue(ev(e, {}, raw_line="2204::Saboteur (1942)", table="movies"))
        self.assertFalse(ev(e, {}, raw_line="1::Toy Story (1995)::Animation", table="movies"))

    def test_field_count_eq(self):
        e = {"op": "field_count_eq", "sep": "::", "expected": 4}
        self.assertTrue(ev(e, {}, raw_line="5::1722::2::978246108"))
        self.assertFalse(ev(e, {}, raw_line="5::1722::2"))

    def test_foreign_delimiter_comma_line(self):
        """P2 语义：整行不含合法 '::'（切分只有 1 段）且出现非法分隔符。"""
        e = {"op": "foreign_delimiter", "valid_sep": "::", "delimiters": [",", ":", "|"]}
        self.assertTrue(ev(e, {}, raw_line="25,688,4,978133460"))

    def test_foreign_delimiter_colon_and_pipe_lines(self):
        e = {"op": "foreign_delimiter", "valid_sep": "::", "delimiters": [",", ":", "|"]}
        self.assertTrue(ev(e, {}, raw_line="3036:M:56:13:14843"))
        self.assertTrue(ev(e, {}, raw_line="32|Twelve Monkeys (1995)|Drama|Sci-Fi"))

    def test_foreign_delimiter_valid_line_with_comma_in_title_is_not_flagged(self):
        """关键反例：合法 '::' 结构下行内的逗号（标题自带）不得判为结构异常。"""
        e = {"op": "foreign_delimiter", "valid_sep": "::", "delimiters": [",", ":", "|"]}
        self.assertFalse(ev(e, {}, raw_line="927::Women, The (1939)::Comedy"))

    def test_foreign_delimiter_extra_field_line_is_not_flagged(self):
        """关键反例：字段数不对但含合法 '::' 的行属于 P3，不是 P2。

        否则 P2 会把 20 条 movies EXTRA_FIELD 行抢走，P2/P3 数字都会错。
        """
        e = {"op": "foreign_delimiter", "valid_sep": "::", "delimiters": [",", ":", "|"]}
        self.assertFalse(ev(e, {}, raw_line="1729::Jackie Brown (1997)::Crime|Drama::EXTRA_FIELD"))

    def test_foreign_delimiter_clean_line_false(self):
        e = {"op": "foreign_delimiter", "valid_sep": "::", "delimiters": [",", ":", "|"]}
        self.assertFalse(ev(e, {}, raw_line="5::1722::2::978246108"))


class TestTokenOps(unittest.TestCase):
    def test_token_in_set_all_tokens_must_be_known(self):
        e = {"op": "token_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertTrue(ev(e, {"Genres": "Action|Comedy"}))
        self.assertFalse(ev(e, {"Genres": "Comedy|UnknownGenre"}))

    def test_token_in_set_single_token(self):
        e = {"op": "token_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertTrue(ev(e, {"Genres": "Drama"}))
        self.assertFalse(ev(e, {"Genres": "UnknownGenre"}))

    def test_token_in_set_empty_tokens_are_unknown(self):
        e = {"op": "token_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertFalse(ev(e, {"Genres": ""}))
        self.assertFalse(ev(e, {"Genres": "Drama|"}))

    def test_token_not_in_set(self):
        e = {"op": "token_not_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertTrue(ev(e, {"Genres": "Comedy|UnknownGenre"}))
        self.assertFalse(ev(e, {"Genres": "Comedy|Thriller"}))
        self.assertFalse(ev(e, {"Genres": "Thriller"}))

    def test_token_not_in_set_empty_genre(self):
        """空类型 token 不属于官方集合 → M7 命中。"""
        e = {"op": "token_not_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertTrue(ev(e, {"Genres": ""}))

    def test_token_not_in_set_hyphenated_official_token(self):
        """'Sci-Fi' 与 'Film-Noir' 是官方类型，'|' 切分不得误伤。"""
        e = {"op": "token_not_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertFalse(ev(e, {"Genres": "Sci-Fi|Film-Noir"}))

    def test_childrens_apostrophe_is_official(self):
        e = {"op": "token_not_in_set", "field": "Genres", "sep": "|", "set_ref": "genres"}
        self.assertFalse(ev(e, {"Genres": "Animation|Children's"}))

    def test_token_empty_or_duplicate_clean(self):
        e = {"op": "token_empty_or_duplicate", "field": "Genres", "sep": "|"}
        self.assertFalse(ev(e, {"Genres": "Action|Comedy"}))

    def test_token_empty_or_duplicate_empty_token(self):
        e = {"op": "token_empty_or_duplicate", "field": "Genres", "sep": "|"}
        self.assertTrue(ev(e, {"Genres": "Action|"}))
        self.assertTrue(ev(e, {"Genres": ""}))

    def test_token_empty_or_duplicate_duplicate_token(self):
        e = {"op": "token_empty_or_duplicate", "field": "Genres", "sep": "|"}
        self.assertTrue(ev(e, {"Genres": "Drama|Drama"}))


class TestAggregateContextOps(unittest.TestCase):
    """依赖作业侧预计算上下文的聚合类算子。"""

    def test_duplicate_by_hit(self):
        e = {"op": "duplicate_by", "keys": ["UserID", "MovieID", "Timestamp"]}
        self.assertTrue(ev(e, {"UserID": "1", "MovieID": "2", "Timestamp": "9"},
                           dup_group_keys={("1", "2", "9")}))
        self.assertFalse(ev(e, {"UserID": "1", "MovieID": "3", "Timestamp": "9"},
                            dup_group_keys={("1", "2", "9")}))

    def test_duplicate_by_missing_context_raises(self):
        e = {"op": "duplicate_by", "keys": ["UserID"]}
        c = ctx()
        del c["dup_group_keys"]
        with self.assertRaises(ConfigError):
            evaluate(e, {"UserID": "1"}, c)

    def test_conflict_by_hit(self):
        e = {"op": "conflict_by", "key": ["UserID", "MovieID", "Timestamp"], "value": "Rating"}
        self.assertTrue(ev(e, {"UserID": "26", "MovieID": "1422", "Timestamp": "9", "Rating": "3"},
                           conflict_group_keys={("26", "1422", "9")}))
        self.assertFalse(ev(e, {"UserID": "26", "MovieID": "999", "Timestamp": "9", "Rating": "3"},
                            conflict_group_keys={("26", "1422", "9")}))

    def test_conflict_by_two_field_key(self):
        e = {"op": "conflict_by", "key": ["UserID", "MovieID"], "value": "Timestamp"}
        self.assertTrue(ev(e, {"UserID": "15", "MovieID": "2567", "Timestamp": "9"},
                           conflict_group_keys={("15", "2567")}))
        self.assertFalse(ev(e, {"UserID": "15", "MovieID": "1", "Timestamp": "9"},
                            conflict_group_keys={("15", "2567")}))

    def test_value_collision_hit(self):
        e = {"op": "value_collision", "value": "Title", "key": "MovieID", "min_keys": 2}
        self.assertTrue(ev(e, {"Title": "Women, The (1939)", "MovieID": "927"},
                           value_collision_values={"Women, The (1939)"}))
        self.assertFalse(ev(e, {"Title": "Toy Story (1995)", "MovieID": "1"},
                            value_collision_values={"Women, The (1939)"}))

    def test_value_collision_empty_title(self):
        """空标题在原始数据里出现 58 次但 key 是同一个，故不算同名冲突。"""
        e = {"op": "value_collision", "value": "Title", "key": "MovieID", "min_keys": 2}
        self.assertFalse(ev(e, {"Title": "", "MovieID": "2720"}, value_collision_values=set()))


class TestRefOps(unittest.TestCase):
    def test_ref_missing_orphan_user(self):
        e = {"op": "ref_missing", "field": "UserID", "dimension": "users", "key_field": "UserID"}
        self.assertTrue(ev(e, {"UserID": "1006040"}, dim_keys={"users": {"1", "2"}, "movies": set()}))
        self.assertFalse(ev(e, {"UserID": "1"}, dim_keys={"users": {"1", "2"}, "movies": set()}))

    def test_ref_missing_empty_value(self):
        e = {"op": "ref_missing", "field": "MovieID", "dimension": "movies", "key_field": "MovieID"}
        self.assertTrue(ev(e, {"MovieID": ""}, dim_keys={"users": set(), "movies": {"1"}}))

    def test_ref_exists(self):
        e = {"op": "ref_exists", "field": "MovieID", "dimension": "movies", "key_field": "MovieID"}
        self.assertTrue(ev(e, {"MovieID": "3952"}, dim_keys={"users": set(), "movies": {"3952"}}))
        self.assertFalse(ev(e, {"MovieID": "3953"}, dim_keys={"users": set(), "movies": {"3952"}}))

    def test_ref_exists_both_dimensions(self):
        e = {"op": "and", "args": [
            {"op": "ref_exists", "field": "UserID", "dimension": "users", "key_field": "UserID"},
            {"op": "ref_exists", "field": "MovieID", "dimension": "movies", "key_field": "MovieID"}]}
        dk = {"users": {"1"}, "movies": {"2"}}
        self.assertTrue(ev(e, {"UserID": "1", "MovieID": "2"}, dim_keys=dk))
        self.assertFalse(ev(e, {"UserID": "1", "MovieID": "3"}, dim_keys=dk))

    def test_ref_unused(self):
        e = {"op": "ref_unused", "dimension": "movies", "key_field": "MovieID"}
        self.assertTrue(ev(e, {"MovieID": "999"}, ref_unused_keys={"users": set(), "movies": {"999"}}))
        self.assertFalse(ev(e, {"MovieID": "1"}, ref_unused_keys={"users": set(), "movies": {"999"}}))

    def test_ref_missing_missing_context_raises(self):
        e = {"op": "ref_missing", "field": "UserID", "dimension": "users", "key_field": "UserID"}
        c = ctx()
        del c["dim_keys"]
        with self.assertRaises(ConfigError):
            evaluate(e, {"UserID": "1"}, c)

    def test_unknown_dimension_raises(self):
        e = {"op": "ref_missing", "field": "UserID", "dimension": "nope", "key_field": "UserID"}
        with self.assertRaises(ConfigError):
            evaluate(e, {"UserID": "1"}, ctx())


class TestGroupAnomaly(unittest.TestCase):
    E = {"op": "group_anomaly", "group_by": "UserID", "min_matches": 1000,
         "where": {"op": "or", "args": [
             {"op": "not_int", "field": "Rating"},
             {"op": "out_of_range", "field": "Rating", "min": 1, "max": 5}]}}

    def test_group_anomaly_hit_at_threshold(self):
        self.assertTrue(ev(self.E, {"UserID": "1", "Rating": "3"},
                           group_match_counts={"1": 1000}))

    def test_group_anomaly_just_below_threshold(self):
        self.assertFalse(ev(self.E, {"UserID": "1", "Rating": "3"},
                            group_match_counts={"1": 999}))

    def test_group_anomaly_unknown_group(self):
        self.assertFalse(ev(self.E, {"UserID": "9999", "Rating": "3"},
                            group_match_counts={"1": 5000}))

    def test_group_anomaly_where_is_evaluatable(self):
        """where 子句本身必须能用 evaluate 求值（pipeline 用它累计组内命中数）。"""
        self.assertTrue(ev(self.E["where"], {"Rating": "five"}))
        self.assertTrue(ev(self.E["where"], {"Rating": "3.5"}))
        self.assertFalse(ev(self.E["where"], {"Rating": "3"}))


if __name__ == "__main__":
    unittest.main()
