# -*- coding: utf-8 -*-
"""engine/operators.py —— 31 个检测算子 + evaluate()（plan.md §4.2）。

表达式是 JSON AST（不是代码字符串），节点形如 {"op": "算子名", ...参数}；
`and`/`or`/`not` 用 `args` 承载子表达式。算子库为**封闭白名单**
（cleaning_rules.v1.说明.md §7），未知算子抛 ConfigError。

ctx 契约（由 pipeline / Streaming 作业注入；聚合类算子依赖它）::

    {
      "reference_domains":     dict,  # 配置的 reference_domains，解析 set_ref
      "raw_line":              str,   # 原始行：field_count_ne / foreign_delimiter
      "table":                 str,   # 表名：field_count_ne 的 expected 按表映射时使用
      "dup_group_keys":        set,   # 本表中「存在重复」的业务键元组      (duplicate_by)
      "conflict_group_keys":   set,   # 本表中「同键 value 冲突」的键元组    (conflict_by)
      "value_collision_values":set,   # 本表中「同一 value 对应 >=min_keys 个 key」的 value
      "dim_keys":              dict,  # {dimension: 该维表 key_field 取值集合} (ref_missing/ref_exists)
      "ref_unused_keys":       dict,  # {dimension: 从未被引用的 key 集合}     (ref_unused)
      "group_match_counts":    dict,  # {组值: 满足 where 的条数}              (group_anomaly)
    }

聚合类算子所需的 ctx 键**缺失即抛 ConfigError**（fail loud），避免静默返回 False
把「没算」伪装成「没命中」——那会让规则命中数悄悄变 0。

只用标准库；不依赖 Hadoop。
"""
import re

from engine.config_loader import OPS, ConfigError

#: 严格整数：允许正负号，拒绝小数/科学计数/下划线/空白。
#: 注意 Python 的 int() 会接受 '1_0' 与 ' 5'，故不能用 int() 直接判定。
_INT_RE = re.compile(r"[+-]?\d+")

__all__ = ["OPERATORS", "evaluate"]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _field(fields, name):
    """取字段值；缺失字段按空串（null）处理。"""
    v = fields.get(name, "")
    return v if isinstance(v, str) else str(v)


def _as_int(s):
    """可解析为整数则返回 int，否则 None。"""
    if isinstance(s, str) and _INT_RE.fullmatch(s):
        return int(s)
    return None


def _need(ctx, key, op):
    """取必需的上下文；缺失即抛 ConfigError。"""
    if not isinstance(ctx, dict) or key not in ctx:
        raise ConfigError(
            "算子 '%s' 需要 ctx['%s']（该算子依赖作业侧/pipeline 预计算的上下文）" % (op, key))
    return ctx[key]


def _resolve_set(node, ctx, op):
    """解析 in_set/not_in_set/token_* 的集合：优先 values，其次 set_ref。"""
    if "values" in node:
        vals = node["values"]
        if not isinstance(vals, list):
            raise ConfigError("算子 '%s' 的 values 必须是数组" % op)
        return set(vals)
    ref = node.get("set_ref")
    if ref is None:
        raise ConfigError("算子 '%s' 需要 values 或 set_ref" % op)
    rd = ctx.get("reference_domains") if isinstance(ctx, dict) else None
    if not isinstance(rd, dict):
        raise ConfigError("算子 '%s' 需要 ctx['reference_domains'] 以解析 set_ref '%s'" % (op, ref))
    if ref not in rd:
        raise ConfigError("算子 '%s' 的 set_ref '%s' 未在 reference_domains 中登记" % (op, ref))
    val = rd[ref]
    if isinstance(val, dict):
        raise ConfigError("算子 '%s' 的 set_ref '%s' 是对象（不是集合），不能用于集合判定"
                          % (op, ref))
    return set(val)


def _compile(pattern, op):
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ConfigError("算子 '%s' 的正则表达式非法: %r (%s)" % (op, pattern, e))


def _tokens(fields, node):
    return _field(fields, node["field"]).split(node["sep"])


# ---------------------------------------------------------------------------
# 逻辑算子
# ---------------------------------------------------------------------------

def op_and(node, fields, ctx):
    """args 全部为真 → 真（空 args 恒真）。"""
    for sub in node.get("args") or []:
        if not evaluate(sub, fields, ctx):
            return False
    return True


