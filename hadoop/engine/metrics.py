# -*- coding: utf-8 -*-
"""engine/metrics.py —— 五维评分引擎（plan.md §4.5；config/scoring_scheme.v1.json）。

指标、权重、公式全部来自配置，本模块只负责**解释**它们，不含任何硬编码阈值。

输入 dataset 形状::

    {
      "tables": {
        "ratings": {"parsed": [{字段: 值}, ...], "raw_lines": int, "schema": [字段名]},
        "users":   {...},
        "movies":  {...},
      }
    }

`parsed` 的含义随「侧」不同（plan §5.2）：
  * raw 侧  —— 字段数正确的行（坏行不在其中），分母用**原始行数**（含坏行）
  * cleaned 侧 —— 清洗后记录，行数 = 记录数

`raw_lines` 同理：raw 侧是非空原始行数，cleaned 侧是清洗后行数（= 记录数）。
C2/C3/S1 的分母都取 `raw_lines`，因此两侧口径由调用方给定，本模块不猜。

F2（覆盖新鲜度）不是比率而是直接得分，按 measure=freshness 单独计算。
"""
from engine.config_loader import ConfigError
from engine.operators import evaluate

__all__ = ["compute_metrics", "dimension_scores", "composite_score", "finalize",
           "aggregate_counts", "metrics_from_counts", "count_keys", "AGGS",
           "SCOPED_TABLES"]

#: scope="all" 时的表集合与求和顺序（确定性）
SCOPED_TABLES = ("ratings", "users", "movies")

#: 已实现的聚合算子（scoring 的 agg 白名单；配置校验侧同步登记）
AGGS = {
    "count", "valid_field_count", "field_count", "nonempty_field_count",
    "record_complete_count", "distinct_key_count", "distinct_row_count",
    "token_count", "dup_group_count",
}


# ---------------------------------------------------------------------------
# dataset 访问
# ---------------------------------------------------------------------------

def _tables_of(ds):
    t = ds.get("tables")
    if not isinstance(t, dict) or not t:
        raise ConfigError("dataset 缺少 'tables'")
    return t


def _schema_of(ds, table):
    tb = _tables_of(ds).get(table)
    if tb is None:
        raise ConfigError("dataset 缺少表 '%s'" % table)
    return tb.get("schema") or []


def _parsed(ds, table):
    if table not in _tables_of(ds):
        raise ConfigError("dataset 缺少表 '%s'" % table)
    return _tables_of(ds)[table].get("parsed") or []


def _targets(ds, spec):
    """解析作用范围：spec 里的 table / tables / scope='all'。

    `tables` 有两种合法形态：表名字符串列表（U3 的分母）与对象列表
    `[{table,key}]`（U3 的分子、S4）；这里统一归一为表名列表。
    """
    if spec.get("table"):
        return [spec["table"]]
    if spec.get("tables"):
        return [t if isinstance(t, str) else t["table"] for t in spec["tables"]]
    if spec.get("scope") == "all":
        return [t for t in SCOPED_TABLES if t in _tables_of(ds)]
    raise ConfigError("聚合 %r 未声明作用范围（table / tables / scope）"
                      % (spec.get("agg"),))


def _ctx(base, table):
    c = dict(base or {})
    c["table"] = table
    return c


# ---------------------------------------------------------------------------
# 各聚合算子
# ---------------------------------------------------------------------------

def _agg_count(ds, spec, base):
    on = spec.get("on", "parsed_records")
    where = spec.get("where", True)
    if on == "raw_lines":
        return sum(_tables_of(ds)[t].get("raw_lines", 0) for t in _targets(ds, spec))
    if on != "parsed_records":
        raise ConfigError("count 的 on 只支持 parsed_records / raw_lines，得到 %r" % (on,))
    n = 0
    for t in _targets(ds, spec):
        ctx = _ctx(base, t)
        for rec in _parsed(ds, t):
            if evaluate(where, rec, ctx):
                n += 1
    return n


def _agg_valid_field_count(ds, spec, base):
    """字段级合法性计数：符合 condition 的 (记录 × 字段) 对数。"""
    n = 0
    for t in _targets(ds, spec):
        ctx = _ctx(base, t)
        for rec in _parsed(ds, t):
            for fs in spec.get("fields") or []:
                if evaluate(fs["condition"], rec, ctx):
                    n += 1
    return n


