#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置校验器 CLI：校验 cleaning_rules 与 scoring_scheme 两份配置。

用法:
    python3 hadoop/tools/validate_configs.py [rules.json] [scoring.json]
默认:
    <repo>/config/cleaning_rules.v1.json
    <repo>/config/scoring_scheme.v1.json
退出码: 0 = PASS, 1 = FAIL

M1a 起，本脚本只是 **engine.config_loader.validate_configs 的薄壳**：
校验规则（算子白名单、维度固定、权重和为 1、policy.value ∈ options、pipeline 覆盖、
字段清单、T1/T2 区间、reference_domains 一致等）全部实现在引擎里，
避免「CLI 校验」与「引擎加载校验」两套逻辑漂移 —— 二者必须完全一致，
否则会出现「校验通过但运行时报错」或反之。
"""
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HADOOP_DIR = os.path.join(_REPO_ROOT, "hadoop")
if _HADOOP_DIR not in sys.path:
    sys.path.insert(0, _HADOOP_DIR)

from engine.config_loader import validate_configs  # noqa: E402

DEFAULT_RULES = os.path.join(_REPO_ROOT, "config", "cleaning_rules.v1.json")
DEFAULT_SCORING = os.path.join(_REPO_ROOT, "config", "scoring_scheme.v1.json")


def main():
    rules_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RULES
    scoring_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SCORING

    try:
        with open(rules_path, encoding="utf-8") as f:
            rules = json.load(f)
        with open(scoring_path, encoding="utf-8") as f:
            scoring = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print("读取配置失败: %s" % e, file=sys.stderr)
        return 1

    print("rules:   %s (%d rules)" % (rules_path, len(rules.get("rules", []))))
    print("scoring: %s (%d dimensions)" % (scoring_path, len(scoring.get("dimensions", []))))

    errors, warns = validate_configs(rules, scoring)

    print("ERRORS: %d" % len(errors))
    for e in errors:
        print("  -", e)
    print("WARNINGS: %d" % len(warns))
    for w in warns:
        print("  -", w)
    print("RESULT:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
