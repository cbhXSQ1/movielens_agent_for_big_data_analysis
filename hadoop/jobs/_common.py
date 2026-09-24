# -*- coding: utf-8 -*-
"""jobs/_common.py —— Streaming 作业公共设施（plan.md §4.6、decisions.md D-012）。

三条硬约束，全部在这里一次性处理掉：

1. **编码**：stdin/stdout 必须显式 ISO-8859-1，不受节点 locale 影响。
   作业之间的内部记录是 JSONL 且 `ensure_ascii=True`（纯 ASCII），
   所以任何编码下都不会被改写；ISO-8859-1 只对最终的 `::` 交付格式有意义。
2. **行号保真**：driver 把原始表物化成 `<line_no>\\t<raw_line>` 后再上传，
   所有作业因此拿到与本地 runner 完全一致的行号
   （规则见 `engine.pipeline.read_raw_table`：跳过空行、保留真实位置）。
3. **原始行保真**：内部记录形如
   `{"n": 行号, "raw": 原始行, "f": {字段: 值}}`。
   `raw` 是**排序键**，任何作业都不得改写它 —— M4/U5 的并列裁决依赖它，
   改了就会让集群的 cleaned 与本地不一致（D-012 问题 2）。

作业一律「本地 stdin→stdout 可测」：不经 Hadoop 也能 `cat fixture | python3 x.py`。
引擎通过 driver 打包的 `engine.zip` 分发（zipimport），本地测试则直接走 PYTHONPATH。
"""
import io
import json
import os
import sys

# ---------------------------------------------------------------------------
# 引擎导入：集群上 engine.zip 与作业脚本一起被 -files 分发到工作目录
# ---------------------------------------------------------------------------

def _ensure_engine_importable():
    """让 `from engine import ...` 在两种环境下都能工作。

    本地：<repo>/hadoop 已在 sys.path（run_tests.sh 会设，作业脚本的引导头也会加），
          直接用 `engine/` 源码树 —— **源码树优先**，否则改完代码仍会 import 到
          陈旧的 engine.zip。
    集群：driver 把 engine/ + config/ 打成 engine.zip 用 -files 分发，
          把它插到 sys.path 首位即可 zipimport。
    """
    try:
        import engine  # noqa: F401
        return
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in ("engine.zip", os.path.join(here, "..", "engine.zip"),
                 os.path.dirname(here)):
        if cand and os.path.exists(cand) and cand not in sys.path:
            sys.path.insert(0, cand)
            try:
                import engine  # noqa: F401
                return
            except ImportError:
                sys.path.pop(0)
    raise ImportError("找不到 engine 包（既不在源码树，也没有 engine.zip）")


_ensure_engine_importable()

from engine.actions import (apply_fix, make_quarantine_record,  # noqa: E402
                            make_record, resolve_records)
from engine.config_loader import ConfigError, load_schemes  # noqa: E402
from engine.metrics import (_numerator_spec, aggregate_counts,  # noqa: E402
                            count_keys, finalize, freshness_max_ts,
                            metrics_from_counts)
from engine.operators import _as_int, evaluate  # noqa: E402
from engine.pipeline import (FINAL_KEYS, FINAL_PAD, TABLE_FILES,  # noqa: E402
                             TABLE_SCHEMAS, Counters, _bad_text_markers,
                             _movie_field_specs, _user_field_specs,
                             conflict_group_keys, cross_stage_one,
                             final_prefix_len, group_stage_one, line_stage_one,
                             parse_record, read_raw_table, residual_stage_one,
                             strip_final_prefix)

SEP = "::"

# ---------------------------------------------------------------------------
# 编码化的标准输入输出（§4.6）
# ---------------------------------------------------------------------------

IN = io.TextIOWrapper(sys.stdin.buffer, encoding="iso-8859-1", newline="\n")
#: stdout 保持**严格** ISO-8859-1：交付格式必须能被 Latin-1 表示，
#: 表示不了说明引擎的 _latin1_safe 归一出了问题，宁可当场炸掉也不要写出坏数据。
OUT = io.TextIOWrapper(sys.stdout.buffer, encoding="iso-8859-1", newline="\n")
#: stderr 用 backslashreplace 兜底：这里只承载计数器与诊断信息，
#: 而诊断信息可能含中文（节点 locale 不可控）。若用严格 Latin-1，
#: 一条中文报错会让作业在**报错时再抛一个 UnicodeEncodeError**，
#: 真正的失败原因就被掩盖了 —— 这个坑在写测试时实测踩到过。
ERR = io.TextIOWrapper(sys.stderr.buffer, encoding="iso-8859-1", newline="\n",
                       errors="backslashreplace")


def counter(group, name, n=1):
    """Streaming 计数器协议；reducer 侧在最后一行输出后调用即可。"""
    ERR.write(u"reporter:counter:%s,%s,%d\n" % (group, name, n))
    ERR.flush()


def log(message):
    ERR.write(u"%s\n" % message)
    ERR.flush()


def emit(line):
    OUT.write(line)
    OUT.write(u"\n")


# ---------------------------------------------------------------------------
# 内部记录
# ---------------------------------------------------------------------------