def _agg_field_count(ds, spec, base):
    """(记录 × 字段) 总数：fields 给定则按给定字段数，否则按该表全字段数。"""
    names = spec.get("fields")
    n = 0
    for t in _targets(ds, spec):
        k = len(names) if names else len(_schema_of(ds, t))
        n += k * len(_parsed(ds, t))
    return n


def _agg_nonempty_field_count(ds, spec, base):
    n = 0
    for t in _targets(ds, spec):
        for rec in _parsed(ds, t):
            for f in _schema_of(ds, t):
                if rec.get(f, "") != "":
                    n += 1
    return n


def _agg_record_complete_count(ds, spec, base):
    n = 0
    for t in _targets(ds, spec):
        for rec in _parsed(ds, t):
            if all(rec.get(f, "") != "" for f in _schema_of(ds, t)):
                n += 1
    return n


def _agg_distinct_key_count(ds, spec, base):
    n = 0
    for tspec in spec.get("tables") or [spec]:
        t = tspec["table"]
        keys = tspec["key"]
        n += len(set(tuple(rec.get(f, "") for f in keys) for rec in _parsed(ds, t)))
    return n


def _agg_distinct_row_count(ds, spec, base):
    """全区不重复行数 = Σ(各表 distinct 解析记录) + Σ(各表无法解析的行数)。

    为什么要把无法解析的行加上：U2 问的是「有多少行不是别人的冗余副本」。
    raw 侧无法解析的行（P2/P3 隔离的 13,681 条）不可能是任何已解析记录的副本，
    所以它们各自算一条不重复行。等价写法：U2 = 1 - Σ(n_t - distinct_t) / raw_lines，
    即参考原型的 `extra` 只统计解析记录内部的重复。
    不这么写，U2 会把 13,681 条坏行当成「重复」而低估完整度
    （实测 91.32% vs 黄金 92.50%）。
    cleaned 侧 raw_lines == len(parsed)，该项自然为 0。
    """
    n = 0
    for t in _targets(ds, spec):
        tb = _tables_of(ds)[t]
        recs = tb.get("parsed") or []
        schema = tb.get("schema") or []
        n += len(set(tuple(rec.get(f, "") for f in schema) for rec in recs))
        n += max(0, tb.get("raw_lines", 0) - len(recs))
    return n


def _inherit_token_where(node, field, sep):
    """A6 的 where 省略了 field/sep（从 agg 继承），求值前递归补齐。

    配置里 `token_count.on=Genres, sep='|'` + `where={op: token_in_set, set_ref: genres}`
    是合法的声明式写法（校验侧允许），但算子按 (field, sep) 取 token，故此处补齐。
    """
    if isinstance(node, list):
        return [_inherit_token_where(x, field, sep) for x in node]
    if not isinstance(node, dict):
        return node
    out = dict(node)
    if out.get("op") in ("token_in_set", "token_not_in_set", "token_empty_or_duplicate"):
        out.setdefault("field", field)
        out.setdefault("sep", sep)
    for k in ("args", "where"):
        if k in out:
            out[k] = _inherit_token_where(out[k], field, sep)
    return out


def _agg_token_count(ds, spec, base):
    field, sep = spec["field"], spec["sep"]
    where = _inherit_token_where(spec.get("where", True), field, sep)
    n = 0
    for t in _targets(ds, spec):
        ctx = _ctx(base, t)
        for rec in _parsed(ds, t):
            for tok in rec.get(field, "").split(sep):
                if evaluate(where, {field: tok}, ctx):
                    n += 1
    return n


def _value_tuple(rec, val):
    if val is None:
        return ()
    if isinstance(val, str):
        return (rec.get(val, ""),)
    return tuple(rec.get(f, "") for f in val)


def _agg_dup_group_count(ds, spec, base):
    """重复分组数。

    conflict_free=True 时只统计「组内 value 完全一致」的组（S4 的分子），
    否则统计全部重复组（S4 的分母）。
    """
    conflict_free = bool(spec.get("conflict_free"))
    total = 0
    for tspec in spec.get("tables") or [spec]:
        t = tspec["table"]
        keys = tspec["key"]
        val = tspec.get("value")
        size, vals = {}, {}
        for rec in _parsed(ds, t):
            k = tuple(rec.get(f, "") for f in keys)
            size[k] = size.get(k, 0) + 1
            vals.setdefault(k, set()).add(_value_tuple(rec, val))
        for k, c in size.items():
            if c <= 1:
                continue
            if conflict_free and len(vals[k]) != 1:
                continue
            total += 1
    return total


