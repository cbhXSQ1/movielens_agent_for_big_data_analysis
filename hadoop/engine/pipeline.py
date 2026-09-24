# -*- coding: utf-8 -*-
"""engine/pipeline.py —— 本地全流程（plan.md §4.4、§5.1、§6）。

`run_local` 在单进程内按 §5.1 的作业顺序跑完清洗+评分，产出与 Hadoop **相同**的
目录结构与 JSON，因此「本地黄金测试 = 集群结果」这条不变量可以被反复验证。

执行顺序（§5.1：先维表后评分表）::

    users_normalize    P1 P2 P3 U1 U2 U3      行级：解析 / 校验 / 修复
    users_resolve      U5                      按键去重 / 字段级合并
    movies_normalize   P1 P2 P3 M1 M2
    movies_resolve     M4
    movies_residual    M3 M6 M7 M9
    ratings_validate   P2 P3 R1 R2 R3 R4 R5
    ratings_dedupe     R6
    ratings_cross      X1 X2                   依赖清洗后维表
    stats_marks        R9 M8 X3                报告类，需结算后聚合

关键口径（全部由黄金测试锁定）：
  * **R4 必须先于 R5**：毫秒值本身就是「越界整数」，若先判范围会把 13,503 条毫秒记录
    误隔离（实测：R4 前越界 25,506 → R4 后 12,003）。
  * R1/R2/R3 的判定集互不重叠（实测交集为空），故「逐行顺序结算」与「按原始数据统计」
    得到相同数字；这也正是契约里 by_rule 说「结算后」而数字仍与原始计数一致的原因。
  * 只有 action=quarantine 的命中进入隔离区；R6/M4/U5 属**去重**，计入 counts.dedupe
    （agent-interface.md §4.5 的 by_rule 不含 R6/M4/U5）。
  * 标记类规则（M6/M7/M8/R8/R9）只统计不落盘改文件：cleaned 三表保持纯 `::` 格式，
    否则下游 split 与内容哈希都会被污染。
  * cleaned 三表按业务键整数序排序输出（单 reducer 语义），保证重跑与集群哈希一致。

只用标准库；不依赖 Hadoop。
"""
import datetime
import io
import json
import os

from engine.actions import (apply_fix, make_quarantine_record, make_record,
                            resolve_records)
from engine.config_loader import ConfigError
from engine.metrics import compute_metrics, finalize
from engine.operators import evaluate

__all__ = ["run_local", "TABLE_SCHEMAS", "TABLE_FILES", "RAWE_TABLE_ORDER",
           "read_raw_table", "parse_record", "RuleBook"]

SEP = "::"

#: 三表字段（顺序即文件列序）。配置未声明表结构，此处按 ml-1m 数据格式固定。
TABLE_SCHEMAS = {
    "users": ["UserID", "Gender", "Age", "Occupation", "Zip-code"],
    "movies": ["MovieID", "Title", "Genres"],
    "ratings": ["UserID", "MovieID", "Rating", "Timestamp"],
}
TABLE_FILES = {"users": "users.dat", "movies": "movies.dat", "ratings": "ratings.dat"}

#: 维表先于评分表（§5.1）
TABLE_ORDER = ("users", "movies", "ratings")

_LINE_STAGES = ("parse", "validate_repair")
_GROUP_STAGE = "dedupe_resolve"
_RESIDUAL_STAGE = "residual_checks"
_CROSS_STAGE = "cross_table"
_REPORT_STAGE = "report"

#: 契约 counts.fix 的 4 个计数器（agent-interface.md §4.5）
FIX_COUNTERS = {
    ("P1", "decode_html_entity"): "P1_text",
    ("P1", "decode_double_encoding"): "P1_text",
    ("R4", "timestamp_ms_to_s"): "R4_ms",
    ("M2", "strip"): "M2_strip",
    ("U3", "zip_plus4_truncate"): "U3_zip_plus4",
}


# ---------------------------------------------------------------------------
# 读取与解析
# ---------------------------------------------------------------------------

