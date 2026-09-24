#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置校验器：校验 cleaning_rules 与 scoring_scheme 两份配置。

用法:
    python3 hadoop/tools/validate_configs.py [rules.json] [scoring.json]
默认:
    <repo>/config/cleaning_rules.v1.json
    <repo>/config/scoring_scheme.v1.json
退出码: 0 = PASS, 1 = FAIL
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULES = ROOT / "config" / "cleaning_rules.v1.json"
DEFAULT_SCORING = ROOT / "config" / "scoring_scheme.v1.json"

OPS = {"and", "or", "not", "is_null", "not_null", "is_int", "not_int", "in_range", "out_of_range",
       "in_set", "not_in_set", "regex_match", "regex_mismatch", "contains_any", "has_leading_trailing_space",
       "year_valid", "year_invalid", "timestamp_unit_ms", "field_count_ne", "field_count_eq",
       "foreign_delimiter", "token_in_set", "token_not_in_set", "token_empty_or_duplicate",
       "duplicate_by", "conflict_by", "value_collision", "ref_missing", "ref_exists", "ref_unused",
       "group_anomaly"}
AGGS = {"count", "distinct_key_count", "distinct_row_count", "nonempty_field_count", "field_count",
        "valid_field_count", "token_count", "record_complete_count", "dup_group_count"}
ACTIONS = {"quarantine", "dedupe", "dedupe_resolve", "fix", "mark", "check", "report"}
FIXES = {"strip", "decode_html_entity", "decode_double_encoding", "timestamp_ms_to_s", "zip_plus4_truncate",
         "blank_invalid_field", "prefer_valid_record", "keep_first", "field_level_merge"}
DIMS = {"Accurate", "Complete", "Unique", "Up-to-date", "Consistent"}


def walk_ops(node, path, refs, errors):
    if isinstance(node, dict):
        if "op" in node:
            if node["op"] not in OPS:
                errors.append(f"{path}: unknown op '{node['op']}'")
            if "set_ref" in node:
                refs.add(node["set_ref"])
        for k, v in node.items():
            walk_ops(v, f"{path}.{k}", refs, errors)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_ops(v, f"{path}[{i}]", refs, errors)


def validate(rules, scoring):
    errors, warns = [], []

    # ---------- cleaning ----------
    if rules.get("config_type") != "cleaning_rules":
        errors.append("cleaning config_type must be 'cleaning_rules'")
    refs = set()
    for r in rules.get("rules", []):
        p = r.get("id", "?")
        walk_ops(r.get("detect"), p + ".detect", refs, errors)
        if r.get("action") not in ACTIONS:
            errors.append(f"{p}: bad action {r.get('action')}")
        for s in r.get("fix", {}).get("strategies", []):
            if s not in FIXES:
                errors.append(f"{p}: bad fix strategy {s}")
        pol = r.get("policy", {})
        if pol and ("value" not in pol or "options" not in pol or pol["value"] not in pol["options"]):
            errors.append(f"{p}: policy value not in options")
        if not r.get("origin"):
            warns.append(f"{p}: missing origin")

    pipe_ids = [rid for st in rules.get("pipeline", []) for rid in st.get("rules", [])]
    rule_ids = [r.get("id") for r in rules.get("rules", [])]
    for rid in pipe_ids:
        if rid not in rule_ids:
            errors.append(f"pipeline references missing rule {rid}")
    for r in rules.get("rules", []):
        if r.get("enabled") and r.get("id") not in pipe_ids:
            errors.append(f"enabled rule {r.get('id')} not in pipeline")
    for ref in refs:
        if ref not in rules.get("reference_domains", {}):
            errors.append(f"cleaning set_ref '{ref}' not in reference_domains")

    # ---------- scoring ----------
    if scoring.get("config_type") != "scoring_scheme":
        errors.append("scoring config_type must be 'scoring_scheme'")
    refs2 = set()
    dim_ids = [d.get("id") for d in scoring.get("dimensions", [])]
    if set(dim_ids) != DIMS or len(dim_ids) != 5:
        errors.append(f"dimensions mismatch: {dim_ids}")
    dw = sum(d.get("weight", 0) for d in scoring.get("dimensions", []))
    if abs(dw - 1.0) > 1e-9:
        errors.append(f"dimension weights sum {dw}")
    for d in scoring.get("dimensions", []):
        mw = 0.0
        for m in d.get("metrics", []):
            if m.get("enabled", True):
                mw += m.get("weight", 0)
            if not m.get("description") or not m.get("limitations") or "unverifiable" not in m:
                errors.append(f"{m.get('id')}: missing description/limitations/unverifiable")
            if m.get("measure") not in {"ratio", "freshness"}:
                errors.append(f"{m.get('id')}: bad measure {m.get('measure')}")
            if m.get("measure") == "ratio":
                for part in ("numerator", "denominator"):
                    agg = m.get(part, {}).get("agg")
                    if agg not in AGGS:
                        errors.append(f"{m.get('id')}.{part}: bad agg {agg}")
                    walk_ops(m.get(part, {}).get("where"), f"{m.get('id')}.{part}.where", refs2, errors)
            elif "params" not in m:
                errors.append(f"{m.get('id')}: freshness missing params")
        if abs(mw - 1.0) > 1e-9:
            errors.append(f"dimension {d.get('id')} metric weights sum {mw:.4f}")
    cw = sum(scoring.get("composite", {}).get("weights", {}).values())
    if scoring.get("composite", {}).get("enabled") and abs(cw - 1.0) > 1e-9:
        errors.append(f"composite weights sum {cw}")
    for ref in refs2:
        if ref not in scoring.get("reference_domains", {}):
            errors.append(f"scoring set_ref '{ref}' not in reference_domains")
    if rules.get("reference_domains") != scoring.get("reference_domains"):
        warns.append("reference_domains differ between cleaning and scoring configs")

    return errors, warns


def main():
    rules_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RULES
    scoring_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_SCORING
    with open(rules_path, encoding="utf-8") as f:
        rules = json.load(f)
    with open(scoring_path, encoding="utf-8") as f:
        scoring = json.load(f)
    print(f"rules:   {rules_path} ({len(rules.get('rules', []))} rules)")
    print(f"scoring: {scoring_path} ({len(scoring.get('dimensions', []))} dimensions)")
    errors, warns = validate(rules, scoring)
    print(f"ERRORS: {len(errors)}")
    for e in errors:
        print("  -", e)
    print(f"WARNINGS: {len(warns)}")
    for w in warns:
        print("  -", w)
    print("RESULT:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