_AGG_FUNCS = {
    "count": _agg_count,
    "valid_field_count": _agg_valid_field_count,
    "field_count": _agg_field_count,
    "nonempty_field_count": _agg_nonempty_field_count,
    "record_complete_count": _agg_record_complete_count,
    "distinct_key_count": _agg_distinct_key_count,
    "distinct_row_count": _agg_distinct_row_count,
    "token_count": _agg_token_count,
    "dup_group_count": _agg_dup_group_count,
}

assert set(_AGG_FUNCS) == AGGS


# ---------------------------------------------------------------------------
# freshness（F2）
# ---------------------------------------------------------------------------

def _dig(ctx, path, default=None):
    """按点号路径取值，如 'reference_domains.timestamp.max'。"""
    cur = ctx
    for part in str(path).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def freshness_max_ts(dataset, spec, base=None):
    """新鲜度用的「最新时间戳」：只统计落在数据集声明范围内的时间戳。

    范围外的值由 F1 负责，不在这里奖励或惩罚（2100 年那种注入值不应把 F2 拉满）。
    返回 None 表示「没有可用时间戳」。
    """
    p = spec.get("params") or {}
    table, field = p.get("table"), p.get("field")
    rng = _dig(base or {}, "reference_domains.timestamp") or {}

    from engine.operators import _as_int
    best = None
    for rec in _parsed(dataset, table):
        v = _as_int(rec.get(field, ""))
        if v is None:
            continue
        if "min" in rng and v < rng["min"]:
            continue
        if "max" in rng and v > rng["max"]:
            continue
        if best is None or v > best:
            best = v
    return best


def freshness_score(max_ts, spec, base=None):
    """由「最新时间戳」算新鲜度得分（与本地/集群共用的唯一公式）。"""
    p = spec.get("params") or {}
    ref = _dig(base or {}, p.get("reference"))
    if ref is None:
        raise ConfigError("freshness 的 reference %r 无法从 ctx 解析" % (p.get("reference"),))
    if max_ts is None:
        return 0.0
    full = float(p.get("full_score_days", 30))
    zero = float(p.get("zero_score_days", 180))
    gap_days = (ref - max_ts) / 86400.0
    if gap_days <= full:
        return 1.0
    if zero <= full:
        return 0.0
    return max(0.0, 1.0 - (gap_days - full) / (zero - full))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _target_key(spec):
    """作用范围的规范化指纹，用于判断分子/分母是否作用于同一总体。"""
    if spec.get("table"):
        return (spec["table"],)
    if spec.get("tables"):
        return tuple(t if isinstance(t, str) else t["table"] for t in spec["tables"])
    if spec.get("scope") == "all":
        return ("all",)
    return None


def _numerator_spec(num, den):
    """比率的分子必须落在分母的总体内（分子 ⊆ 分母）。

    配置里 S3 是唯一分子与分母都带 where 的比率：
      分子 = 非毫秒时间戳，分母 = 可解析时间戳
    若把分子直接对**全部**解析记录计数，不可解析的时间戳（3,375 条 'five'）会因为
    `timestamp_unit_ms` 对非整数返回 False 而被算进分子，却不在分母里，
    得到 99.11%；这正是「比率可能超过 1」的错误建模。
    把分子的 where 与分母的 where 取合并后，S3 = 98.81%，与黄金值一致。
    """
    if num.get("agg") != "count" or den.get("agg") != "count":
        return num
    if num.get("on", "parsed_records") != den.get("on", "parsed_records"):
        return num
    if _target_key(num) != _target_key(den):
        return num
    dw, nw = den.get("where", True), num.get("where", True)
    if dw is True or nw is True or dw == {} or nw == {}:
        return num
    out = dict(num)
    out["where"] = {"op": "and", "args": [dw, nw]}
    return out


def _run_agg(ds, spec, base):
    """按名字取聚合算子；未登记的名字必须报错（配置校验侧的白名单之外一律拒绝）。"""
    name = spec.get("agg")
    fn = _AGG_FUNCS.get(name)
    if fn is None:
        raise ConfigError("未登记的聚合算子 %r（登记表：%s）"
                          % (name, ", ".join(sorted(AGGS))))
    return fn(ds, spec, base)


