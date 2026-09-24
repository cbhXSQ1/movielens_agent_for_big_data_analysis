# -*- coding: utf-8 -*-
"""engine/config_loader.py —— 配置加载、校验与版本哈希（plan.md §4.1）。

职责：
  1. 读取两份 v1 配置（`cleaning_rules` / `scoring_scheme`）
  2. 按「封闭算子库 + 注册策略库」白名单做完整校验，失败即拒绝执行
     （cleaning_rules.v1.说明.md §12：不得带着坏规则跑数据）
  3. 计算版本哈希：rule_hash / scoring_hash（文件字节 sha256）、
     policy_version（所有 policy(ref,value) 排序后的 sha256）

校验规则 = `hadoop/tools/validate_configs.py` 的全部检查项，并补齐 §12 清单中
原先缺失的项（字段清单、算子参数、dedupe.keep、T1/T2 区间、version/status）。
`hadoop/tools/validate_configs.py` 已改为调用本模块，保证**单一事实来源**。

只用标准库；不依赖 Hadoop。
"""
import hashlib
import json
import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 封闭白名单（v1）。扩展需升级版本号。
# ---------------------------------------------------------------------------

#: 31 个检测算子 —— 与 cleaning_rules.v1.说明.md §7 一一对应
OPS = {
    "and", "or", "not",
    "is_null", "not_null", "is_int", "not_int",
    "in_range", "out_of_range",
    "in_set", "not_in_set",
    "regex_match", "regex_mismatch",
    "contains_any", "has_leading_trailing_space",
    "year_valid", "year_invalid",
    "timestamp_unit_ms",
    "field_count_ne", "field_count_eq",
    "foreign_delimiter",
    "token_in_set", "token_not_in_set", "token_empty_or_duplicate",
    "duplicate_by", "conflict_by", "value_collision",
    "ref_missing", "ref_exists", "ref_unused",
    "group_anomaly",
}

#: 聚合算子（scoring 的 numerator/denominator）
AGGS = {
    "count", "distinct_key_count", "distinct_row_count", "nonempty_field_count",
    "field_count", "valid_field_count", "token_count", "record_complete_count",
    "dup_group_count",
}

ACTIONS = {"quarantine", "dedupe", "dedupe_resolve", "fix", "mark", "check", "report"}
FIXES = {
    "strip", "decode_html_entity", "decode_double_encoding", "timestamp_ms_to_s",
    "zip_plus4_truncate", "blank_invalid_field",
    "prefer_valid_record", "keep_first", "field_level_merge",
}
DEDUPE_KEEP = {"first", "latest"}
MEASURES = {"ratio", "freshness"}

#: 五个维度名称与含义为课程固定，不可增删改
DIMS = {"Accurate", "Complete", "Unique", "Up-to-date", "Consistent"}

#: 每个算子必需的参数（用于「参数类型/数量正确」检查）
_OP_REQUIRED = {
    "and": ("args",), "or": ("args",), "not": ("args",),
    "is_null": ("field",), "not_null": ("field",),
    "is_int": ("field",), "not_int": ("field",),
    "in_range": ("field",), "out_of_range": ("field",),
    "in_set": ("field",), "not_in_set": ("field",),
    "regex_match": ("field", "pattern"), "regex_mismatch": ("field", "pattern"),
    "contains_any": ("field", "values"),
    "has_leading_trailing_space": ("field",),
    "year_valid": ("field", "pattern", "min", "max"),
    "year_invalid": ("field", "pattern", "min", "max"),
    "timestamp_unit_ms": ("field", "threshold"),
    "field_count_ne": ("sep", "expected"), "field_count_eq": ("sep", "expected"),
    "foreign_delimiter": ("valid_sep", "delimiters"),
    "token_in_set": ("field", "sep", "set_ref"),
    "token_not_in_set": ("field", "sep", "set_ref"),
    "token_empty_or_duplicate": ("field", "sep"),
    "duplicate_by": ("keys",),
    "conflict_by": ("key", "value"),
    "value_collision": ("value", "key", "min_keys"),
    "ref_missing": ("field", "dimension", "key_field"),
    "ref_exists": ("field", "dimension", "key_field"),
    "ref_unused": ("dimension", "key_field"),
    "group_anomaly": ("group_by", "where", "min_matches"),
}

#: 引用维表的算子里，`dimension` 的合法取值
DIMENSIONS = {"users", "movies", "ratings", "all"}