def op_or(node, fields, ctx):
    """args 任一为真 → 真（空 args 恒假）。"""
    for sub in node.get("args") or []:
        if evaluate(sub, fields, ctx):
            return True
    return False


def op_not(node, fields, ctx):
    """args 的「任一为真」取反（配置中 args 均为单元素）。"""
    for sub in node.get("args") or []:
        if evaluate(sub, fields, ctx):
            return False
    return True


# ---------------------------------------------------------------------------
# 空值 / 整数
# ---------------------------------------------------------------------------

def op_is_null(node, fields, ctx):
    return _field(fields, node["field"]) == ""


def op_not_null(node, fields, ctx):
    return _field(fields, node["field"]) != ""


def op_is_int(node, fields, ctx):
    return _as_int(_field(fields, node["field"])) is not None


def op_not_int(node, fields, ctx):
    return _as_int(_field(fields, node["field"])) is None


# ---------------------------------------------------------------------------
# 范围
# ---------------------------------------------------------------------------

def op_in_range(node, fields, ctx):
    """落在 [min,max] 闭区间（边界可省略）。

    不可解析为整数的值一律返回 False —— 「无法比较」不等于「在范围内」。
    scoring 的 F1 依赖此语义：空/垃圾时间戳不得被算作范围合规。
    """
    v = _as_int(_field(fields, node["field"]))
    if v is None:
        return False
    lo, hi = node.get("min"), node.get("max")
    if lo is not None and v < lo:
        return False
    if hi is not None and v > hi:
        return False
    return True


def op_out_of_range(node, fields, ctx):
    """超出 [min,max]（边界可省略）。

    不可解析为整数的值返回 False：非整数的处置属于 is_int/not_int 系规则
    （本例中 R3 先于 R5 执行），此处不重复判罚。
    """
    v = _as_int(_field(fields, node["field"]))
    if v is None:
        return False
    lo, hi = node.get("min"), node.get("max")
    if lo is not None and v < lo:
        return True
    if hi is not None and v > hi:
        return True
    return False


# ---------------------------------------------------------------------------
# 集合 / 正则
# ---------------------------------------------------------------------------

def op_in_set(node, fields, ctx):
    return _field(fields, node["field"]) in _resolve_set(node, ctx, "in_set")


def op_not_in_set(node, fields, ctx):
    return _field(fields, node["field"]) not in _resolve_set(node, ctx, "not_in_set")


def op_regex_match(node, fields, ctx):
    """正则**全匹配**（说明 §7：regex_match = 全匹配）。"""
    return _compile(node["pattern"], "regex_match").fullmatch(
        _field(fields, node["field"])) is not None


def op_regex_mismatch(node, fields, ctx):
    return _compile(node["pattern"], "regex_mismatch").fullmatch(
        _field(fields, node["field"])) is None


# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------

def op_contains_any(node, fields, ctx):
    """字段包含 values 中任一子串。"""
    v = _field(fields, node["field"])
    for sub in node.get("values") or []:
        if sub in v:
            return True
    return False


def op_has_leading_trailing_space(node, fields, ctx):
    v = _field(fields, node["field"])
    return v != v.strip()


# ---------------------------------------------------------------------------
# 年份 / 时间戳单位
# ---------------------------------------------------------------------------

def _year_of(node, fields):
    """返回标题中的年份 int；无法解析返回 None。"""
    pattern = node.get("pattern", r"\((\d{4})\)\s*$")
    m = _compile(pattern, node.get("op", "year_valid")).search(
        _field(fields, node["field"]))
    if not m:
        return None
    if not m.groups():
        raise ConfigError("算子 '%s' 的 pattern 必须含一个捕获组（年份）: %r"
                          % (node.get("op"), pattern))
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def op_year_valid(node, fields, ctx):
    """标题年份可解析且落在 [min,max]（说明 §7）。"""
    y = _year_of(node, fields)
    if y is None:
        return False
    return node.get("min", 0) <= y <= node.get("max", 9999)


def op_year_invalid(node, fields, ctx):
    """年份缺失或异常（与 year_valid 互补）。"""
    return not op_year_valid(node, fields, ctx)


