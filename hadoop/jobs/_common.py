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
from engine.operators import evaluate  # noqa: E402
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


def emit_record(rec):
    emit(dumps(make_internal(rec)))


def emit_quarantine(q):
    emit(dumps(q))


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
    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        yield parse_internal(line)


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


def report_counters(ctr, mode):
    """把本趟的计数器按 Streaming 协议写到 stderr。

    两趟分工，避免重复计数：
      * `--mode quarantine` 只报隔离命中
      * `--mode keep`       只报修复 / 去重 / 标记 / 明细
    driver 分别取用后拼成契约 counts（agent-interface.md §4.5）。
    """
    if mode == "quarantine":
        for rid, n in sorted(ctr.quarantine.items()):
            if n:
                counter("quarantine", rid, n)
        return
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
    """行级作业：读取带行号的原始表，跑 parse + validate_repair。

    `--mode keep` 输出修复后的内部记录；`--mode quarantine` 输出隔离记录。
    两模式判定互补（同一份 line_stage_one），plan §5.1「双模式两趟」。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    mode = opts["mode"]
    ctr = Counters()
    for line_no, raw in iter_numbered():
        rec = make_record(parse_record(raw, table) or {}, raw, line_no,
                          TABLE_FILES[table])
        rec, q = line_stage_one(rec, table, book, base, task_id, ts, ctr)
        if mode == "quarantine":
            for item in q:
                emit_quarantine(item)
        elif rec is not None:
            emit_record(rec)
    report_counters(ctr, mode)


def run_resolve(table):
    """分组裁决作业：map 发射 `"<业务键>\t<内部记录>"`，reduce 调用 group_stage_one。

    reducer 按 key 分组，组内**先按原始行字典序排序**再应用策略（plan §5.1），
    故 value 的到达顺序不影响结果 —— 与本地 runner 逐字节一致。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    mode = opts["mode"]
    specs = field_specs(table, schemes.rules, schemes.scoring)
    rids = book.ids(table, ("dedupe_resolve",))
    if not rids:
        raise ConfigError("表 %s 没有 dedupe_resolve 规则" % table)
    rule = book.get(rids[0])
    key_fields = rule["detect"]["keys"]
    ctr = Counters()

    if not opts.get("reduce"):
        if mode == "quarantine":
            raise ConfigError(
                "resolve 作业的 mapper 趟只做分区：判重发生在 reduce 趟。"
                "请加 --reduce（并保持 --mode quarantine）来产出被去重的记录。")
        for rec in iter_records():
            key = SEP.join(rec["fields"].get(k, "") for k in key_fields)
            emit("%s\t%s" % (key, dumps(make_internal(rec))))
        return

    current, group = None, []

    def flush():
        if not group:
            return
        kept, dropped = group_stage_one(group, table, rule, base, specs,
                                        task_id, ts, ctr)
        if mode == "quarantine":
            for item in dropped:
                emit_quarantine(item)
        elif kept is not None:
            emit_record(kept)

    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        key, _, payload = line.partition("\t")
        if key != current:
            flush()
            current, group = key, []
        group.append(parse_internal(payload))
    flush()
    report_counters(ctr, mode)


def run_residual(table):
    """兜底检查作业（movies：M3/M6/M7/M9）。map-only —— 这些规则不需要分组上下文。"""
    opts, schemes, book, base, task_id, ts = _setup()
    mode = opts["mode"]
    ctr = Counters()
    rids = book.ids(table, ("residual_checks",))
    for rec in iter_records():
        keep = True
        for rid in rids:
            rule = book.get(rid)
            keep, q = residual_stage_one(rec, table, rule, base, task_id, ts, ctr)
            if mode == "quarantine" and q is not None:
                emit_quarantine(q)
            if not keep:
                break
        if mode != "quarantine" and keep:
            emit_record(rec)
    report_counters(ctr, mode)