class ConfigError(Exception):
    """配置加载/校验失败。`errors` 为完整错误列表（供 CLI 的 details 字段）。"""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__("配置校验失败(%d 项): %s" % (len(self.errors), "; ".join(self.errors)))


@dataclass
class LoadedSchemes:
    """已加载并校验通过的方案组合（plan.md §4.1）。"""
    rules: dict
    scoring: dict
    rules_path: str
    scoring_path: str
    rule_version: str
    rule_hash: str
    scoring_version: str
    scoring_hash: str
    policy_version: str
    t1: str
    t2: str


# ---------------------------------------------------------------------------
# 哈希工具
# ---------------------------------------------------------------------------

def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    with open(path, "rb") as f:
        return _sha256_bytes(f.read())


def _policy_version(rules):
    """所有 policy(ref,value) 排序后序列化的 sha256。

    语义：只要有任何一条规则的处置口径（policy.value）发生变化，
    policy_version 必然变化 —— 因为「换口径 = 新方案版本」，跨版本结果不可比
    （scoring_scheme 的 comparability 与 cleaning_rules 说明 §13）。
    只改 description 等非口径字段不影响它。
    """
    pairs = []
    for r in rules.get("rules", []) or []:
        pol = r.get("policy") or {}
        if pol.get("ref") is not None:
            pairs.append((str(pol["ref"]), str(pol.get("value"))))
    pairs.sort()
    payload = "\n".join("%s=%s" % (ref, val) for ref, val in pairs)
    return _sha256_bytes(payload.encode("utf-8"))


# ---------------------------------------------------------------------------
# 字段清单解析
# ---------------------------------------------------------------------------

def _inventory(scoring):
    inv = scoring.get("field_inventory") or {}
    return {k: list(v) for k, v in inv.items() if isinstance(v, list)}


def _table_fields(inv, table):
    """(fields:set, error:str|None) —— table 为 None/'all' 时取并集。"""
    if table is None or table == "all":
        out = set()
        for v in inv.values():
            out |= set(v)
        return out, None
    if table not in inv:
        return set(), "未知表 '%s'（field_inventory 中不存在）" % table
    return set(inv[table]), None


def _scope_fields(part, inv):
    """解析 numerator/denominator 的字段作用域 → (fields:set, table_label, errors:list)。

    `tables` 有两种合法写法（两份配置都用到了）：
      * 表名字符串列表：U3 的 denominator `"tables": ["users", "movies"]`
      * 对象列表：       U3 的 numerator / S4 的 `"tables": [{"table": ..., "key": [...]}, ...]`
    """
    errors = []
    fields = set()
    label = "all"

    tables = part.get("tables")
    if isinstance(tables, list) and tables:
        labels = []
        for t in tables:
            if isinstance(t, str):
                tname = t
                f, err = _table_fields(inv, tname)
            elif isinstance(t, dict):
                tname = t.get("table")
                f, err = _table_fields(inv, tname)
            else:
                errors.append("tables 元素必须是表名字符串或对象，实际 %r" % (t,))
                continue
            if err:
                errors.append(err)
            fields |= f
            labels.append(str(tname))
        return fields, "/".join(labels), errors

    if "table" in part:
        f, err = _table_fields(inv, part.get("table"))
        if err:
            errors.append(err)
        return f, str(part.get("table")), errors

    # scope: all 或无表限定
    f, _ = _table_fields(inv, None)
    return f, label, errors


# ---------------------------------------------------------------------------
# detect 表达式遍历
# ---------------------------------------------------------------------------

def _fields_referenced(node):
    """返回该算子节点直接引用的 [(参数名, 字段名, 归属)]。

    归属 'self'  = 属于本规则/本指标的表
    归属 'dim'   = 属于 `dimension` 指向的维表
    """
    out = []
    if not isinstance(node, dict):
        return out
    op = node.get("op")

    if isinstance(node.get("field"), str):
        out.append(("field", node["field"], "self"))
    if op == "duplicate_by":
        for k in node.get("keys") or []:
            out.append(("keys", k, "self"))
    if op == "conflict_by":
        for k in node.get("key") or []:
            out.append(("key", k, "self"))
        if isinstance(node.get("value"), str):
            out.append(("value", node["value"], "self"))
    if op == "value_collision":
        if isinstance(node.get("value"), str):
            out.append(("value", node["value"], "self"))
        if isinstance(node.get("key"), str):
            out.append(("key", node["key"], "self"))
    if op in ("ref_missing", "ref_exists", "ref_unused"):
        if isinstance(node.get("key_field"), str):
            out.append(("key_field", node["key_field"], "dim"))
    if op == "group_anomaly" and isinstance(node.get("group_by"), str):
        out.append(("group_by", node["group_by"], "self"))
    return out