def make_internal(rec):
    """规范记录 → 内部 JSON 记录（含原始行与行号）。"""
    return {"n": rec["line_no"], "raw": rec["raw_line"], "f": rec["fields"]}


def dumps(obj):
    """内部记录序列化：ensure_ascii 保证字节流是纯 ASCII（D-012）。"""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


#: 内部流的三种记录类型（decisions.md D-014：一趟作业双流合一，driver 按标签分拣）
KEEP = "K"    # 保留（清洗后的数据流）
QUAR = "Q"    # 隔离（action=quarantine 的命中）
DUMP = "D"    # 去重移除（R6/M4/U5，计入 counts.dedupe，不进隔离区）


def emit_record(rec):
    emit(KEEP + "\t" + dumps(make_internal(rec)))


def emit_quarantine(q):
    emit(QUAR + "\t" + dumps(q))


def emit_dedupe(q):
    emit(DUMP + "\t" + dumps(q))


def split_tagged(line):
    """`'K\t<json>'` → ('K', '<json>')。"""
    if len(line) < 3 or line[1] != "\t" or line[0] not in (KEEP, QUAR, DUMP):
        raise ConfigError("内部流行缺少合法类型标签：%r" % line[:80])
    return line[0], line[2:]


def iter_tagged():
    """逐行读 stdin，产出 `(整行, 类型, JSON载荷字符串)`；空行跳过。

    载荷**不做**解析：Q（隔离）与 D（去重）记录是不含 `f` 字段的独立结构，
    只有 K（保留）记录的载荷才是内部记录形态，且仅在需要时才 parse。
    """
    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        kind, payload = split_tagged(line)
        yield line, kind, payload


def parse_internal(line):
    """内部 JSON 记录 → 规范记录。"""
    obj = json.loads(line)
    return make_record(obj["f"], obj.get("raw", ""), obj.get("n", 0), obj.get("src", ""))


def iter_input(handler):
    """逐行读 stdin 并交给 handler；空行跳过。"""
    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        handler(line)


def iter_records():
    """只产出 **保留**（K）记录 —— 下游作业只消费这一支流。"""
    for line, kind, payload in iter_tagged():
        if kind == KEEP:
            yield parse_internal(payload)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def parse_args(argv=None, extra=()):
    """极简参数解析：--mode keep|quarantine --rules X --scoring Y [--table T] …"""
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {"mode": "keep", "rules": "cleaning_rules.v1.json",
            "scoring": "scoring_scheme.v1.json", "table": "", "job": ""}
    for name in extra:
        opts[name] = ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if "=" in key:
                key, val = key.split("=", 1)
                opts[key] = val
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                opts[key] = argv[i + 1]
                i += 1
            else:
                opts[key] = "1"
        i += 1
    return opts


def load(argv=None, extra=()):
    """解析参数并加载（已校验的）配置。"""
    opts = parse_args(argv, extra)
    schemes = load_schemes(opts["rules"], opts["scoring"])
    return opts, schemes