def op_timestamp_unit_ms(node, fields, ctx):
    """时间戳 >= threshold 判定为毫秒。

    不可解析为整数的值返回 False —— scoring 的 S3 分母限定为 is_int(Timestamp)，
    非整数不属于「单位不一致」。
    """
    v = _as_int(_field(fields, node["field"]))
    if v is None:
        return False
    return v >= node["threshold"]


# ---------------------------------------------------------------------------
# 结构（依赖 ctx["raw_line"]）
# ---------------------------------------------------------------------------

def _expected_count(node, ctx, op):
    exp = node["expected"]
    if isinstance(exp, dict):
        table = ctx.get("table") if isinstance(ctx, dict) else None
        if table is None:
            raise ConfigError("算子 '%s' 的 expected 按表映射，需要 ctx['table']" % op)
        if table not in exp:
            raise ConfigError("算子 '%s' 的 expected 未定义表 '%s'" % (op, table))
        return exp[table]
    return exp


def _raw_line(ctx, op):
    if not isinstance(ctx, dict) or "raw_line" not in ctx:
        raise ConfigError("算子 '%s' 需要 ctx['raw_line']" % op)
    return ctx["raw_line"]


def op_field_count_ne(node, fields, ctx):
    """按 sep 切分后字段数 ≠ expected（expected 可为按表映射的对象）。"""
    raw = _raw_line(ctx, "field_count_ne")
    return len(raw.split(node["sep"])) != _expected_count(node, ctx, "field_count_ne")


def op_field_count_eq(node, fields, ctx):
    raw = _raw_line(ctx, "field_count_eq")
    return len(raw.split(node["sep"])) == _expected_count(node, ctx, "field_count_eq")


def op_foreign_delimiter(node, fields, ctx):
    """行内出现非法分隔符**且不具备合法 valid_sep 结构**。

    判定式刻意收窄为「整行切分后只有 1 段（即根本不含 valid_sep）且含任一非法分隔符」，
    理由是配置与实测数字要求 P2 与 P3 严格互补：
      * '927::Women, The (1939)::Comedy'        标题自带逗号，但 '::' 结构合法 → 不是 P2
      * '1729::...::Crime|Drama::EXTRA_FIELD'   含 '|' 但 '::' 结构存在 → 属 P3，不是 P2
      * '25,688,4,978133460'                    无 '::' 且含 ',' → P2
    若改成「含非法分隔符且字段数不对」，上述第二例会被 P2 抢走，
    导致 P2 偏大、P3 偏小（实测：P2=6075 / P3=7606 将不再成立）。
    """
    raw = _raw_line(ctx, "foreign_delimiter")
    valid_sep = node["valid_sep"]
    if len(raw.split(valid_sep)) > 1:
        return False
    for d in node.get("delimiters") or []:
        if d and d in raw:
            return True
    return False


# ---------------------------------------------------------------------------
# 多值字段 token
# ---------------------------------------------------------------------------

def op_token_in_set(node, fields, ctx):
    """全部 token 都属于集合（空 token 视为不属于 → 返回 False）。"""
    s = _resolve_set(node, ctx, "token_in_set")
    return all(t in s for t in _tokens(fields, node))


def op_token_not_in_set(node, fields, ctx):
    """存在不属于集合的 token。"""
    s = _resolve_set(node, ctx, "token_not_in_set")
    return any(t not in s for t in _tokens(fields, node))


def op_token_empty_or_duplicate(node, fields, ctx):
    """存在空 token 或重复 token。"""
    toks = _tokens(fields, node)
    if any(t == "" for t in toks):
        return True
    return len(set(toks)) != len(toks)


# ---------------------------------------------------------------------------
# 聚合类（依赖 ctx 预计算）
# ---------------------------------------------------------------------------

def op_duplicate_by(node, fields, ctx):
    """该记录的业务键在本表中存在重复。"""
    keys = _need(ctx, "dup_group_keys", "duplicate_by")
    k = tuple(_field(fields, x) for x in node["keys"])
    return k in keys


def op_conflict_by(node, fields, ctx):
    """该记录所处的键分组内 value 存在多个不同取值。"""
    keys = _need(ctx, "conflict_group_keys", "conflict_by")
    k = tuple(_field(fields, x) for x in node["key"])
    return k in keys