def _walk_expr(node, path, refs, errors, inv, self_fields, relax_token=False):
    """递归检查一个 detect 表达式 AST。

    refs   —— 收集到的 set_ref（调用方检查是否登记在 reference_domains）
    errors —— 就地追加错误

    只递归进入**承载子表达式**的键：`args`（and/or/not）与 `where`（group_anomaly）。
    像 `expected`（按表映射的字典）、`values`（字符串数组）这类参数不得被当成表达式，
    否则会误报「表达式节点缺少 op」。

    relax_token=True 用于 scoring 的 `where` 子句：那里 A6 的
    `token_count` 把 `field`/`sep` 放在 agg 层，where 内的 `token_in_set`
    只带 `set_ref`，因此不可强制要求 field/sep。
    """
    if isinstance(node, dict):
        op = node.get("op")
        if op is None:
            errors.append("%s: 表达式节点缺少 op" % path)
            return
        if op not in OPS:
            errors.append("%s: unknown op '%s'" % (path, op))
        else:
            for req in _OP_REQUIRED.get(op, ()):
                if req in node:
                    continue
                # where 子句里 token_* 的 field/sep 由 agg 层继承
                if relax_token and op in ("token_in_set", "token_not_in_set") \
                        and req in ("field", "sep"):
                    continue
                errors.append("%s: 算子 '%s' 缺少必需参数 '%s'" % (path, op, req))
            # 参数类型抽查
            if op in ("and", "or", "not") and not isinstance(node.get("args"), list):
                errors.append("%s: 算子 '%s' 的 args 必须是数组" % (path, op))
            if op in ("in_range", "out_of_range") and "min" not in node and "max" not in node:
                errors.append("%s: 算子 '%s' 至少需要 min 或 max" % (path, op))
            if op in ("in_set", "not_in_set") and "set_ref" not in node and "values" not in node:
                errors.append("%s: 算子 '%s' 需要 set_ref 或 values" % (path, op))
            if op in ("field_count_ne", "field_count_eq"):
                exp = node.get("expected")
                if isinstance(exp, dict):
                    for t in exp:
                        if t not in inv:
                            errors.append("%s: expected 中的表 '%s' 不在 field_inventory" % (path, t))
                elif not isinstance(exp, int):
                    errors.append("%s: expected 必须是整数或按表映射的对象" % path)

        # set_ref 收集
        if "set_ref" in node:
            refs.add(node["set_ref"])

        # 字段归属检查
        dim = node.get("dimension")
        if dim is not None and dim not in DIMENSIONS:
            errors.append("%s: 未知 dimension '%s'" % (path, dim))
        for label, fname, owner in _fields_referenced(node):
            if owner == "dim":
                allowed, err = _table_fields(inv, None if dim in (None, "all") else dim)
                if err:
                    errors.append("%s: %s" % (path, err))
            else:
                allowed, err = self_fields
                if err:
                    errors.append("%s: %s" % (path, err))
            if fname not in allowed:
                errors.append("%s: %s '%s' 不在字段清单内（表 %s）"
                              % (path, label, fname, dim if owner == "dim" else "self"))

        # 仅递归承载子表达式的键
        for k in ("args", "where"):
            if k in node:
                _walk_expr(node[k], "%s.%s" % (path, k), refs, errors, inv,
                           self_fields, relax_token)

    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_expr(v, "%s[%d]" % (path, i), refs, errors, inv, self_fields, relax_token)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _validate_time_boundaries(rules, errors):
    """§12：T1 < T2 且落在 timestamp 声明范围内。"""
    tb = (rules.get("data_version") or {}).get("time_boundaries") or {}
    t1, t2 = tb.get("T1"), tb.get("T2")
    ts = (rules.get("reference_domains") or {}).get("timestamp") or {}
    if not t1 or not t2:
        errors.append("data_version.time_boundaries 缺少 T1 或 T2")
        return

    def _parse(s):
        # 形如 2001-12-31T23:59:59Z
        import datetime
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc)
        except Exception:
            return None

    d1, d2 = _parse(t1), _parse(t2)
    if d1 is None:
        errors.append("T1 '%s' 不是合法的 ISO8601 UTC 时间" % t1)
    if d2 is None:
        errors.append("T2 '%s' 不是合法的 ISO8601 UTC 时间" % t2)
    if d1 and d2 and not d1 < d2:
        errors.append("T1 (%s) 必须早于 T2 (%s)" % (t1, t2))
    lo, hi = ts.get("min"), ts.get("max")
    if d1 and d2 and isinstance(lo, int) and isinstance(hi, int):
        e1, e2 = int(d1.timestamp()), int(d2.timestamp())
        if not (lo <= e1 <= hi):
            errors.append("T1 (%s → %d) 超出 timestamp 声明 range [%d, %d]" % (t1, e1, lo, hi))
        if not (lo <= e2 <= hi):
            errors.append("T2 (%s → %d) 超出 timestamp 声明 range [%d, %d]" % (t2, e2, lo, hi))