def read_raw_table(path):
    """按 ISO-8859-1 读取，返回 [(文件中真实行号, 原始行)]；空行不计入。

    行号保留文件中的真实位置（跳过空行不改变编号），便于隔离记录回溯定位。
    """
    with io.open(path, "r", encoding="iso-8859-1", newline="") as fh:
        text = fh.read()
    out = []
    for i, line in enumerate(text.split("\n"), start=1):
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            continue
        out.append((i, line))
    return out


def parse_record(raw_line, table):
    """按 '::' 解析；字段数不符返回 None（该情形由 P3 隔离）。"""
    parts = raw_line.split(SEP)
    if len(parts) != len(TABLE_SCHEMAS[table]):
        return None
    return dict(zip(TABLE_SCHEMAS[table], parts))


# ---------------------------------------------------------------------------
# 计数器
# ---------------------------------------------------------------------------

class Counters(object):
    def __init__(self):
        self.quarantine = {}      # rule_id -> 结算后命中数（进契约）
        self.dedupe = {}          # table -> 移除数（进契约）
        self.fix = {}             # 契约计数器名 -> 次数（进契约）
        self.detail = {}          # 更细统计（进 stats.json，不进契约）
        self.marks = {}           # rule_id -> 被标记记录数
        self.groups = {}          # rule_id -> 命中分组/对象数

    def hit(self, rid, n=1):
        self.quarantine[rid] = self.quarantine.get(rid, 0) + n

    def mark(self, rid, n=1):
        self.marks[rid] = self.marks.get(rid, 0) + n

    def fixup(self, name, n=1):
        self.fix[name] = self.fix.get(name, 0) + n

    def bumped(self, key, n=1):
        self.detail[key] = self.detail.get(key, 0) + n


def counts_of(ctr, raw_lines, cleaned_counts):
    """组装契约 counts 结构（agent-interface.md §4.5）。"""
    return {
        "input": {
            "ratings_lines": raw_lines["ratings"],
            "users_lines": raw_lines["users"],
            "movies_lines": raw_lines["movies"],
        },
        "output": dict(cleaned_counts),
        "quarantine": {"total": sum(ctr.quarantine.values()),
                       "by_rule": dict(sorted(ctr.quarantine.items()))},
        "dedupe": dict(ctr.dedupe),
        "fix": dict(ctr.fix),
    }


# ---------------------------------------------------------------------------
# 规则索引
# ---------------------------------------------------------------------------