class Book(object):
    """规则索引：按表 + 阶段取规则，顺序与 config.pipeline 一致。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.rules = dict((r["id"], r) for r in cfg["rules"])
        self.stage_order = []
        for stage in cfg["pipeline"]:
            for rid in stage["rules"]:
                self.stage_order.append((stage["stage"], rid))

    def ids(self, table, stages):
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


def base_ctx(cfg):
    return {"reference_domains": cfg.get("reference_domains", {})}


def rec_ctx(base, table, raw_line):
    c = dict(base)
    c["table"] = table
    c["raw_line"] = raw_line
    return c


def field_specs(table, cfg, scoring):
    if table == "movies":
        return _movie_field_specs(cfg, scoring)
    if table == "users":
        return _user_field_specs(cfg)
    raise ConfigError("表 '%s' 未登记字段合法性判据" % table)


def bad_text_markers(scoring):
    return _bad_text_markers(scoring)


# ---------------------------------------------------------------------------
# 隔离记录
# ---------------------------------------------------------------------------

def quarantine(rec, table, rule, task_id, processed_at):
    return make_quarantine_record(
        TABLE_FILES[table], rec["line_no"], rec["raw_line"], rule["id"],
        rule["stage"], rule.get("name", rule["id"]), task_id, processed_at)


def task_id_of(opts):
    return opts.get("task-id") or opts.get("task_id") or os.environ.get(
        "ML_TASK_ID", "T-LOCAL")


def processed_at_of(opts):
    return opts.get("processed-at") or opts.get("processed_at") or None


# ---------------------------------------------------------------------------
# 作业主循环骨架
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 输入形态
# ---------------------------------------------------------------------------
# driver 会把原始表物化成 "<line_no>\t<raw_line>"（D-012 问题 1），
# 使集群 mapper 与本地 runner 用同一套行号。

def split_numbered(line):
    """`"<line_no>\t<raw_line>"` → (line_no, raw_line)。"""
    head, tab, rest = line.partition("\t")
    if not tab:
        raise ConfigError("输入缺少行号前缀（期望 '<line_no>\\t<raw_line>'）：%r"
                          % line[:80])
    return int(head), rest


def iter_numbered():
    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        yield split_numbered(line)


def report_counters(ctr):
    """把一趟作业的全部计数器按 Streaming 协议写到 stderr。

    D-014：keep 与隔离两趟合并后，同一趟会同时上报隔离命中、修复、
    去重、标记与分组数 —— 计数分组不同，互不混淆；driver 按组取用即可。
    """
    for rid, n in sorted(ctr.quarantine.items()):
        if n:
            counter("quarantine", rid, n)
    for name, n in sorted(ctr.fix.items()):
        if n:
            counter("fix", name, n)
    for table, n in sorted(ctr.dedupe.items()):
        if n:
            counter("dedupe", table, n)
    for rid, n in sorted(ctr.marks.items()):
        if n:
            counter("marks", rid, n)
    for rid, n in sorted(ctr.groups.items()):
        if n:
            counter("groups", rid, n)
    for key, n in sorted(ctr.detail.items()):
        if n:
            counter("detail", key, n)


# ---------------------------------------------------------------------------
# 通用作业实现（具名脚本都是 3 行包装，避免同一套逻辑写五遍）
# ---------------------------------------------------------------------------

def _setup():
    opts, schemes = load()
    return (opts, schemes, Book(schemes.rules), base_ctx(schemes.rules),
            task_id_of(opts), processed_at_of(opts))


def run_normalize(table):
    """行级作业（单趟双流，D-014）：读取带行号的原始表，跑 parse + validate_repair。

    一趟同时输出两种流（stdout 带 K/Q 标签），driver 事后分拣：
      K —— 修复后的内部记录（继续进清洗链）
      Q —— 被隔离的行（含原文/行号/规则/原因）
    判断代码与原先两趟完全一致（line_stage_one），只是不再跑两遍。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    ctr = Counters()
    for line_no, raw in iter_numbered():
        rec = make_record(parse_record(raw, table) or {}, raw, line_no,
                          TABLE_FILES[table])
        rec, q = line_stage_one(rec, table, book, base, task_id, ts, ctr)
        if rec is not None:
            emit_record(rec)
        for item in q:
            emit_quarantine(item)
    report_counters(ctr)


def run_resolve(table):
    """分组裁决作业（单趟双流，D-014）。map+reduce，key = 业务键。

    mapper：K 记录按键发射进 shuffle；Q/D 记录以 `Q\t<json>` 原样发射
            （键 "Q" 是保留命名空间，用户 ID 全是数字，不会撞），
            reducer 见到该组原样转发。
    reducer：同键组内先按**原始行**排序再应用策略（plan §5.1）；
            保留 → K；被去重的 → D（rule_id=R6/M4/U5，计 counts.dedupe）。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    specs = field_specs(table, schemes.rules, schemes.scoring)
    rids = book.ids(table, ("dedupe_resolve",))
    if not rids:
        raise ConfigError("表 %s 没有 dedupe_resolve 规则" % table)
    rule = book.get(rids[0])
    key_fields = rule["detect"]["keys"]
    ctr = Counters()

    if not opts.get("reduce"):
        for line, kind, payload in iter_tagged():
            if kind != KEEP:
                emit(line)
                continue
            rec = parse_internal(payload)
            key = SEP.join(rec["fields"].get(k, "") for k in key_fields)
            emit("%s\t%s" % (key, dumps(make_internal(rec))))
        return

    current, group = None, []

    def flush():
        if not group:
            return
        kept, dropped = group_stage_one(group, table, rule, base, specs,
                                        task_id, ts, ctr)
        if kept is not None:
            emit_record(kept)
        for item in dropped:
            emit_dedupe(item)

    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        key, _, payload = line.partition("\t")
        if key == QUAR:                       # 保留命名空间：原样转发隔离记录
            emit(line)
            continue
        if key != current:
            flush()
            current, group = key, []
        group.append(parse_internal(payload))
    flush()
    report_counters(ctr)


def run_residual(table):
    """兜底检查作业（movies：M3/M6/M7/M9）。map-only，单趟双流。

    非 K 记录原样转发；K 记录逐规则判定：M3 隔离 → Q，M6/M7 标记、M9 检查 → 仍 K。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    ctr = Counters()
    rids = book.ids(table, ("residual_checks",))
    for line, kind, payload in iter_tagged():
        if kind != KEEP:
            emit(line)
            continue
        rec = parse_internal(payload)
        keep_flag = True
        for rid in rids:
            rule = book.get(rid)
            keep_flag, q = residual_stage_one(rec, table, rule, base, task_id, ts, ctr)
            if q is not None:
                emit_quarantine(q)
            if not keep_flag:
                break
        if keep_flag:
            emit_record(rec)
    report_counters(ctr)