def run_ratings_dedupe():
    """评分去重与冲突兜底（R6 + R7）。map+reduce，key = (UserID, MovieID, Timestamp)。

    为什么 key 取 R6 的完整业务键：
      * R6（同键重复 → 保留一条）本身就是按这个键分组的；
      * R7（同键评分冲突 → 整组隔离）在 **R6 之后**判定。R6 已经把每个键收敛成
        一条记录，所以「同键不同 Rating」在结算后必然为空 —— 与本地 pipeline
        的行为完全一致（本地也是先 R6 再去残留检查里算 conflict_group_keys）。
      * R8（同用户同电影多时间戳）的键是 (UserID, MovieID)，跨组，且是 `mark_only`
        （不删记录），因此由 stats_marks 统计，不进本作业 —— 这样本作业的 reducer
        不需要第二个 shuffle。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    mode = opts["mode"]
    table = "ratings"
    rids = book.ids(table, ("dedupe_resolve",))
    if not rids:
        raise ConfigError("ratings 没有 dedupe_resolve 规则")
    rule = book.get(rids[0])
    key_fields = rule["detect"]["keys"]

    # 只取与 R6 同键的残留规则（即 R7）；R8 键不同，归 stats_marks
    residual = [book.get(r) for r in book.ids(table, ("residual_checks",))
                if book.get(r)["detect"].get("op") == "conflict_by"
                and book.get(r)["detect"].get("key") == list(key_fields)]
    ctr = Counters()

    if not opts.get("reduce"):
        if mode == "quarantine":
            raise ConfigError(
                "resolve 作业的 mapper 趟只做分区：判重发生在 reduce 趟。"
                "请加 --reduce（并保持 --mode quarantine）来产出被去重的记录。")
        for rec in iter_records():
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
                if mode == "quarantine" and q is not None:
                    emit_quarantine(q)
                if keep:
                    nxt.append(rec)
            survivors = nxt

        if mode == "quarantine":
            for item in dropped:
                emit_quarantine(item)
        else:
            for rec in survivors:
                emit_record(rec)

    for line in IN:
        line = line.rstrip("\n")
        if line == "":
            continue
        key, _, payload = line.partition("\t")
        if key != current:
            flush()
            current, group = key, []
        group.append(parse_internal(payload))
    flush()
    report_counters(ctr, mode)


def load_dim_keys(paths):
    """从 `-files` 分发来的维表 keep 产物里收集键集合（X1/X2 的 ctx["dim_keys"]）。

    输入是内部 JSONL（users_resolve / movies_resolve 的 keep 输出），
    不是交付格式 —— 因此不需要经过 clean_finalize，避免了「为了跨表校验先把
    维表写一遍再读回来」的额外往返。
    """
    dim = {}
    for table, key in (("users", "UserID"), ("movies", "MovieID")):
        path = paths.get(table)
        if not path:
            raise ConfigError("X1/X2 需要清洗后的 %s 维表（--%s <file>）" % (table, table))
        keys = set()
        with io.open(path, "r", encoding="iso-8859-1") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if line == "":
                    continue
                keys.add(parse_internal(line)["fields"].get(key, ""))
        if not keys:
            raise ConfigError("维表 %s 的键集合为空，疑似传错了文件：%s" % (table, path))
        dim[table] = keys
    return dim


def run_ratings_cross():
    """跨表引用校验（X1 孤儿用户 / X2 孤儿电影）。map-only。

    维表经 `-files` 广播；拿不到维表必须报错，不能静默把孤儿当成有效引用。
    """
    opts, schemes, book, base, task_id, ts = _setup()
    mode = opts["mode"]
    dim_keys = load_dim_keys(opts)
    ctr = Counters()
    for rec in iter_records():
        keep, q = cross_stage_one(rec, book, base, dim_keys, task_id, ts, ctr)
        if mode == "quarantine":
            if q is not None:
                emit_quarantine(q)
        elif keep:
            emit_record(rec)
    report_counters(ctr, mode)


def _load_r9_users(opts):
    """读取上一趟（`--source raw-ratings`）产出的 R9 匹配用户集合。

    R9 是唯一一条**跨趟**的统计：命中集合来自原始数据，而「命中记录数」
    要在清洗结果里数。driver 把 raw 趟的 part 文件取回、抽出这个集合、
    再作为 `-files` 传给 cleaned 趟。没传就退化为不统计命中记录数（不影响契约字段）。
    """
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
        for rec in iter_records():
            f = rec["fields"]
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
        report_counters(ctr, "keep")
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
    report_counters(ctr, "keep")


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