class RuleBook(object):
    """按表与阶段组织规则，提供 §5.1 的执行次序。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.rules = dict((r["id"], r) for r in cfg["rules"])
        self.stage_order = []
        for stage in cfg["pipeline"]:
            for rid in stage["rules"]:
                self.stage_order.append((stage["stage"], rid))

    def ids(self, table, stages):
        """按配置阶段顺序返回适用于该表的规则 id（table=all 的规则适用于所有表）。"""
        out = []
        for stage, rid in self.stage_order:
            if stage not in stages:
                continue
            r = self.rules[rid]
            if r.get("enabled", True) is False:
                continue
            if r["table"] in (table, "all"):
                out.append(rid)
        return out

    def get(self, rid):
        return self.rules[rid]


# ---------------------------------------------------------------------------
# ctx 构造
# ---------------------------------------------------------------------------

def _base_ctx(cfg):
    return {"reference_domains": cfg.get("reference_domains", {})}


def _rec_ctx(base, table, raw_line):
    c = dict(base)
    c["table"] = table
    c["raw_line"] = raw_line
    return c


# ---------------------------------------------------------------------------
# 行级阶段（parse / validate_repair）
# ---------------------------------------------------------------------------

def _line_stage(table, lines, book, base, task_id, processed_at, ctr):
    """逐行执行行级规则；命中 quarantine 即隔离并停止该行的后续判定。"""
    rule_ids = book.ids(table, _LINE_STAGES)
    kept, quarantined = [], []

    for line_no, raw in lines:
        rec = make_record(parse_record(raw, table) or {}, raw, line_no,
                          TABLE_FILES[table])
        dropped = False

        for rid in rule_ids:
            rule = book.get(rid)
            if not evaluate(rule["detect"], rec["fields"],
                            _rec_ctx(base, table, raw)):
                continue

            action = rule["action"]
            if action == "quarantine":
                ctr.hit(rid)
                quarantined.append(make_quarantine_record(
                    TABLE_FILES[table], line_no, raw, rid, rule["stage"],
                    rule.get("name", rid), task_id, processed_at))
                dropped = True
                break

            if action == "fix":
                before = dict(rec["fields"])
                rec, applied = apply_fix(rec, rule, book.cfg)
                for st in applied:
                    name = FIX_COUNTERS.get((rid, st))
                    if name:
                        ctr.fixup(name)
                if "blank_invalid_field" in applied:
                    blanks = sum(1 for f in before
                                 if before[f] != "" and rec["fields"].get(f, "") == "")
                    ctr.bumped("%s_blank_records" % rid)
                    ctr.bumped("%s_blank_fields" % rid, blanks)
                if rule.get("mark"):
                    ctr.mark(rid)
                continue

            if action == "check":
                ctr.bumped("%s_checked" % rid)
                continue

            raise ConfigError("阶段 %s 不支持 action=%r（规则 %s）"
                              % (rule["stage"], action, rid))

        if not dropped:
            kept.append(rec)

    return kept, quarantined


# ---------------------------------------------------------------------------
# 字段合法性判据（M4 / U5 的 dedupe_resolve）
# ---------------------------------------------------------------------------

def _bad_text_markers(scoring):
    """乱码标记取自配置（S2 numerator 的 contains_any.values），不在引擎里另立字面量。"""
    def walk(node):
        if isinstance(node, list):
            for x in node:
                got = walk(x)
                if got is not None:
                    return got
            return None
        if not isinstance(node, dict):
            return None
        if node.get("op") == "contains_any" and node.get("values"):
            return list(node["values"])
        for k in ("args", "where"):
            got = walk(node.get(k))
            if got is not None:
                return got
        return None

    for dim in scoring["dimensions"]:
        for m in dim["metrics"]:
            if m["id"] == "S2":
                got = walk(m["numerator"])
                if got is not None:
                    return got
    raise ConfigError("scoring 配置缺少 S2 的乱码标记，无法构造 M4 的字段合法性判据")


def _movie_field_specs(cfg, scoring):
    """M4「保留合法字段最多的记录：标题非空、无 [20XX]、无乱码、类型合法」。

    后两条带 not_null(Title) 前置（与参考原型同形）：空标题不应白得这 2 分，
    否则「空标题副本」与「坏年份副本」会排到同一档，M4 的取舍就失去依据。
    """
    bad = _bad_text_markers(scoring)
    return [
        {"field": "Title", "weight": 2,
         "condition": {"op": "not_null", "field": "Title"}},
        {"field": "Title", "weight": 1, "condition": {"op": "and", "args": [
            {"op": "not_null", "field": "Title"},
            {"op": "not", "args": [{"op": "contains_any", "field": "Title",
                                    "values": ["[20XX]"]}]}]}},
        {"field": "Title", "weight": 1, "condition": {"op": "and", "args": [
            {"op": "not_null", "field": "Title"},
            {"op": "not", "args": [{"op": "contains_any", "field": "Title",
                                    "values": bad}]}]}},
        {"field": "Genres", "weight": 1,
         "condition": {"op": "token_in_set", "field": "Genres", "sep": "|",
                       "set_ref": "genres"}},
    ]


def _user_field_specs(cfg):
    """U5「三属性非空 + 邮编合法」；邮编正则取自 reference_domains.zip.pattern。"""
    zip_pat = ((cfg.get("reference_domains") or {}).get("zip") or {}).get(
        "pattern", r"^\d{5}$")
    specs = [{"field": f, "weight": 1,
              "condition": {"op": "not_null", "field": f}}
             for f in ("Gender", "Age", "Occupation")]
    specs.append({"field": "Zip-code", "weight": 1,
                  "condition": {"op": "regex_match", "field": "Zip-code",
                                "pattern": zip_pat}})
    return specs


# ---------------------------------------------------------------------------
# 分组阶段（R6 / M4 / U5）
# ---------------------------------------------------------------------------

def _dedupe_keep(grp, rule):
    """action=dedupe：保留一条，其余移除。排序后取，保证确定性。"""
    keep = (rule.get("dedupe") or {}).get("keep") or rule["policy"]["value"]
    ordered = sorted(grp, key=lambda r: (r["raw_line"], r["line_no"]))
    if keep == "first":
        return ordered[0], ordered[1:]
    if keep == "latest":
        return ordered[-1], ordered[:-1]
    raise ConfigError("未登记的 dedupe.keep=%r（登记表：first / latest）" % (keep,))


def _group_stage(table, records, book, base, specs, task_id, processed_at, ctr):
    for rid in book.ids(table, (_GROUP_STAGE,)):
        rule = book.get(rid)
        keys = rule["detect"]["keys"]

        groups = {}
        for rec in records:
            groups.setdefault(tuple(rec["fields"].get(k, "") for k in keys), []).append(rec)

        nxt = []
        for _, grp in groups.items():
            if len(grp) == 1:
                nxt.append(grp[0])
                continue

            if rule["action"] == "dedupe":
                kept, rest = _dedupe_keep(grp, rule)
                dropped = [_as_drop(r, rid, table, task_id, processed_at,
                                    "同键重复，保留一条(policy=%s)"
                                    % ((rule.get("dedupe") or {}).get("keep", "first"),))
                           for r in rest]
                ctr.bumped("%s_dup_groups" % rid)
            elif rule["action"] == "dedupe_resolve":
                kept, dropped = resolve_records(grp, rule["policy"], specs, dict(base))
                ctr.bumped("%s_groups" % rid)
            else:
                raise ConfigError("阶段 %s 不支持 action=%r（规则 %s）"
                                  % (_GROUP_STAGE, rule["action"], rid))

            n = len(dropped)
            ctr.dedupe[table] = ctr.dedupe.get(table, 0) + n
            ctr.bumped("%s_removed" % rid, n)
            if kept is not None:
                nxt.append(kept)
        records = nxt
    return records


def _as_drop(rec, rid, table, task_id, processed_at, reason):
    return make_quarantine_record(TABLE_FILES[table], rec["line_no"], rec["raw_line"],
                                  rid, _GROUP_STAGE, reason, task_id, processed_at)


# ---------------------------------------------------------------------------
# 残留检查阶段（M3/M6/M7/M9、R7/R8）
# ---------------------------------------------------------------------------

def _conflict_keys(records, key_fields, value_field):
    """组内 value_field 出现多个不同取值的键集合。"""
    vals = {}
    for rec in records:
        k = tuple(rec["fields"].get(f, "") for f in key_fields)
        vals.setdefault(k, set()).add(rec["fields"].get(value_field, ""))
    return set(k for k, v in vals.items() if len(v) > 1)


def _residual_stage(table, records, book, base, task_id, processed_at, ctr):
    """逐规则串行过一遍；quarantine 会真的把记录移出，后续规则看到的是移除后的集合。"""
    quarantined = []
    for rid in book.ids(table, (_RESIDUAL_STAGE,)):
        rule = book.get(rid)
        ctx = dict(base)
        if rule["detect"].get("op") == "conflict_by":
            ctx["conflict_group_keys"] = _conflict_keys(
                records, rule["detect"]["key"], rule["detect"]["value"])
            ctr.groups[rid] = len(ctx["conflict_group_keys"])

        kept = []
        for rec in records:
            rctx = dict(ctx)
            rctx["raw_line"] = rec["raw_line"]
            rctx["table"] = table
            if not evaluate(rule["detect"], rec["fields"], rctx):
                kept.append(rec)
                continue

            action = rule["action"]
            if action == "quarantine":
                ctr.hit(rid)
                quarantined.append(make_quarantine_record(
                    TABLE_FILES[table], rec["line_no"], rec["raw_line"], rid,
                    rule["stage"], rule.get("name", rid), task_id, processed_at))
            elif action == "check":
                # check = 只报告不处置（M9），记录照常保留
                ctr.bumped("%s_checked" % rid)
                kept.append(rec)
            elif action == "mark":
                ctr.mark(rid)
                kept.append(rec)
            else:
                raise ConfigError("阶段 %s 不支持 action=%r（规则 %s）"
                                  % (_RESIDUAL_STAGE, action, rid))
        records = kept
    return records, quarantined


# ---------------------------------------------------------------------------
# 跨表阶段（X1/X2）
# ---------------------------------------------------------------------------

def _cross_stage(records, book, base, dim_keys, task_id, processed_at, ctr):
    rule_ids = book.ids("ratings", (_CROSS_STAGE,))
    kept, quarantined = [], []
    for rec in records:
        dropped = False
        for rid in rule_ids:
            rule = book.get(rid)
            ctx = dict(base)
            ctx["dim_keys"] = dim_keys
            ctx["raw_line"] = rec["raw_line"]
            ctx["table"] = "ratings"
            if not evaluate(rule["detect"], rec["fields"], ctx):
                continue
            ctr.hit(rid)
            quarantined.append(make_quarantine_record(
                TABLE_FILES["ratings"], rec["line_no"], rec["raw_line"], rid,
                rule["stage"], rule.get("name", rid), task_id, processed_at))
            dropped = True
            break
        if not dropped:
            kept.append(rec)
    return kept, quarantined


# ---------------------------------------------------------------------------
# 报告阶段（R9 / M8 / X3）：只统计与标记
# ---------------------------------------------------------------------------

def _report_stage(table, records, book, base, extra_ctx, ctr):
    for rid in book.ids(table, (_REPORT_STAGE,)):
        rule = book.get(rid)
        ctx = dict(base)
        ctx.update(extra_ctx.get(rid, {}))
        n = 0
        for rec in records:
            rctx = dict(ctx)
            rctx["raw_line"] = rec["raw_line"]
            rctx["table"] = table
            if evaluate(rule["detect"], rec["fields"], rctx):
                n += 1
        action = rule["action"]
        if action == "mark":
            ctr.mark(rid, n)
        elif action == "report":
            ctr.bumped("%s_reported" % rid, n)
        else:
            raise ConfigError("阶段 %s 不支持 action=%r（规则 %s）"
                              % (_REPORT_STAGE, action, rid))
    return records


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

_SORT_FIELDS = {"ratings": ("UserID", "MovieID", "Timestamp"),
                "users": ("UserID",),
                "movies": ("MovieID",)}


def _sort_key(table):
    fields = _SORT_FIELDS[table]

    def key(rec):
        f = rec["fields"]
        return tuple(int(f[k]) for k in fields)

    return key


def _write_dat(path, table, records):
    """cleaned 表：ISO-8859-1、'::' 分隔、每行一条；按业务键整数序排序。"""
    schema = TABLE_SCHEMAS[table]
    ordered = sorted(records, key=_sort_key(table))
    with io.open(path, "w", encoding="iso-8859-1", newline="\n") as fh:
        for rec in ordered:
            fh.write(SEP.join(rec["fields"].get(f, "") for f in schema))
            fh.write(u"\n")
    return len(ordered)


def _write_json(path, obj):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))
        fh.write(u"\n")


def _write_jsonl(path, rows):
    """隔离记录：一行一个 JSON 对象（UTF-8）。

    不用 '::' 拼接是因为 raw_line 本身含 '::'，拼接后无法无歧义还原；
    JSONL 可审计、可回溯，也便于 Streaming 作业直接输出。
    """
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False))
            fh.write(u"\n")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_local(raw_dir, schemes, out_dir, task_id, processed_at=None):
    """跑完清洗+评分并落盘，返回 stats（含契约 counts）。

    raw_dir —— 含 ratings.dat/users.dat/movies.dat 的目录
    out_dir —— 任务输出目录（不存在则创建）
    processed_at —— 隔离记录时间戳；显式传入可让重跑产物逐字节一致
    """
    cfg = schemes.rules
    book = RuleBook(cfg)
    base = _base_ctx(cfg)
    ctr = Counters()
    if processed_at is None:
        processed_at = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    # 1) 原始三表：行数 + 解析结果（解析结果只用于 before 评分，不参与清洗）
    raw_lines, parsed = {}, {}
    for table in TABLE_ORDER:
        lines = read_raw_table(os.path.join(raw_dir, TABLE_FILES[table]))
        raw_lines[table] = len(lines)
        parsed[table] = [r for r in (parse_record(raw, table) for _, raw in lines)
                         if r is not None]

    # 2) 行级阶段
    records, quarantine = {}, []
    for table in TABLE_ORDER:
        lines = read_raw_table(os.path.join(raw_dir, TABLE_FILES[table]))
        records[table], q = _line_stage(table, lines, book, base, task_id,
                                        processed_at, ctr)
        quarantine.extend(q)

    # 3) 分组阶段（维表先于评分表）
    for table in ("users", "movies"):
        specs = (_user_field_specs(cfg) if table == "users"
                 else _movie_field_specs(cfg, schemes.scoring))
        records[table] = _group_stage(table, records[table], book, base, specs,
                                      task_id, processed_at, ctr)

    # 4) 电影残留检查
    records["movies"], q = _residual_stage("movies", records["movies"], book, base,
                                           task_id, processed_at, ctr)
    quarantine.extend(q)

    # 5) 评分表：去重 → 跨表 → 残留
    records["ratings"] = _group_stage("ratings", records["ratings"], book, base, None,
                                      task_id, processed_at, ctr)
    dim_keys = {"users": set(r["fields"].get("UserID", "") for r in records["users"]),
                "movies": set(r["fields"].get("MovieID", "") for r in records["movies"])}
    records["ratings"], q = _cross_stage(records["ratings"], book, base, dim_keys,
                                         task_id, processed_at, ctr)
    quarantine.extend(q)
    records["ratings"], q = _residual_stage("ratings", records["ratings"], book, base,
                                            task_id, processed_at, ctr)
    quarantine.extend(q)

    # 6) 报告阶段的聚合上下文
    #    R9 的分组计数必须取自**原始**数据：非法评分已被 R2 隔离，清洗后无从计数，
    #    这正是「标记注入嫌疑用户」这类行为统计需要独立于清洗结果的原因。
    group_match_counts = {}
    r9_where = book.get("R9")["detect"]["where"]
    for _, raw in read_raw_table(os.path.join(raw_dir, TABLE_FILES["ratings"])):
        f = parse_record(raw, "ratings")
        if not f:
            continue
        if evaluate(r9_where, f, _rec_ctx(base, "ratings", raw)):
            group_match_counts[f["UserID"]] = group_match_counts.get(f["UserID"], 0) + 1

    title_ids = {}
    for rec in records["movies"]:
        title_ids.setdefault(rec["fields"].get("Title", ""), set()).add(
            rec["fields"].get("MovieID", ""))
    colliding = set(t for t, ids in title_ids.items() if t != "" and len(ids) >= 2)

    rated_users = set(r["fields"].get("UserID", "") for r in records["ratings"])
    rated_movies = set(r["fields"].get("MovieID", "") for r in records["ratings"])
    never_rated = {
        "users": set(r["fields"].get("UserID", "") for r in records["users"]) - rated_users,
        "movies": set(r["fields"].get("MovieID", "") for r in records["movies"]) - rated_movies,
    }
    extra_ctx = {
        "R9": {"group_match_counts": group_match_counts},
        "M8": {"value_collision_values": colliding},
        "X3": {"ref_unused_keys": never_rated},
    }
    for table in TABLE_ORDER:
        records[table] = _report_stage(table, records[table], book, base, extra_ctx, ctr)

    ctr.groups["R9"] = len(set(u for u, c in group_match_counts.items() if c >= 1000))
    ctr.groups["M8"] = len(colliding)
    ctr.groups["X3_users"] = len(never_rated["users"])
    ctr.groups["X3_movies"] = len(never_rated["movies"])

    # 7) 落盘
    data_version = cfg["data_version"]["id"]
    cleaned_dir = os.path.join(out_dir, "cleaned", data_version)
    qdir = os.path.join(out_dir, "quarantine", data_version)
    mdir = os.path.join(out_dir, "metrics")
    for d in (cleaned_dir, qdir, mdir):
        if not os.path.isdir(d):
            os.makedirs(d)

    cleaned_counts = {}
    for table in TABLE_ORDER:
        cleaned_counts[table] = _write_dat(
            os.path.join(cleaned_dir, TABLE_FILES[table]), table, records[table])

    by_file = {}
    for rec in quarantine:
        by_file.setdefault(rec["source_file"], []).append(rec)
    for table in TABLE_ORDER:
        rows = sorted(by_file.get(TABLE_FILES[table], []),
                      key=lambda r: (r["line_no"], r["rule_id"]))
        _write_jsonl(os.path.join(qdir, TABLE_FILES[table]), rows)

    # 8) 评分：before = 原始（分母含坏行） / after = 清洗后（行 = 记录）
    before_ds = {"tables": {
        t: {"parsed": parsed[t], "raw_lines": raw_lines[t], "schema": TABLE_SCHEMAS[t]}
        for t in TABLE_ORDER}}
    before_ctx = dict(base)
    before_ctx["dim_keys"] = {
        "users": set(r.get("UserID", "") for r in parsed["users"]),
        "movies": set(r.get("MovieID", "") for r in parsed["movies"]),
    }
    after_ds = {"tables": {
        t: {"parsed": [r["fields"] for r in records[t]],
            "raw_lines": cleaned_counts[t], "schema": TABLE_SCHEMAS[t]}
        for t in TABLE_ORDER}}
    after_ctx = dict(base)
    after_ctx["dim_keys"] = dim_keys

    before = finalize(compute_metrics(before_ds, schemes, before_ctx), schemes)
    after = finalize(compute_metrics(after_ds, schemes, after_ctx), schemes)
    digits = int((schemes.scoring.get("score_scale") or {}).get("rounding", 2))
    scores = {
        "before": dict(before["dimensions"], composite=before["composite"]),
        "after": dict(after["dimensions"], composite=after["composite"]),
        "delta": dict((k, round(after["dimensions"][k] - before["dimensions"][k], digits))
                      for k in after["dimensions"]),
        "metrics": {"before": before["metrics"], "after": after["metrics"]},
    }
    scores["delta"]["composite"] = round(after["composite"] - before["composite"], digits)

    _write_json(os.path.join(mdir, "before.json"), before)
    _write_json(os.path.join(mdir, "after.json"), after)
    _write_json(os.path.join(mdir, "composite.json"),
                {"before": scores["before"], "after": scores["after"],
                 "delta": scores["delta"]})

    counts = counts_of(ctr, raw_lines, cleaned_counts)
    qsummary = [{"rule_id": rid, "name": book.get(rid).get("name", rid), "count": n}
                for rid, n in sorted(ctr.quarantine.items(), key=lambda kv: (-kv[1], kv[0]))]
    _write_json(os.path.join(qdir, "quarantine_summary.json"), qsummary)

    stats = {
        "task_id": task_id,
        "counts": counts,
        "rule_hits": {"quarantine": ctr.quarantine, "marks": ctr.marks,
                      "groups": ctr.groups},
        "detail": ctr.detail,
        "scores": scores,
        "quarantine_summary": qsummary,
    }
    _write_json(os.path.join(out_dir, "stats.json"), stats)

    metadata = {
        "task_id": task_id,
        "data_version": data_version,
        "rule_version": schemes.rule_version,
        "rule_hash": schemes.rule_hash,
        "scoring_scheme_version": schemes.scoring_version,
        "scoring_hash": schemes.scoring_hash,
        "policy_version": schemes.policy_version,
        "T1": schemes.t1,
        "T2": schemes.t2,
        "input_counts": counts["input"],
        "output_counts": counts["output"],
        "quarantine_summary": qsummary,
        "paths": {"task_dir": out_dir, "cleaned_dir": cleaned_dir,
                  "quarantine_dir": qdir, "metrics_dir": mdir},
    }
    _write_json(os.path.join(out_dir, "metadata.json"), metadata)

    return stats