def run_ratings_dedupe():
    """评分去重与冲突兜底（R6 + R7）。map+reduce，key = (UserID, MovieID, Timestamp)。

    与 run_resolve 同一套路：mapper 转发非 K；reducer 逐键组做
    R6（保留一条）+ R7（同键评分冲突 → 整组隔离）后按 K/Q/D 分流。
    R8（同用户同电影多时间戳）键不同且 mark_only，归 stats_marks。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    table = "ratings"
    rids = book.ids(table, ("dedupe_resolve",))
    if not rids:
        raise ConfigError("ratings 没有 dedupe_resolve 规则")
    rule = book.get(rids[0])
    key_fields = rule["detect"]["keys"]

    residual = [book.get(r) for r in book.ids(table, ("residual_checks",))
                if book.get(r)["detect"].get("op") == "conflict_by"
                and book.get(r)["detect"].get("key") == list(key_fields)]
    ctr = Counters()

    if not opts.get("reduce"):
        for line, kind, payload in iter_tagged():
            if kind != KEEP:
                emit(line)
                continue
            rec = parse_internal(payload)
            emit("%s\t%s" % (SEP.join(rec["fields"].get(k, "") for k in key_fields),
                              dumps(make_internal(rec))))
        return

    current, group = None, []

    def flush():
        if not group:
            return
        kept, dropped = group_stage_one(group, table, rule, base, None,
                                        task_id, ts, ctr)
        survivors = [kept] if kept is not None else []
        for rr in residual:
            ck = conflict_group_keys(survivors, rr["detect"]["key"],
                                     rr["detect"]["value"])
            ctx = dict(base)
            ctx["conflict_group_keys"] = ck
            nxt = []
            for rec in survivors:
                keep, q = residual_stage_one(rec, table, rr, ctx, task_id, ts, ctr)
                if q is not None:
                    emit_quarantine(q)
                if keep:
                    nxt.append(rec)
            survivors = nxt

        for item in dropped:
            emit_dedupe(item)
        for rec in survivors:
            emit_record(rec)

    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        key, _, payload = line.partition("\t")
        if key == QUAR:
            emit(line)
            continue
        if key != current:
            flush()
            current, group = key, []
        group.append(parse_internal(payload))
    flush()
    report_counters(ctr)


def load_dim_keys(paths, source="cleaned"):
    """收集维表键集合（X1/X2 与 A4 都要用的 `ctx["dim_keys"]`）。

    `source="cleaned"` 读内部 JSONL（users_resolve / movies_resolve 的 keep 产物），
    不必先把维表落一遍交付格式再读回来；
    `source="raw"` 读 driver 物化的 `<行号>\t<原始行>` —— before 侧的 A4 口径是
    「引用了**原始**维表里的 ID」，用清洗后的键集合会高估。
    """
    dim = {}
    for table, key in (("users", "UserID"), ("movies", "MovieID")):
        path = paths.get(table)
        if not path:
            raise ConfigError("需要 %s 维表用于跨表判定（--%s <file>）" % (table, table))
        keys = set()
        if source == "raw":
            # 原始维表同样是 driver 物化的 `<行号>\t<原始行>`（见 D-012），
            # 不能按普通 .dat 读 —— 那会把行号并进第一个字段。
            with io.open(path, "r", encoding="iso-8859-1", newline="\n") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if line == "":
                        continue
                    _, raw = split_numbered(line)
                    f = parse_record(raw, table)
                    if f is not None:
                        keys.add(f.get(key, ""))
        else:
            with io.open(path, "r", encoding="iso-8859-1") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if line == "":
                        continue
                    kind, payload = split_tagged(line)
                    if kind != KEEP:
                        continue          # 只有保留流里的对象才算有效维表键
                    keys.add(parse_internal(payload)["fields"].get(key, ""))
        if not keys:
            raise ConfigError("维表 %s 的键集合为空，疑似传错了文件：%s" % (table, path))
        dim[table] = keys
    return dim


def run_ratings_cross():
    """跨表引用校验（X1/X2）。map-only，单趟双流。

    维表经 -files 广播（load_dim_keys 只认 K 流，见下）；拿不到维表必须报错。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    dim_keys = load_dim_keys(opts, opts.get("source") or "cleaned")
    ctr = Counters()
    for line, kind, payload in iter_tagged():
        if kind != KEEP:
            emit(line)
            continue
        rec = parse_internal(payload)
        keep, q = cross_stage_one(rec, book, base, dim_keys, task_id, ts, ctr)
        if q is not None:
            emit_quarantine(q)
        if keep:
            emit_record(rec)
    report_counters(ctr)


# ---------------------------------------------------------------------------
# 评分（D-014：每侧合并成**一个**单 reducer 作业）
# ---------------------------------------------------------------------------
# 原设计：每侧 10 趟 = measure×3 + groupstats(distinct/dupgroups)×6 + finalize。
# 合并后：每侧 1 趟。mapper 按输入路径分派表（或 --table 显式指定），发射四种线：
#   M\t<计数键>\t<数值>          —— 逐记录贡献（sum）
#   D\t<计数键>\t<键元组>        —— 唯一键/唯一行（distinct 计数）
#   G\t<指标>\t<键元组>\t<值元组> —— 重复组统计（S4）
#   T\t<计数键>\t<时间戳>        —— 新鲜度的最大时间戳
# reducer 用**单 reducer + 内存聚合**（本数据量下 distinct 集合约几百 MB，
# 容器内存拿到能申请到的上限即可；换来 18 趟作业的 AM/JVM 启动开销归零）。
# 注意：本伪分布式节点的 NM 总内存 4096MB（map 1024MB×2），driver 把该作业
# 的 reduce 容器开成 2048MB —— 再大（如 3072）永远分配不到容器，reduce 会
# 无限 PENDING（全量运行实测踩到）。
# 最终分数仍在作业里由 metrics_from_counts 算出 —— 「比率不得在 driver 里算」
# 的约定不变。