def _validate_cleaning(rules, scoring, inv, errors, warnings):
    if rules.get("config_type") != "cleaning_rules":
        errors.append("cleaning config_type must be 'cleaning_rules'")
    if not rules.get("version"):
        errors.append("cleaning 缺少 version")
    if rules.get("status") not in ("registered_default", "custom"):
        errors.append("cleaning status 必须是 registered_default 或 custom")

    refs = set()
    rule_ids = [r.get("id") for r in rules.get("rules", [])]
    for r in rules.get("rules", []):
        rid = r.get("id", "?")
        table = r.get("table")
        self_fields, terr = _table_fields(inv, table)
        if terr:
            errors.append("%s: %s" % (rid, terr))
        self_fields = (self_fields, None)

        _walk_expr(r.get("detect"), rid + ".detect", refs, errors, inv, self_fields)

        if r.get("action") not in ACTIONS:
            errors.append("%s: bad action %r" % (rid, r.get("action")))
        for s in (r.get("fix") or {}).get("strategies", []) or []:
            if s not in FIXES:
                errors.append("%s: bad fix strategy '%s'" % (rid, s))
        keep = (r.get("dedupe") or {}).get("keep")
        if keep is not None and keep not in DEDUPE_KEEP:
            errors.append("%s: dedupe.keep '%s' 不在 %s" % (rid, keep, sorted(DEDUPE_KEEP)))
        for k in (r.get("dedupe") or {}).get("keys", []) or []:
            if k not in self_fields[0]:
                errors.append("%s: dedupe.keys '%s' 不在字段清单内" % (rid, k))

        pol = r.get("policy") or {}
        if pol:
            if "value" not in pol or "options" not in pol or pol.get("value") not in pol.get("options", []):
                errors.append("%s: policy value %r not in options %r"
                              % (rid, pol.get("value"), pol.get("options")))
        if not r.get("origin"):
            warnings.append("%s: missing origin" % rid)

    pipe_ids = [rid for st in rules.get("pipeline", []) for rid in st.get("rules", [])]
    for rid in pipe_ids:
        if rid not in rule_ids:
            errors.append("pipeline references missing rule %s" % rid)
    for r in rules.get("rules", []):
        if r.get("enabled") and r.get("id") not in pipe_ids:
            errors.append("enabled rule %s not in pipeline" % r.get("id"))

    domains = rules.get("reference_domains", {})
    for ref in refs:
        if ref not in domains:
            errors.append("cleaning set_ref '%s' not in reference_domains" % ref)


