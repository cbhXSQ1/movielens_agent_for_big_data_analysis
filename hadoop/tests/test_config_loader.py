# -*- coding: utf-8 -*-
"""M1a 测试：engine/config_loader.py —— 加载、校验、版本哈希。

TDD：本文件先于实现编写，初始应全部失败（engine.config_loader 不存在）。

覆盖 plan.md §4.1 / §7.1 与 cleaning_rules.v1.说明.md §12 的加载校验清单：
  * 正常配置：加载成功、版本/哈希/时间边界正确
  * 篡改配置：逐类错误必须被拒（缺字段、坏算子、权重和≠1、维度被改、
    policy.value 不在 options、pipeline 覆盖不全、set_ref 悬空、
    两份配置 reference_domains 不一致、T1/T2 非法、字段不在字段清单内…）
  * 哈希语义：rule_hash=文件字节 sha256；policy_version=排序后 policy(ref,value) 的 sha256
"""
import copy
import hashlib
import json
import os
import unittest

from engine import config_loader
from engine.config_loader import ConfigError, load_schemes, validate_configs

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
RULES_PATH = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
SCORING_PATH = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestLoadSchemes(unittest.TestCase):
    """正常配置的加载结果。"""

    @classmethod
    def setUpClass(cls):
        cls.s = load_schemes(RULES_PATH, SCORING_PATH)

    def test_versions_from_config(self):
        self.assertEqual("1.0.0", self.s.rule_version)
        self.assertEqual("1.0.0", self.s.scoring_version)

    def test_paths_recorded(self):
        self.assertEqual(RULES_PATH, self.s.rules_path)
        self.assertEqual(SCORING_PATH, self.s.scoring_path)

    def test_rule_hash_is_sha256_of_file_bytes(self):
        """rule_hash / scoring_hash 必须是文件**原始字节**的 sha256（不是解析后对象的哈希）。"""
        for path, got in ((RULES_PATH, self.s.rule_hash), (SCORING_PATH, self.s.scoring_hash)):
            with open(path, "rb") as f:
                expected = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(expected, got, path)
            self.assertRegex(got, r"^[0-9a-f]{64}$")

    def test_policy_version_is_sha256_of_sorted_policy_pairs(self):
        """policy_version = 所有 policy(ref,value) 排序后序列化的 sha256。"""
        rules = _read_json(RULES_PATH)
        pairs = []
        for r in rules["rules"]:
            pol = r.get("policy") or {}
            if pol.get("ref") is not None:
                pairs.append((pol["ref"], pol.get("value")))
        pairs.sort()
        payload = "\n".join("%s=%s" % (ref, val) for ref, val in pairs)
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.assertEqual(expected, self.s.policy_version)

    def test_time_boundaries(self):
        self.assertEqual("2001-12-31T23:59:59Z", self.s.t1)
        self.assertEqual("2002-06-30T23:59:59Z", self.s.t2)

    def test_raw_configs_exposed(self):
        self.assertEqual("ml1m-cleaning-default", self.s.rules["scheme_id"])
        self.assertEqual("ml1m-quality-default", self.s.scoring["scheme_id"])
        self.assertEqual(27, len(self.s.rules["rules"]))
        self.assertEqual(5, len(self.s.scoring["dimensions"]))

    def test_real_configs_have_no_validation_errors(self):
        errors, warnings = validate_configs(self.s.rules, self.s.scoring)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)


class TestHashSensitivity(unittest.TestCase):
    """哈希对内容变化的敏感度必须符合语义。"""

    def setUp(self):
        self.rules = _read_json(RULES_PATH)
        self.scoring = _read_json(SCORING_PATH)

    def test_policy_version_changes_on_policy_value_edit(self):
        before = config_loader._policy_version(self.rules)
        rules2 = copy.deepcopy(self.rules)
        rules2["rules"][0]["policy"]["value"] = "quarantine_record"
        after = config_loader._policy_version(rules2)
        self.assertNotEqual(before, after,
                            "改动 policy.value 必须改变 policy_version（换口径=新版本）")

    def test_policy_version_ignores_unrelated_edit(self):
        """只改 description 不应影响 policy_version（它不是处置口径的一部分）。"""
        before = config_loader._policy_version(self.rules)
        rules2 = copy.deepcopy(self.rules)
        rules2["rules"][0]["description"] = "改了说明文字"
        self.assertEqual(before, config_loader._policy_version(rules2))

    def test_policy_version_is_order_insensitive(self):
        """规则在数组中的先后顺序不应影响 policy_version（已排序）。"""
        before = config_loader._policy_version(self.rules)
        rules2 = copy.deepcopy(self.rules)
        rules2["rules"] = list(reversed(rules2["rules"]))
        self.assertEqual(before, config_loader._policy_version(rules2))