def _metric_specs(schemes):
    for dim in schemes.scoring["dimensions"]:
        for spec in dim["metrics"]:
            yield spec


def _metric_token_where(node, field, sep):
    """A6 的 where 省略 field/sep（继承自 agg），求值前补齐（与 metrics.py 同规）。"""
    if isinstance(node, list):
        return [_metric_token_where(x, field, sep) for x in node]
    if not isinstance(node, dict):
        return node
    out = dict(node)
    if out.get("op") in ("token_in_set", "token_not_in_set", "token_empty_or_duplicate"):
        out.setdefault("field", field)
        out.setdefault("sep", sep)
    for k in ("args", "where"):
        if k in out:
            out[k] = _metric_token_where(out[k], field, sep)
    return out


def _scope_covers(agg_spec, table):
    """该聚合的作用范围是否包含这张表。"""
    if agg_spec.get("table"):
        return agg_spec["table"] == table
    if agg_spec.get("tables"):
        names = [t if isinstance(t, str) else t["table"] for t in agg_spec["tables"]]
        return table in names
    return True


def _record_agg_value(agg_spec, table, fields, ctx):
    """单条记录对某个逐记录聚合的贡献值；`None` 表示该聚合不进本作业的 M 流。"""
    agg = agg_spec["agg"]
    if agg == "count":
        return 1 if evaluate(agg_spec.get("where", True), fields, ctx) else 0
    if agg == "valid_field_count":
        return sum(1 for fs in agg_spec.get("fields") or []
                   if evaluate(fs["condition"], fields, ctx))
    if agg == "field_count":
        names = agg_spec.get("fields")
        return len(names) if names else len(TABLE_SCHEMAS[table])
    if agg == "nonempty_field_count":
        return sum(1 for f in TABLE_SCHEMAS[table] if fields.get(f, "") != "")
    if agg == "record_complete_count":
        return 1 if all(fields.get(f, "") != "" for f in TABLE_SCHEMAS[table]) else 0
    if agg == "token_count":
        field, sep = agg_spec["field"], agg_spec["sep"]
        where = _metric_token_where(agg_spec.get("where", True), field, sep)
        return sum(1 for tok in fields.get(field, "").split(sep)
                   if evaluate(where, {field: tok}, ctx))
    return None


def _score_input(opts, table):
    """按 `--source` 产出 `(fields | None, 原始行, 行号)`。

    raw     —— driver 物化的 `<行号>\t<原始行>`，before 侧的坏行也要看得到
                （C2/C3/U2/S1 的分母是原始行数，U2 分子还要把坏行各算一条不重复行）
    cleaned —— 内部标签流，只取 K（保留）记录
    """
    source = opts.get("source")
    if source == "raw":
        for line_no, raw in iter_numbered():
            yield parse_record(raw, table), raw, line_no
        return
    if source == "cleaned":
        for line, kind, payload in iter_tagged():
            if kind == KEEP:
                rec = parse_internal(payload)
                yield rec["fields"], rec["raw_line"], rec["line_no"]
        return
    raise ConfigError("--source 必须是 raw 或 cleaned，得到 %r" % (source,))


def _dispatch_table():
    """集群上从 `mapreduce_map_input_file` 推断本输入属于哪张表。

    三个输入目录由 driver 命名（u_res / m_resid / r_cross，
    raw 侧就是 users.dat / movies.dat / ratings.dat），按特征子串分派；
    本地测试显式传 `--table`，不走这条路。
    """
    f = os.environ.get("mapreduce_map_input_file", "") or ""
    if not f:
        raise ConfigError("缺少 mapreduce_map_input_file，也无法从 --table 得到表名")
    if f.endswith("users.dat") or "u_res" in f:
        return "users"
    if f.endswith("movies.dat") or "m_resid" in f:
        return "movies"
    if f.endswith("ratings.dat") or "r_cross" in f:
        return "ratings"
    raise ConfigError("无法从输入路径推断表名：%s（请检查 driver 的输入目录命名）" % f)