def _validate_scoring(rules, scoring, inv, errors, warnings):
    if scoring.get("config_type") != "scoring_scheme":
        errors.append("scoring config_type must be 'scoring_scheme'")
    if not scoring.get("version"):
        errors.append("scoring 缺少 version")
    if scoring.get("status") not in ("registered_default", "custom"):
        errors.append("scoring status 必须是 registered_default 或 custom")

    dim_ids = [d.get("id") for d in scoring.get("dimensions", [])]
    if set(dim_ids) != DIMS or len(dim_ids) != 5:
        errors.append("dimensions mismatch（五个维度名称固定，不可增删改）: %s" % dim_ids)

    dw = sum(d.get("weight", 0) for d in scoring.get("dimensions", []))
    if abs(dw - 1.0) > 1e-9:
        errors.append("dimension weights sum %s" % dw)

    refs = set()
    for d in scoring.get("dimensions", []):
        mw = 0.0
        for m in d.get("metrics", []):
            mid = m.get("id", "?")
            if m.get("enabled", True):
                mw += m.get("weight", 0)
            if not m.get("description") or not m.get("limitations") or "unverifiable" not in m:
                errors.append("%s: missing description/limitations/unverifiable" % mid)
            measure = m.get("measure")
            if measure not in MEASURES:
                errors.append("%s: bad measure %r" % (mid, measure))
            if measure == "ratio":
                for part_name in ("numerator", "denominator"):
                    part = m.get(part_name) or {}
                    agg = part.get("agg")
                    if agg not in AGGS:
                        errors.append("%s.%s: bad agg %r" % (mid, part_name, agg))
                    allowed_fields, label, perrors = _scope_fields(part, inv)
                    for pe in perrors:
                        errors.append("%s.%s: %s" % (mid, part_name, pe))

                    # 字段清单检查：field / fields / key / keys / value
                    checks = []
                    if isinstance(part.get("field"), str):
                        checks.append(("field", part["field"]))
                    for f in part.get("fields") or []:
                        if isinstance(f, str):
                            checks.append(("fields", f))
                    for f in part.get("key") or []:
                        if isinstance(f, str):
                            checks.append(("key", f))
                    for f in part.get("keys") or []:
                        if isinstance(f, str):
                            checks.append(("keys", f))
                    if part.get("agg") == "dup_group_count":
                        pass  # 其 value/key 在 tables 子项里，下面单独查
                    for cname, fname in checks:
                        if fname not in allowed_fields:
                            errors.append("%s.%s: %s '%s' 不在字段清单内（表 %s）"
                                          % (mid, part_name, cname, fname, label))

                    # tables 列表子项：key / value
                    for t in part.get("tables") or []:
                        if not isinstance(t, dict):
                            continue
                        tfields, terr = _table_fields(inv, t.get("table"))
                        if terr:
                            errors.append("%s.%s: %s" % (mid, part_name, terr))
                            continue
                        subs = list(t.get("key") or [])
                        if isinstance(t.get("value"), str):
                            subs.append(t["value"])
                        elif isinstance(t.get("value"), list):
                            subs.extend(t["value"])
                        for fname in subs:
                            if fname not in tfields:
                                errors.append("%s.%s: 字段 '%s' 不在表 %s 的字段清单内"
                                              % (mid, part_name, fname, t.get("table")))

                    _walk_expr(part.get("where"), "%s.%s.where" % (mid, part_name),
                               refs, errors, inv, (allowed_fields, None), relax_token=True)
            elif "params" not in m:
                errors.append("%s: freshness missing params" % mid)
        if abs(mw - 1.0) > 1e-9:
            errors.append("dimension %s metric weights sum %.4f" % (d.get("id"), mw))

    comp = scoring.get("composite", {})
    if comp.get("enabled"):
        cw = sum(comp.get("weights", {}).values())
        if abs(cw - 1.0) > 1e-9:
            errors.append("composite weights sum %s" % cw)

    sdomains = scoring.get("reference_domains", {})
    for ref in refs:
        if ref not in sdomains:
            errors.append("scoring set_ref '%s' not in reference_domains" % ref)

    if rules.get("reference_domains") != scoring.get("reference_domains"):
        errors.append("reference_domains differ between cleaning and scoring configs")


def validate_configs(rules, scoring):
    """返回 (errors, warnings)。空 errors 表示可以执行。"""
    errors, warnings = [], []
    inv = _inventory(scoring)
    if not inv:
        warnings.append("scoring 缺少 field_inventory，跳过字段清单校验")
    _validate_cleaning(rules, scoring, inv, errors, warnings)
    _validate_scoring(rules, scoring, inv, errors, warnings)
    _validate_time_boundaries(rules, errors)
    return errors, warnings


def _raise_if_errors(rules, scoring):
    errors, _ = validate_configs(rules, scoring)
    if errors:
        raise ConfigError(errors)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _load_json(path):
    if not os.path.isfile(path):
        raise ConfigError("配置文件不存在: %s" % path)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError("配置文件不是合法 JSON: %s (%s)" % (path, e))
    except OSError as e:
        raise ConfigError("配置文件读取失败: %s (%s)" % (path, e))


def load_schemes(rules_path, scoring_path):
    """加载并校验两份配置；校验失败抛 ConfigError（含完整错误列表）。"""
    rules = _load_json(rules_path)
    scoring = _load_json(scoring_path)
    _raise_if_errors(rules, scoring)

    tb = (rules.get("data_version") or {}).get("time_boundaries") or {}
    return LoadedSchemes(
        rules=rules,
        scoring=scoring,
        rules_path=rules_path,
        scoring_path=scoring_path,
        rule_version=rules.get("version", ""),
        rule_hash=_sha256_file(rules_path),
        scoring_version=scoring.get("version", ""),
        scoring_hash=_sha256_file(scoring_path),
        policy_version=_policy_version(rules),
        t1=tb.get("T1", ""),
        t2=tb.get("T2", ""),
    )