class TestTamperedConfigsRejected(unittest.TestCase):
    """篡改配置必须被拒 —— 逐类错误各一例。"""

    def setUp(self):
        self.rules = _read_json(RULES_PATH)
        self.scoring = _read_json(SCORING_PATH)

    def _errors(self, mutate):
        rules = copy.deepcopy(self.rules)
        scoring = copy.deepcopy(self.scoring)
        mutate(rules, scoring)
        errors, _ = validate_configs(rules, scoring)
        return errors

    def assertRejected(self, mutate, needle, msg=""):
        errors = self._errors(mutate)
        self.assertTrue(
            any(needle in e for e in errors),
            "%s\n期望错误中包含 %r，实际 errors=%r" % (msg, needle, errors),
        )

    # ---- 顶层标识 ----
    def test_wrong_config_type(self):
        self.assertRejected(lambda r, s: r.__setitem__("config_type", "something_else"),
                            "config_type")

    def test_wrong_scoring_config_type(self):
        self.assertRejected(lambda r, s: s.__setitem__("config_type", "nope"),
                            "config_type")

    # ---- 算子白名单 ----
    def test_unknown_operator_in_detect(self):
        self.assertRejected(lambda r, s: r["rules"][0]["detect"].__setitem__("op", "totally_made_up"),
                            "unknown op")

    def test_unknown_operator_nested_deep(self):
        """嵌套在 args 深处的坏算子也要被发现（walk 必须递归）。"""
        def mutate(r, s):
            r["rules"][3]["detect"]["args"][0]["op"] = "bogus_deep_op"
        self.assertRejected(mutate, "unknown op")

    # ---- 动作 / 修复策略 / 去重 ----
    def test_bad_action(self):
        self.assertRejected(lambda r, s: r["rules"][0].__setitem__("action", "explode"),
                            "action")

    def test_bad_fix_strategy(self):
        self.assertRejected(lambda r, s: r["rules"][0]["fix"].__setitem__(
            "strategies", ["not_a_registered_strategy"]), "fix strategy")

    def test_dedupe_keep_invalid(self):
        """R6 的 dedupe.keep 只能是 first/latest。"""
        idx = [i for i, x in enumerate(self.rules["rules"]) if x["id"] == "R6"][0]
        self.assertRejected(lambda r, s: r["rules"][idx]["dedupe"].__setitem__("keep", "random"),
                            "keep")

    def test_policy_value_not_in_options(self):
        self.assertRejected(lambda r, s: r["rules"][0]["policy"].__setitem__("value", "not_an_option"),
                            "policy")

    # ---- pipeline 覆盖 ----
    def test_pipeline_references_missing_rule(self):
        def mutate(r, s):
            r["pipeline"][0]["rules"].append("NOPE-99")
        self.assertRejected(mutate, "pipeline")

    def test_enabled_rule_not_in_pipeline(self):
        """enabled 的规则必须被 pipeline 覆盖。"""
        def mutate(r, s):
            for st in r["pipeline"]:
                st["rules"] = [x for x in st["rules"] if x != "P1"]
        self.assertRejected(mutate, "P1")

    # ---- reference_domains / set_ref ----
    def test_set_ref_not_registered(self):
        self.assertRejected(lambda r, s: r["rules"][0]["detect"].__setitem__(
            "values", ["Ã"]) or r["rules"][0]["detect"].__setitem__("set_ref", "no_such_domain"),
                            "set_ref")

    def test_reference_domains_differ_between_configs(self):
        """两份配置的 reference_domains 必须一致（否则同一算子两侧语义不同）。"""
        def mutate(r, s):
            s["reference_domains"]["rating"]["max"] = 10
        self.assertRejected(mutate, "reference_domains")

    # ---- 评分维度与权重 ----
    def test_dimensions_renamed(self):
        """五个维度名称与含义是课程固定，不可增删改。"""
        def mutate(r, s):
            s["dimensions"][0]["id"] = "Accuracy"
        self.assertRejected(mutate, "dimension")

    def test_dimensions_removed(self):
        def mutate(r, s):
            s["dimensions"].pop()
        self.assertRejected(mutate, "dimension")

    def test_dimension_weights_not_summing_to_one(self):
        def mutate(r, s):
            s["dimensions"][0]["weight"] = 0.9
        self.assertRejected(mutate, "dimension weights sum")

    def test_metric_weights_not_summing_to_one(self):
        def mutate(r, s):
            s["dimensions"][0]["metrics"][0]["weight"] = 0.99
        self.assertRejected(mutate, "metric weights sum")

    def test_composite_weights_not_summing_to_one(self):
        def mutate(r, s):
            s["composite"]["weights"]["Accurate"] = 0.9
        self.assertRejected(mutate, "composite weights sum")

    def test_bad_agg(self):
        def mutate(r, s):
            s["dimensions"][0]["metrics"][0]["numerator"]["agg"] = "median"
        self.assertRejected(mutate, "agg")

    def test_bad_measure(self):
        def mutate(r, s):
            s["dimensions"][0]["metrics"][0]["measure"] = "ratio_but_typo"
        self.assertRejected(mutate, "measure")

    def test_freshness_missing_params(self):
        """F2 是 freshness，必须有 params。"""
        def mutate(r, s):
            for d in s["dimensions"]:
                for m in d["metrics"]:
                    if m["measure"] == "freshness":
                        m.pop("params")
        self.assertRejected(mutate, "params")

    def test_metric_missing_limitations(self):
        def mutate(r, s):
            s["dimensions"][0]["metrics"][0].pop("limitations")
        self.assertRejected(mutate, "limitations")

    def test_metric_missing_unverifiable(self):
        def mutate(r, s):
            s["dimensions"][0]["metrics"][0].pop("unverifiable")
        self.assertRejected(mutate, "unverifiable")

    # ---- 字段清单 ----
    def test_field_not_in_inventory(self):
        def mutate(r, s):
            s["dimensions"][0]["metrics"][0]["numerator"]["where"]["args"][0]["field"] = "NoSuchField"
        self.assertRejected(mutate, "NoSuchField")

    def test_cleaning_rule_field_not_in_inventory(self):
        def mutate(r, s):
            r["rules"][0]["detect"]["field"] = "NotAField"
        self.assertRejected(mutate, "NotAField")

    # ---- 时间边界 ----
    def test_t1_not_before_t2(self):
        def mutate(r, s):
            tb = r["data_version"]["time_boundaries"]
            tb["T1"], tb["T2"] = tb["T2"], tb["T1"]
        self.assertRejected(mutate, "T1")

    def test_time_boundary_outside_declared_timestamp_range(self):
        def mutate(r, s):
            r["data_version"]["time_boundaries"]["T2"] = "2009-01-01T00:00:00Z"
        self.assertRejected(mutate, "range")


class TestConfigError(unittest.TestCase):
    """ConfigError 必须携带完整错误列表（供 CLI 的 details 字段使用）。"""

    def test_config_error_carries_error_list(self):
        bad = _read_json(RULES_PATH)
        bad["config_type"] = "wrong"
        scoring = _read_json(SCORING_PATH)
        with self.assertRaises(ConfigError) as ctx:
            config_loader._raise_if_errors(bad, scoring)
        self.assertTrue(hasattr(ctx.exception, "errors"))
        self.assertTrue(any("config_type" in e for e in ctx.exception.errors))
        self.assertIn("config_type", str(ctx.exception))

    def test_load_schemes_raises_on_bad_file(self):
        """文件不存在 / 非法 JSON → ConfigError（而不是裸 OSError/JSONDecodeError）。"""
        with self.assertRaises(ConfigError):
            load_schemes(os.path.join(REPO_ROOT, "config", "does_not_exist.json"), SCORING_PATH)


if __name__ == "__main__":
    unittest.main()