def run_score_all():
    """评分（每侧一趟，D-014）：measure + 键统计 + 新鲜度 + 最终分数。

    mapper 按表发射 M/D/G/T 四种线（每一侧把三张表作为三个 -input 交给
    同一个作业，mapper 按 `mapreduce_map_input_file` 分派表）；
    单 reducer 做内存聚合，结束时用 `metrics_from_counts` 算出 18 个指标、
    五个维度与综合分，输出**唯一**一行最终 JSON
    （`{"side","counts","result"}`，与原先 score_finalize 的格式一致）。

    内存说明：D 流会为 U1/U2 各保留约 100 万个键元组的集合（数百 MB），
    因此 driver 提交时必须把 reducer 容器内存提到 2GB：
    本机 NM 总内存 4096MB，`-D mapreduce.reduce.memory.mb=2048` 是可申请到的
    上限附近（3072 会永远 PENDING，见 D-014 踩坑记录）。
    这是刻意的取舍：为 24MB 的数据量保留「流式分组」的复杂度没必要，
    换来整个评分阶段从 20 趟缩到 2 趟。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    source = opts.get("source")
    if source not in ("raw", "cleaned"):
        raise ConfigError("--source 必须是 raw 或 cleaned，得到 %r" % (source,))
    specs = list(_metric_specs(schemes))

    if not opts.get("reduce"):
        # 只有 mapper 需要知道表；reducer 只看聚合后的 M/D/G/T 线
        table = opts.get("table") or _dispatch_table()
        # A4 需要维表键集合（before 用原始维表、after 用清洗后维表）
        dim_keys = load_dim_keys(opts, source)

        for fields, raw, n in _score_input(opts, table):
            ctx = rec_ctx(base, table, raw)
            ctx["dim_keys"] = dim_keys
            for spec in specs:
                mid = spec["id"]
                if spec.get("measure", "ratio") == "freshness":
                    p = spec["params"]
                    if table == p.get("table") and fields is not None:
                        v = _as_int(fields.get(p["field"], ""))
                        rng = base.get("reference_domains", {}).get("timestamp") or {}
                        if v is not None and not (
                                ("min" in rng and v < rng["min"]) or
                                ("max" in rng and v > rng["max"])):
                            emit("T\t%s\t%d" % (mid + ".max_ts", v))
                    continue
                for part, agg_spec in (("num", _numerator_spec(
                        spec["numerator"], spec["denominator"])),
                                       ("den", spec["denominator"])):
                    if not _scope_covers(agg_spec, table):
                        continue
                    key = "%s.%s" % (mid, part)
                    agg = agg_spec.get("agg")
                    if agg == "count" and agg_spec.get("on") == "raw_lines":
                        # 分母的原始行数：每一条非空行都记 1（含无法解析的坏行）
                        emit("M\t%s\t1" % key)
                        continue
                    if agg in ("distinct_key_count",):
                        for tspec in agg_spec.get("tables") or [agg_spec]:
                            if tspec["table"] == table and fields is not None:
                                # D 线必须带表名：同一 part（如 U3.num）在不同表里
                                # 的键元组可能重叠（用户 ID 与电影 ID 都是数字串），
                                # 集合按 (part, 表) 分开，收尾时再逐表求和 ——
                                # 与本地 aggregate_counts 的语义一致。
                                emit("D\t%s\t%s\t%s" % (key, table, SEP.join(
                                    fields.get(f, "") for f in tspec["key"])))
                        continue
                    if agg == "distinct_row_count":
                        if fields is not None:
                            payload = SEP.join(fields.get(f, "")
                                               for f in TABLE_SCHEMAS[table])
                        else:
                            payload = "#unparsed:%d:%s" % (n, raw)
                        emit("D\t%s\t%s\t%s" % (key, table, payload))
                        continue
                    if agg == "dup_group_count":
                        # G 线必须带 part：num 与 den 是同一个聚合的两个变体
                        # （conflict_free 与否），两者各发射一遍，reducer 必须
                        # 按 (mid, part) 分开统计 —— 合并统计会把每条记录的
                        # 组大小翻倍，把非重复组误算成重复组。
                        for tspec in agg_spec.get("tables") or [agg_spec]:
                            if tspec["table"] == table and fields is not None:
                                v = tspec.get("value")
                                val = (fields.get(v, "") if isinstance(v, str)
                                       else SEP.join(fields.get(f, "") for f in v or []))
                                emit("G\t%s\t%s\t%s\t%s\t%s" % (mid, part, table,
                                    SEP.join(fields.get(f, "") for f in tspec["key"]),
                                    val))
                        continue
                    if fields is None:
                        continue
                    val = _record_agg_value(agg_spec, table, fields, ctx)
                    if val:
                        emit("M\t%s\t%d" % (key, val))
        return

    # ---- reducer：内存聚合 ----
    # D/G 的集合与分组都按 (part, 表) / (mid, 表, 键) 命名空间，收尾时逐表求和：
    # 用户 ID 与电影 ID 都是数字串，不同表的键元组会重叠，跨表合并集合会少算。
    counts, partsets, groups, best = {}, {}, {}, {}
    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        kind, _, rest = line.partition("\t")
        if kind == "M":
            part, _, val = rest.partition("\t")
            counts[part] = counts.get(part, 0) + int(val)
        elif kind == "D":
            part, _, rest2 = rest.partition("\t")
            table, _, payload = rest2.partition("\t")
            partsets.setdefault((part, table), set()).add(payload)
        elif kind == "G":
            mid, _, rest2 = rest.partition("\t")
            part, _, rest3 = rest2.partition("\t")
            table, _, rest4 = rest3.partition("\t")
            key, _, val = rest4.partition("\t")
            g = groups.setdefault((mid, part, table, key), [0, set()])
            g[0] += 1
            g[1].add(val)
        elif kind == "T":
            part, _, stamp = rest.partition("\t")
            v = int(stamp)
            if v > best.get(part, -1):
                best[part] = v
        else:
            raise ConfigError("未知的汇总线类型 %r（%s）" % (kind, line[:80]))

    for (mid, part, _table, _key), (size, vals) in groups.items():
        # 与本地 _agg_dup_group_count 同规：只统计组大小 > 1 的组；
        # num 额外要求组内 value 完全一致（conflict_free）。
        if size <= 1:
            continue
        if part == "den":
            counts[mid + ".den"] = counts.get(mid + ".den", 0) + 1
        elif part == "num" and len(vals) == 1:
            counts[mid + ".num"] = counts.get(mid + ".num", 0) + 1
    for (part, _table), s in partsets.items():
        counts[part] = counts.get(part, 0) + len(s)
    for part, v in best.items():
        counts[part] = v

    unknown = sorted(set(counts) - set(count_keys(schemes)))
    if unknown:
        raise ConfigError("出现未登记的计数键（疑似作业间串了数据）：%s" % unknown)

    side = "before" if source == "raw" else "after"
    values = metrics_from_counts(counts, schemes, base)
    emit(dumps({"side": side, "counts": counts, "result": finalize(values, schemes)}))


def _load_r9_users(opts):
    """读取 raw 趟产出的 R9 匹配用户集合（driver 经 -files 传入）。"""
    path = opts.get("r9-users")
    if not path:
        return set()
    with io.open(path, "r", encoding="iso-8859-1") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("tag") == "R9":
                return set(obj.get("counts", {}))
    return set()


def run_stats_marks():
    """统计与标记作业（R9 / M8 / R8 / X3）—— 单 reducer，产出 stats.json 的组成部分。

    两个互补的趟，用 `--source` 选择：

    * `--source raw-ratings`（输入：带行号的**原始**评分表）
        R9 要统计「每个用户的非法评分条数」。非法评分已经被 R2 隔离，
        清洗结果里再也数不出来 —— 这类行为统计必须回到原始数据，
        这也是它被单独做成一个作业而不是塞进清洗链的原因。
    * `--source cleaned`（输入：清洗后的 ratings + users + movies，可多个 `-input`）
        * R8  同用户同电影多时间戳 → 冲突对（mark_only，不删记录）
        * M8  不同 MovieID 出现完全相同标题 → 同名组
        * X3  维表里从未被评分的对象（冷门电影 / 沉默用户），保留并报告

    记录**自描述**：按字段集合分派（有 Rating → 评分；有 Title → 电影；
    有 Gender → 用户），因此三种清洗产物可以混在同一个 `-input` 里。

    单 reducer（`-D mapreduce.job.reduces=1`）让 reducer 在输入结束时做全局集合运算
    （X3 的差集），聚集体都很小：用户 6,040 / 电影 3,883 / 同名组 218。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    source = opts.get("source")
    if source not in ("raw-ratings", "cleaned"):
        raise ConfigError("--source 必须是 raw-ratings 或 cleaned，得到 %r" % (source,))
    ctr = Counters()

    if not opts.get("reduce"):
        if source == "raw-ratings":
            # 直接消费 driver 物化的 `<行号>\t<原始行>`，不经过任何清洗作业 ——
            # R9 要的正是「非法评分」，而那些行会被 R2 隔离，越早读越好。
            where = book.get("R9")["detect"]["where"]
            for line_no, raw in iter_numbered():
                f = parse_record(raw, "ratings")
                if f is None:
                    continue
                if evaluate(where, f, rec_ctx(base, "ratings", raw)):
                    emit("R9\t%s" % f.get("UserID", ""))
            return
        r9_users = _load_r9_users(opts)
        for line, kind, payload in iter_tagged():
            if kind != KEEP:
                continue
            f = parse_internal(payload)["fields"]
            if "Rating" in f:
                emit("RU\t%s" % f.get("UserID", ""))
                emit("RM\t%s" % f.get("MovieID", ""))
                emit("R8\t%s::%s\t%s" % (f.get("UserID", ""), f.get("MovieID", ""),
                                          f.get("Timestamp", "")))
                if r9_users and f.get("UserID", "") in r9_users:
                    emit("R9M\t1")
            elif "Title" in f:
                emit("M8\t%s\t%s" % (f.get("Title", ""), f.get("MovieID", "")))
                emit("MSET\t%s" % f.get("MovieID", ""))
            elif "Gender" in f:
                emit("USET\t%s" % f.get("UserID", ""))
            else:
                raise ConfigError("无法识别的记录字段集合（既非评分/电影/用户）：%s"
                                  % sorted(f))
        return

    r9, ru, rm, uset, mset = {}, set(), set(), set(), set()
    m8, r8, r9m = {}, {}, 0
    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        tag, _, payload = line.partition("\t")
        if tag == "R9":
            r9[payload] = r9.get(payload, 0) + 1
        elif tag == "RU":
            ru.add(payload)
        elif tag == "RM":
            rm.add(payload)
        elif tag == "USET":
            uset.add(payload)
        elif tag == "MSET":
            mset.add(payload)
        elif tag == "M8":
            title, _, mid = payload.partition("\t")
            m8.setdefault(title, set()).add(mid)
        elif tag == "R8":
            pair, _, stamp = payload.partition("\t")
            r8.setdefault(pair, set()).add(stamp)
        elif tag == "R9M":
            r9m += 1
        else:
            raise ConfigError("未知的统计标签 %r" % tag)

    if source == "raw-ratings":
        rule = book.get("R9")
        threshold = rule["detect"]["min_matches"]
        matched = dict((u, n) for u, n in r9.items() if n >= threshold)
        ctr.groups["R9"] = len(matched)
        ctr.bumped("R9_matched_users", len(matched))
        emit(dumps({"tag": "R9", "min_matches": threshold, "counts": matched}))
        report_counters(ctr)
        return

    min_keys = book.get("M8")["detect"].get("min_keys", 2)
    groups = dict((t, sorted(ids)) for t, ids in m8.items()
                  if t != "" and len(ids) >= min_keys)
    conflicts = dict((k, sorted(v)) for k, v in r8.items() if len(v) >= 2)
    never_users = uset - ru
    never_movies = mset - rm

    ctr.groups["M8"] = len(groups)
    ctr.groups["R8"] = len(conflicts)
    ctr.groups["X3_users"] = len(never_users)
    ctr.groups["X3_movies"] = len(never_movies)
    ctr.mark("M8", sum(len(v) for v in groups.values()))
    if r9m:
        # R9 命中记录数 = 「被判注入嫌疑的用户」在**清洗结果**里保留下来的评分数。
        # 该用户集合来自 raw 趟（见 _load_r9_users），因此这里数的是清洗后仍然存活的记录，
        # 与本地 pipeline 的 marks.R9 口径一致。
        ctr.mark("R9", r9m)
    ctr.bumped("X3_reported", len(never_users) + len(never_movies))
    emit(dumps({"tag": "M8", "min_keys": min_keys, "groups": groups}))
    emit(dumps({"tag": "R8", "conflicts": conflicts}))
    emit(dumps({"tag": "X3", "users_never_rated": sorted(never_users),
                "movies_never_rated": sorted(never_movies)}))
    report_counters(ctr)