def op_value_collision(node, fields, ctx):
    """同一 value 对应 >=min_keys 个不同 key（如同名不同 MovieID）。"""
    vals = _need(ctx, "value_collision_values", "value_collision")
    return _field(fields, node["value"]) in vals


def _dim_set(ctx, dim, op, key):
    d = _need(ctx, key, op)
    if not isinstance(d, dict) or dim not in d:
        raise ConfigError("算子 '%s' 需要 ctx['%s']['%s']" % (op, key, dim))
    return d[dim]


def op_ref_missing(node, fields, ctx):
    dim = node["dimension"]
    return _field(fields, node["field"]) not in _dim_set(ctx, dim, "ref_missing", "dim_keys")


def op_ref_exists(node, fields, ctx):
    dim = node["dimension"]
    return _field(fields, node["field"]) in _dim_set(ctx, dim, "ref_exists", "dim_keys")


def op_ref_unused(node, fields, ctx):
    dim = node["dimension"]
    unused = _dim_set(ctx, dim, "ref_unused", "ref_unused_keys")
    return _field(fields, node["key_field"]) in unused


def op_group_anomaly(node, fields, ctx):
    """该记录所属分组中满足 where 的条数 >= min_matches。

    「满足 where 的条数」由作业侧/pipeline 预先聚合（Streaming 下是 stats_marks 作业），
    本算子只做阈值比较，保证本地与集群同一套判定。
    """
    counts = _need(ctx, "group_match_counts", "group_anomaly")
    g = _field(fields, node["group_by"])
    return counts.get(g, 0) >= node["min_matches"]


# ---------------------------------------------------------------------------
# 算子库注册表（31 个）
# ---------------------------------------------------------------------------

OPERATORS = {
    # 逻辑
    "and": op_and, "or": op_or, "not": op_not,
    # 空值 / 整数
    "is_null": op_is_null, "not_null": op_not_null,
    "is_int": op_is_int, "not_int": op_not_int,
    # 范围
    "in_range": op_in_range, "out_of_range": op_out_of_range,
    # 集合
    "in_set": op_in_set, "not_in_set": op_not_in_set,
    # 正则
    "regex_match": op_regex_match, "regex_mismatch": op_regex_mismatch,
    # 文本
    "contains_any": op_contains_any,
    "has_leading_trailing_space": op_has_leading_trailing_space,
    # 年份 / 时间单位
    "year_valid": op_year_valid, "year_invalid": op_year_invalid,
    "timestamp_unit_ms": op_timestamp_unit_ms,
    # 结构
    "field_count_ne": op_field_count_ne, "field_count_eq": op_field_count_eq,
    "foreign_delimiter": op_foreign_delimiter,
    # token
    "token_in_set": op_token_in_set, "token_not_in_set": op_token_not_in_set,
    "token_empty_or_duplicate": op_token_empty_or_duplicate,
    # 聚合
    "duplicate_by": op_duplicate_by, "conflict_by": op_conflict_by,
    "value_collision": op_value_collision,
    # 跨表引用
    "ref_missing": op_ref_missing, "ref_exists": op_ref_exists, "ref_unused": op_ref_unused,
    # 分组异常
    "group_anomaly": op_group_anomaly,
}

# 启动期自检：算子库与配置校验白名单必须完全一致
assert set(OPERATORS) == set(OPS), (
    "算子库与 config_loader.OPS 不一致: %s" % (set(OPERATORS) ^ set(OPS)))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def evaluate(expr, fields, ctx):
    """求值一个 detect 表达式 AST。

    expr   —— 表达式节点字典；常量 True/False 直接返回（scoring 里有 `"where": true`）
    fields —— 解析后的列 {列名: 字符串值}
    ctx    —— 见模块 docstring
    """
    if expr is True:
        return True
    if expr is False or expr is None:
        return False
    if not isinstance(expr, dict):
        raise ConfigError("无法求值的检测表达式（既不是 True/False 也不是对象）: %r" % (expr,))
    op = expr.get("op")
    fn = OPERATORS.get(op)
    if fn is None:
        raise ConfigError("未知算子 '%s'；算子库为封闭白名单，扩展需升级版本号" % (op,))
    return bool(fn(expr, fields, ctx))