def count_keys(schemes):
    """本次评分需要收集的计数键列表（本地与集群必须完全一致）。

    `"<mid>.num"` / `"<mid>.den"` 是比率的分子分母，`"<mid>.max_ts"` 是新鲜度的
    最新时间戳。集群侧三个评分作业按这套键产出计数，再由 metrics_from_counts 统一
    算出分数 —— **公式只写一遍**，所以「本地 = 集群」不需要靠人工比对维护。
    """
    keys = []
    for dim in schemes.scoring["dimensions"]:
        for spec in dim["metrics"]:
            mid = spec["id"]
            _check_measure(spec)
            if spec.get("measure", "ratio") == "freshness":
                keys.append(mid + ".max_ts")
            else:
                keys.append(mid + ".num")
                keys.append(mid + ".den")
    return keys


def _check_measure(spec):
    measure = spec.get("measure", "ratio")
    if measure not in ("ratio", "freshness"):
        raise ConfigError("未登记的 measure %r（规则 %s 的登记表：ratio / freshness）"
                          % (measure, spec.get("id")))
    return measure


def aggregate_counts(dataset, schemes, ctx=None):
    """把 dataset 归约成 `{计数键: 数值}` —— 本地 runner 的「measure」阶段。"""
    base = ctx or {}
    counts = {}
    for dim in schemes.scoring["dimensions"]:
        for spec in dim["metrics"]:
            mid = spec["id"]
            if _check_measure(spec) == "freshness":
                counts[mid + ".max_ts"] = freshness_max_ts(dataset, spec, base)
                continue
            counts[mid + ".num"] = _run_agg(
                dataset, _numerator_spec(spec["numerator"], spec["denominator"]), base)
            counts[mid + ".den"] = _run_agg(dataset, spec["denominator"], base)
    return counts


def metrics_from_counts(counts, schemes, ctx=None):
    """由计数算出 18 个指标值（0..1）—— 唯一的公式出口。"""
    base = ctx or {}
    out = {}
    for dim in schemes.scoring["dimensions"]:
        for spec in dim["metrics"]:
            mid = spec["id"]
            if spec.get("measure", "ratio") == "freshness":
                out[mid] = freshness_score(counts.get(mid + ".max_ts"), spec, base)
                continue
            den = counts.get(mid + ".den", 0)
            if not den:
                # 分母为 0 = 该侧无适用记录；按「无缺陷」记满分（与参考原型一致）
                out[mid] = 1.0
            else:
                out[mid] = counts.get(mid + ".num", 0) / float(den)
    return out


def compute_metrics(dataset, schemes, ctx=None):
    """逐指标求值，返回 {metric_id: 0..1}（未取整，便于加权汇总）。

    刻意实现为「先归约成计数、再由计数算分」两步 —— 与集群侧
    score_measure / score_groupstats → score_finalize 完全同构。
    这样就不存在「本地一套公式、集群另一套公式」的漂移空间。
    """
    return metrics_from_counts(aggregate_counts(dataset, schemes, ctx), schemes, ctx)


def dimension_scores(metric_values, schemes):
    """维度分 = 维度内指标加权平均 × 100（score_scale.max）。"""
    scale_max = float((schemes.scoring.get("score_scale") or {}).get("max", 100))
    out = {}
    for dim in schemes.scoring["dimensions"]:
        num = den = 0.0
        for spec in dim["metrics"]:
            w = float(spec.get("weight", 1.0))
            num += w * metric_values[spec["id"]]
            den += w
        out[dim["id"]] = (num / den) * scale_max if den else 0.0
    return out


def composite_score(dim_scores, schemes):
    """综合分 = 五维加权平均（权重和必须为 1，由配置校验保证）。"""
    num = den = 0.0
    for dim in schemes.scoring["dimensions"]:
        w = float(dim.get("weight", 1.0))
        num += w * dim_scores[dim["id"]]
        den += w
    return num / den if den else 0.0


def _round(value, schemes):
    digits = int((schemes.scoring.get("score_scale") or {}).get("rounding", 2))
    return round(value, digits)


def finalize(metric_values, schemes):
    """组装接口文档 result.scores 的单侧结构。

    指标按 score_scale 换算为 0..100 并取整；维度分与综合分由**未取整**的指标算出
    后再取整，避免中间取整造成的偏差累积（plan §5.2：最终分数必须由 finalize 产出）。
    """
    dims = dimension_scores(metric_values, schemes)
    return {
        "metrics": {k: _round(v * 100.0, schemes) for k, v in metric_values.items()},
        "dimensions": {k: _round(v, schemes) for k, v in dims.items()},
        "composite": _round(composite_score(dims, schemes), schemes),
    }