def run_finalize(table):
    """收尾作业：把内部记录变成交付格式的 cleaned 表，并按业务键**数值序**排列。

    plan §5.1 未规定最终行序，而 Hadoop 的 Text 排序是字典序（"10" < "2"）。
    零填充业务键让 Text 排序与数值序一致；**单 reducer** 保证全局有序（§10 已认可）。

    reducer **原样吐出整行**（含零填充键前缀），由 driver 按固定宽度
    `strip_final_prefix()` 剥离。原因见 engine/pipeline.py 的注释：
    `-D ...separator=` 的空值会被 Hadoop 静默丢弃，Streaming 仍会给不带 TAB 的
    reducer 输出补一个尾随 TAB，那会污染交付数据。保留前缀则无损。
    """
    opts, schemes = load()
    keys = FINAL_KEYS[table]
    schema = TABLE_SCHEMAS[table]

    if not opts.get("reduce"):
        for rec in iter_records():
            for f in schema:
                if "\t" in rec["fields"].get(f, ""):
                    raise ConfigError(
                        "字段 %s 含 TAB，会破坏交付格式的分隔约定：%r"
                        % (f, rec["fields"][f][:60]))
            padded = SEP.join(("%0*d" % (FINAL_PAD, int(rec["fields"][f]))) for f in keys)
            emit("%s\t%s" % (padded, SEP.join(rec["fields"].get(f, "") for f in schema)))
        return

    # reducer：整行原样输出，前缀交给 driver 剥离
    for line in IN:
        line = line.rstrip("\n")
        if line != "":
            emit(line)


def run_guarded(fn):
    """统一异常出口：把错误写 stderr 并以非 0 退出，便于 driver 抓取摘要。"""
    try:
        fn()
    except BrokenPipeError:  # pragma: no cover - 下游提前关闭
        pass
    except Exception as exc:  # noqa: BLE001 - 作业边界，必须整体兜住
        log("FATAL %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
    finally:
        try:
            OUT.flush()
        except Exception:  # pragma: no cover
            pass


__all__ = [
    "IN", "OUT", "ERR", "SEP", "counter", "log", "emit", "emit_record",
    "emit_quarantine", "dumps", "make_internal", "parse_internal", "iter_input",
    "iter_records", "parse_args", "load", "Book", "base_ctx", "rec_ctx",
    "field_specs", "bad_text_markers", "quarantine", "task_id_of",
    "processed_at_of", "run_guarded", "apply_fix", "resolve_records",
    "make_record", "make_quarantine_record", "evaluate", "load_schemes",
    "ConfigError", "parse_record", "read_raw_table", "TABLE_SCHEMAS",
    "TABLE_FILES", "FINAL_KEYS", "FINAL_PAD", "final_prefix_len",
    "strip_final_prefix",
]
