# -*- coding: utf-8 -*-
"""engine/actions.py —— 修复策略、resolve 策略与隔离记录（plan.md §4.3）。

三件事：
  1. ``make_quarantine_record`` 生成标准隔离记录（字段见配置的 quarantine_record.fields）
  2. ``apply_fix`` 按规则声明的策略**原地修复**记录，返回 (新记录, 实际生效的策略名)
  3. ``resolve_records`` 对同键的多条记录做去重/冲突解决，返回 (保留记录, 被丢弃记录)

记录（record）规范形状 —— pipeline 与 Streaming 作业共同遵守::

    {
      "fields":      {字段名: 字符串值},
      "raw_line":    str,   # 原始行：用于确定性排序与隔离
      "line_no":     int,   # 1-based 原始行号
      "source_file": str,
    }

**确定性**（plan.md §5.1、§10）：同键分组内一律先按 ``raw_line`` 字典序排序再应用策略，
因此重跑、乱序、Map 输出顺序变化都不会改变结果。这一点由单元测试锁定。

只用标准库；不依赖 Hadoop。
"""
import datetime
import html
import re

from engine.config_loader import FIXES, ConfigError
from engine.operators import _as_int, evaluate

__all__ = ["make_record", "make_quarantine_record", "apply_fix", "resolve_records",
           "valid_score", "field_level_merge"]

# ---------------------------------------------------------------------------
# ISO-8859-1 可编码性归一
# ---------------------------------------------------------------------------
# 为什么需要：数据处理约定 C-ENC 要求输入输出都是 ISO-8859-1，但**修复动作本身**
# 可能产出 ISO-8859-1 表示不了的字符。本数据里的唯一实例是 P1 的 HTML 实体：
#   'And God Created Woman (Et Dieu&#8230;Créa la Femme) (1956)'
#   html.unescape → U+2026 '…'，ISO-8859-1 无此字符（它在 CP1252 里才存在）。
# 参考原型只把结果写进 UTF-8 报告，从未写回 ISO-8859-1，所以这个问题不会暴露；
# Hadoop 作业必须写回 ISO-8859-1，故必须显式定策。
# 策略：先用语义等价的 ASCII 形式替换常见标点，再用 errors='replace' 兜底。
# 记录见 docs/hadoop/decisions.md D-007。
_LATIN1_FALLBACK = {
    "\u2026": "...",    # … HORIZONTAL ELLIPSIS
    "\u2014": "-",      # — EM DASH
    "\u2013": "-",      # – EN DASH
    "\u2018": "'", "\u2019": "'",   # ‘ ’
    "\u201c": '"', "\u201d": '"',   # “ ”
    "\u2022": "*",      # • BULLET
    "\u20ac": "EUR",    # €
    "\u2122": "(TM)",   # ™
    "\u00a0": " ",      # NBSP（Latin-1 有，保留亦可；此处统一为普通空格）
}

#: 双重编码乱码的特征标记（与 P1 的 detect.values 一致）
_MOJIBAKE_MARKERS = ("Ã", "Â", "â€")

_ZIP_PLUS4_RE = re.compile(r"\d{5}-\d{4}")


def _latin1_safe(value):
    """把字符串归一为 ISO-8859-1 可编码形式。"""
    if not isinstance(value, str):
        return value
    for ch, repl in _LATIN1_FALLBACK.items():
        if ch in value:
            value = value.replace(ch, repl)
    return value.encode("iso-8859-1", errors="replace").decode("iso-8859-1")


# ---------------------------------------------------------------------------
# 记录构造
# ---------------------------------------------------------------------------

def make_record(fields, raw_line="", line_no=0, source_file=""):
    """构造规范记录。`fields` 会被浅拷贝，调用方后续改动不影响记录。"""
    return {
        "fields": dict(fields),
        "raw_line": raw_line,
        "line_no": line_no,
        "source_file": source_file,
    }


def make_quarantine_record(source_file, line_no, raw_line, rule_id, stage, reason, task_id,
                           processed_at=None):
    """标准隔离记录（config/cleaning_rules.v1.json 的 quarantine_record.fields）。

    processed_at 默认取当前 UTC 时间；需要重跑可比对（黄金测试、哈希对账）时
    显式传入固定值以保证确定性。字段顺序与配置声明的顺序一致。
    """
    if processed_at is None:
        processed_at = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    return {
        "source_file": source_file,
        "line_no": line_no,
        "raw_line": raw_line,
        "rule_id": rule_id,
        "stage": stage,
        "reason": reason,
        "task_id": task_id,
        "processed_at": processed_at,
    }


# ---------------------------------------------------------------------------
# 规则内省：这条规则作用于哪些字段 / 每字段的判定子句是什么
# ---------------------------------------------------------------------------

def _leaf_predicates(expr):
    """展开 detect 表达式，返回 [(字段名, 该字段的判定子句), ...]。

    用于 U2/U3 这类「一条规则管多个字段、只处置其中非法的字段」的场景：
    U2 的 detect 是 or(not_in_set Gender, not_in_set Age, not_in_set Occupation)，
    展开后即可逐字段判断，而不是整条记录一刀切。
    """
    out = []
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("field"), str):
                out.append((node["field"], node))
            else:
                for k in ("args", "where"):
                    if k in node:
                        stack.append(node[k])
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _target_fields(rule):
    """规则 detect 涉及的字段（保持出现顺序，去重）。"""
    seen = []
    for f, _ in _leaf_predicates(rule.get("detect")):
        if f not in seen:
            seen.append(f)
    return seen


def _op_param(rule, op, param, default=None):
    """从 detect 中找出某个算子的参数值（如 R4 的 threshold）。"""
    stack = [rule.get("detect")]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("op") == op and param in node:
                return node[param]
            for k in ("args", "where"):
                if k in node:
                    stack.append(node[k])
        elif isinstance(node, list):
            stack.extend(node)
    return default


def _table_of(rule, record):
    """规则作用表；规则写 all 时由 source_file 推断（users.dat → users）。"""
    t = rule.get("table")
    if t in ("ratings", "users", "movies"):
        return t
    base = str(record.get("source_file", "")).rsplit("/", 1)[-1]
    if base.endswith(".dat"):
        base = base[:-4]
    return base or t


def _ctx_for(rules, record, rule):
    """apply_fix 内部的求值上下文；reference_domains 来自配置本身。"""
    return {
        "reference_domains": (rules or {}).get("reference_domains", {}),
        "raw_line": record.get("raw_line", ""),
        "table": _table_of(rule, record),
    }


# ---------------------------------------------------------------------------
# 修复策略（cleaning_rules.v1.说明.md §8 的策略库；名字必须与配置登记一致）
# ---------------------------------------------------------------------------

def _strat_strip(fields, rule, targets, ctx):
    """去掉字段首尾空白。"""
    changed = False
    for f in targets:
        v = fields.get(f)
        if isinstance(v, str) and v != v.strip():
            fields[f] = v.strip()
            changed = True
    return changed


def _strat_decode_html_entity(fields, rule, targets, ctx):
    """HTML 实体解码（如 '&#8230;' → '…'），随后做 ISO-8859-1 可编码性归一。"""
    changed = False
    for f in targets:
        v = fields.get(f)
        if not isinstance(v, str) or "&" not in v:
            continue
        nv = _latin1_safe(html.unescape(v))
        if nv != v:
            fields[f] = nv
            changed = True
    return changed


def _strat_decode_double_encoding(fields, rule, targets, ctx):
    """双重编码解码：UTF-8 字节被按 ISO-8859-1 读出后的还原（'LÃ©on' → 'Léon'）。

    仅当字段含乱码特征标记（'Ã'/'Â'/'â€'）时才尝试 —— 与本地原型的口径一致。
    加这道闸门是为了不误伤本来就正确的 Latin-1 文本：虽然 'Mépris' 这类
    （重音字符后跟 ASCII）反解必然抛错而被跳过，但显式闸门让行为可读、可解释，
    且与配置里 P1 的 detect.values 保持同一套标记。
    """
    changed = False
    for f in targets:
        v = fields.get(f)
        if not isinstance(v, str) or not any(m in v for m in _MOJIBAKE_MARKERS):
            continue
        try:
            nv = _latin1_safe(v.encode("latin-1").decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if nv != v:
            fields[f] = nv
            changed = True
    return changed


def _strat_timestamp_ms_to_s(fields, rule, targets, ctx):
    """13 位毫秒时间戳整除 1000 转秒（阈值取自规则 detect 的 threshold）。"""
    threshold = _op_param(rule, "timestamp_unit_ms", "threshold", 100000000000)
    changed = False
    for f in targets:
        v = fields.get(f)
        n = _as_int(v)
        if n is None or n < threshold:
            continue
        fields[f] = str(n // 1000)
        changed = True
    return changed


def _strat_zip_plus4_truncate(fields, rule, targets, ctx):
    """ZIP+4（12345-6789）截取前 5 位；其余形态不在此处理（交给 blank_invalid_field）。"""
    changed = False
    for f in targets:
        v = fields.get(f)
        if isinstance(v, str) and _ZIP_PLUS4_RE.fullmatch(v):
            fields[f] = v[:5]
            changed = True
    return changed


def _strat_blank_invalid_field(fields, rule, targets, ctx):
    """把该规则判定为非法的那**些字段**置空（逐字段判定，不整条记录处置）。

    U2：非法 Gender/Age/Occupation 置空并保留用户（属性非法不代表用户与其评分非法）。
    U3：无法判定的邮编（ABCDE/过短/空）置空。
    """
    changed = False
    for f, node in _leaf_predicates(rule.get("detect")):
        if not evaluate(node, fields, ctx):
            continue
        if fields.get(f, "") != "":
            fields[f] = ""
            changed = True
    return changed


# --- 记录级策略：属 resolve 阶段，apply_fix 里是空操作但必须可识别 ---

def _strat_record_level(fields, rule, targets, ctx):
    return False


FIX_STRATEGIES = {
    # 字段级修复
    "strip": _strat_strip,
    "decode_html_entity": _strat_decode_html_entity,
    "decode_double_encoding": _strat_decode_double_encoding,
    "timestamp_ms_to_s": _strat_timestamp_ms_to_s,
    "zip_plus4_truncate": _strat_zip_plus4_truncate,
    "blank_invalid_field": _strat_blank_invalid_field,
    # 记录级（由 resolve_records 实现）
    "prefer_valid_record": _strat_record_level,
    "keep_first": _strat_record_level,
    "field_level_merge": _strat_record_level,
}

assert set(FIX_STRATEGIES) == set(FIXES), (
    "修复策略库与配置校验白名单不一致: %s" % (set(FIX_STRATEGIES) ^ set(FIXES)))


# ---------------------------------------------------------------------------
# apply_fix
# ---------------------------------------------------------------------------

def apply_fix(record, fix_spec, rules):
    """按策略列表修复记录。

    record   —— 规范记录（见模块 docstring）
    fix_spec —— 规则对象（含 detect/fix），或直接的 fix 对象 {"strategies": [...]}
    rules    —— 完整 cleaning_rules，用于取 reference_domains

    返回 (新记录, 实际生效的策略名列表)。**不修改入参记录**。
    策略名只在它确实改动了至少一个字段值时才计入 —— 这样「命中数」与「实际修复数」
    可以区分开（例如 P1 的 decode_html_entity 对纯乱码标题是空转）。
    """
    if isinstance(fix_spec, dict) and "strategies" in fix_spec and "fix" not in fix_spec:
        rule = {"fix": fix_spec, "detect": fix_spec.get("detect", {})}
    else:
        rule = fix_spec or {}

    strategies = (rule.get("fix") or {}).get("strategies") or []
    targets = _target_fields(rule)
    ctx = _ctx_for(rules, record, rule)

    out = make_record(record.get("fields", {}), record.get("raw_line", ""),
                      record.get("line_no", 0), record.get("source_file", ""))
    fields = out["fields"]

    applied = []
    for name in strategies:
        fn = FIX_STRATEGIES.get(name)
        if fn is None:
            raise ConfigError("未登记的修复策略 '%s'；策略库为封闭白名单" % name)
        if fn(fields, rule, targets, ctx):
            applied.append(name)

    # 输出必须能写回 ISO-8859-1（C-ENC）
    for f in targets:
        if isinstance(fields.get(f), str):
            fields[f] = _latin1_safe(fields[f])

    return out, applied


# ---------------------------------------------------------------------------
# resolve：同键多记录的处置
# ---------------------------------------------------------------------------

def valid_score(record, field_specs, ctx=None):
    """字段合法性得分 = 满足各 spec.condition 的 spec 权重之和。

    field_specs 由 pipeline 从配置构造，例如电影：
      Title 非空(+2)、标题无 [20XX](+1)、标题无乱码(+1)、类型全部合法(+1)
    用户：Gender/Age/Occupation 非空各 +1、Zip 匹配 ^\\d{5}$(+1)
    """
    ctx = ctx or {}
    fields = record.get("fields", {})
    total = 0
    for spec in field_specs:
        cond = spec.get("condition")
        if cond is None:
            continue
        if evaluate(cond, fields, ctx):
            total += spec.get("weight", 1)
    return total


def field_level_merge(records, field_specs):
    """字段级合并：一致字段保留，冲突字段置空。

    仅用于 U5 的 prefer_valid_then_field_merge 在得分并列时：
    不整组隔离（那会级联删除大量有效评分），而是逐字段取共识。
    字段顺序取自 field_specs 中首次出现的次序，保证输出稳定。
    """
    order = []
    for spec in field_specs:
        f = spec.get("field")
        if f and f not in order:
            order.append(f)
    merged = {}
    for f in order:
        vals = set(r.get("fields", {}).get(f, "") for r in records)
        merged[f] = vals.pop() if len(vals) == 1 else ""
    return merged


def _dropped(record, reason):
    """被丢弃/隔离的记录：保留原始信息 + 原因，便于审计与计数。"""
    return {
        "fields": dict(record.get("fields", {})),
        "raw_line": record.get("raw_line", ""),
        "line_no": record.get("line_no", 0),
        "source_file": record.get("source_file", ""),
        "reason": reason,
    }


def resolve_records(records, policy, field_specs, ctx=None):
    """同键多记录的处置，返回 (保留记录 | None, 被丢弃记录列表)。

    policy.value 的已登记口径（cleaning_rules.v1.说明.md §9）：
      prefer_valid / prefer_valid_then_field_merge —— 保留合法字段最多者；
          并列时：prefer_valid 取原始行字典序最小者；
                  prefer_valid_then_field_merge 先做字段级合并
      quarantine_all —— 整组丢弃（M4/U5 的非推荐口径）
      keep_first     —— 取原始行字典序最小者

    确定性：入参顺序不影响结果（内部先按 raw_line 字典序排序）。
    被丢弃记录**不计入隔离区**，而计入 counts.dedupe（见 agent-interface.md §4.5：
    quarantine.by_rule 不含 M4/U5/R6），由调用方按 reason 分类计数。
    """
    if not records:
        return None, []

    value = (policy or {}).get("value")
    ctx = ctx or {}

    # 确定性：按原始行字典序（同 raw_line 时退化为 line_no）排序
    ordered = sorted(records, key=lambda r: (r.get("raw_line", ""), r.get("line_no", 0)))

    if value == "quarantine_all":
        return None, [_dropped(r, "整组隔离(policy=quarantine_all)") for r in ordered]

    if value == "keep_first":
        return ordered[0], [_dropped(r, "保留首条(policy=keep_first)") for r in ordered[1:]]

    if value in ("prefer_valid", "prefer_valid_then_field_merge"):
        scores = [valid_score(r, field_specs, ctx) for r in ordered]
        best = max(scores)
        top = [i for i, s in enumerate(scores) if s == best]

        if len(top) == 1:
            keep_idx = top[0]
            reason = "保留合法字段最多的记录(得分 %d)" % best
        elif value == "prefer_valid_then_field_merge":
            keep_idx = top[0]
            reason = "得分并列(%d) → 字段级合并" % best
        else:
            # 并列 → 取原始行字典序最小者（ordered 已排序，top[0] 即最小）
            keep_idx = top[0]
            reason = "得分并列(%d) → 取原始行字典序最小者" % best

        kept = ordered[keep_idx]
        if len(top) > 1 and value == "prefer_valid_then_field_merge":
            merged = field_level_merge([ordered[i] for i in top], field_specs)
            kept = make_record(merged, kept.get("raw_line", ""), kept.get("line_no", 0),
                               kept.get("source_file", ""))

        dropped = [_dropped(ordered[i], reason) for i in range(len(ordered)) if i != keep_idx]
        return kept, dropped

    raise ConfigError("未登记的 resolve 口径 policy.value=%r（登记表见说明 §9）" % (value,))
