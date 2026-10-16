# -*- coding: utf-8 -*-
"""第 5 节 Agent：结果解释层（把 JSON 信封翻译成人话）。

铁律：
1. 只用信封里真实存在的数字；字段缺失就写「未返回」，绝不填占位值。
2. 任务失败/未完成时，只复述状态与原因，不猜测、不拿旧数据顶替。
3. 每份结果都必须带上 limitations——「隔离 ≠ 修复」是本课程的评分红线。

阶段 2（T2.6）：新增**分块 / 事件化**出口（chunk_text / iter_deltas /
stream_kind / stream_explainer），供阶段 3 配合流式推送；**一次性调用方式
（explain_* 返回整段字符串）原样保留，不受影响**。

阶段 2（T2.5）：维度名与中文名改为从中立模块 constants 取，避免复制。
"""

from .constants import DIMS, DIM_CN

__all__ = [
    "DIMS", "DIM_CN",
    "explain_status", "explain_error", "explain_result",
    "explain_samples", "explain_schemes",
    "chunk_text", "iter_deltas", "stream_kind", "stream_explainer",
    "EXPLAINERS", "CHUNK_SIZE",
]

#: 默认分块大小（字符数）；阶段 3 可按推送节奏调整
CHUNK_SIZE = 240

#: 结果类型 → 解释函数（供 stream_kind 按名取用）
EXPLAINERS = {}


def _num(value):
    """把数字格式化成两位小数；不是数字就原样返回。"""
    if isinstance(value, (int, float)):
        return "%.2f" % value
    return "未返回"


def explain_status(envelope):
    """解释 status 信封（执行情况）。"""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return explain_error(envelope)

    status = envelope.get("status", "未返回")
    stage = envelope.get("stage", "")
    pct = envelope.get("progress_percent")
    message = envelope.get("message", "")
    tid = envelope.get("task_id", "")

    cn = {"queued": "排队中", "running": "运行中", "succeeded": "已成功",
          "failed": "已失败"}.get(status, status)

    lines = ["任务 %s：%s" % (tid, cn)]
    if stage:
        idx = envelope.get("stage_index")
        total = envelope.get("stage_total")
        if idx and total:
            lines.append("当前阶段：%s（第 %s/%s 步）" % (stage, idx, total))
        else:
            lines.append("当前阶段：%s" % stage)
    if isinstance(pct, (int, float)):
        lines.append("进度：%d%%" % pct)
    if message:
        lines.append("说明：%s" % message)

    errors = envelope.get("errors") or []
    if errors:
        lines.append("错误信息：")
        for e in errors:
            if isinstance(e, dict):
                lines.append("  - 阶段 %s / 作业 %s：%s"
                             % (e.get("stage", "?"), e.get("job", "?"),
                                e.get("message", "?")))
            else:
                lines.append("  - %s" % e)
    return "\n".join(lines)


def explain_error(envelope):
    """解释失败信封（如实复述错误码与原因）。"""
    err = (envelope or {}).get("error") if isinstance(envelope, dict) else None
    if not isinstance(err, dict):
        return "调用失败，但未返回结构化错误信息（原始信封：%s）" % (envelope,)

    lines = ["调用失败：%s（错误码 %s）"
             % (err.get("message", "未返回原因"), err.get("code", "?"))]
    if err.get("task_id"):
        lines.append("任务 ID：%s" % err["task_id"])
    if err.get("stage"):
        lines.append("失败阶段：%s" % err["stage"])
    if err.get("job"):
        lines.append("失败作业：%s" % err["job"])
    if err.get("exit_code") is not None:
        lines.append("作业退出码：%s" % err["exit_code"])
    for key in ("details", "stderr", "raw"):
        if err.get(key):
            lines.append("%s：%s" % (key, err[key]))
    lines.append("以上为 driver 返回的真实状态，未做任何推测或补数。")
    return "\n".join(lines)


def explain_result(envelope):
    """解释 result 信封（五维对比 + 数据量变化 + 隔离统计 + 局限性）。"""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return explain_error(envelope)

    out = []
    out.append("任务 %s 已完成，数据版本 %s。"
               % (envelope.get("task_id", "?"), envelope.get("data_version", "?")))

    # ---- 1. 数据量变化 ----
    counts = envelope.get("counts") or {}
    inp = counts.get("input") or {}
    outp = counts.get("output") or {}
    if inp or outp:
        out.append("")
        out.append("【数据量变化】")
        for key, label in (("ratings_lines", "评分表输入"),
                           ("users_lines", "用户表输入"),
                           ("movies_lines", "电影表输入")):
            if inp.get(key) is not None:
                out.append("  %s：%s 行" % (label, inp[key]))
        for key, label in (("ratings", "评分表输出"),
                           ("users", "用户表输出"),
                           ("movies", "电影表输出")):
            if outp.get(key) is not None:
                out.append("  %s：%s 行" % (label, outp[key]))

    # ---- 2. 隔离与去重（隔离 ≠ 修复）----
    quar = counts.get("quarantine") or {}
    dedupe = counts.get("dedupe") or {}
    fix = counts.get("fix") or {}
    if quar or dedupe or fix:
        out.append("")
        out.append("【隔离 / 去重 / 修复】")
        if quar.get("total") is not None:
            out.append("  隔离总数：%s 行（这些行被移出正表，不是被修好了）"
                       % quar["total"])
        by_rule = quar.get("by_rule") or {}
        if by_rule:
            top = sorted(by_rule.items(), key=lambda kv: kv[1], reverse=True)
            out.append("  按规则（结算后命中数，取前 5）：")
            for rule, cnt in top[:5]:
                out.append("    %s：%s 行" % (rule, cnt))
        if dedupe:
            out.append("  去重：%s" % "，".join("%s %s 行" % (k, v)
                                                for k, v in sorted(dedupe.items())))
        if fix:
            out.append("  修复：%s" % "，".join("%s %s 行" % (k, v)
                                                for k, v in sorted(fix.items())))

    # ---- 3. 五维质量分 ----
    scores = envelope.get("scores") or {}
    before = scores.get("before") or {}
    after = scores.get("after") or {}
    delta = scores.get("delta") or {}
    if before or after:
        out.append("")
        out.append("【五维质量分（清洗前 → 清洗后）】")
        out.append("  维度            前        后        提升")
        for dim in DIMS + ("composite",):
            if dim not in before and dim not in after:
                continue
            name = DIM_CN.get(dim, dim)
            pad = name + " " * (14 - len(name) * 2 + len(name))
            out.append("  %s %s → %s（+%s）"
                       % (pad, _num(before.get(dim)), _num(after.get(dim)),
                          _num(delta.get(dim))))

    # ---- 4. 局限性（必须展示）----
    limits = envelope.get("limitations") or []
    if limits:
        out.append("")
        out.append("【必须同时说明的局限性】")
        for item in limits:
            out.append("  - %s" % item)

    out.append("")
    out.append("口径提醒：分数提升有一部分来自「把问题行隔离出分母」，"
               "不等于这些问题被修复；汇报时必须同屏展示数据量变化与隔离影响。")
    return "\n".join(out)


def explain_samples(envelope):
    """解释 samples 信封（异常记录样例）。"""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return explain_error(envelope)

    kind = envelope.get("type", "?")
    table = envelope.get("table", "?")
    total = envelope.get("total_available")
    rows = envelope.get("samples") or []

    out = ["%s 表 %s 样本（共 %s 条可用，本次取 %d 条）："
           % (table, "隔离区" if kind == "quarantine" else "清洗后",
              "未返回" if total is None else total, len(rows))]
    for r in rows:
        if not isinstance(r, dict):
            out.append("  %s" % r)
            continue
        if kind == "quarantine":
            out.append("  行 %s｜规则 %s｜%s｜%s"
                       % (r.get("line_no", "?"), r.get("rule_id", "?"),
                          r.get("reason", ""), r.get("raw_line", "")))
        else:
            out.append("  行 %s｜%s" % (r.get("line", "?"), r.get("raw_line", "")))
    return "\n".join(out)


def explain_schemes(envelope):
    """解释 schemes 信封（已登记方案）。"""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return explain_error(envelope)
    items = envelope.get("schemes") or []
    if not items:
        return "当前没有登记任何方案。"
    out = ["已登记方案 %d 个：" % len(items)]
    for s in items:
        out.append("  - %s（类型 %s，版本 %s，状态 %s）"
                   % (s.get("scheme_id", "?"), s.get("type", "?"),
                      s.get("version", "?"), s.get("status", "?")))
        if s.get("description"):
            out.append("    %s" % s["description"])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 阶段 2 · T2.6：分块 / 事件化出口（配合流式推送；不改变上面的一次性用法）
# ---------------------------------------------------------------------------

#: 结果类型 → 解释函数（键名与 http_api 里的结果类别一致）
EXPLAINERS.update({
    "status":  explain_status,
    "error":   explain_error,
    "result":  explain_result,
    "samples": explain_samples,
    "schemes": explain_schemes,
})


def chunk_text(text, size=CHUNK_SIZE):
    """把整段中文解释切成若干片段，**优先在换行处切开**，便于流式逐段推送。

    - size <= 0 或文本为空时退化为「整段一次产出」，行为与一次性调用一致；
    - 拼接所有片段 == 原文（保证「分块」不丢字、不改字）。
    """
    if not text:
        return
    size = int(size) if size else 0
    if size <= 0 or len(text) <= size:
        yield text
        return
    buf = ""
    for line in text.splitlines(True):          # keepends=True，保留换行
        if buf and len(buf) + len(line) > size:
            yield buf
            buf = ""
        while len(line) > size:                 # 单行过长时硬切
            if buf:
                yield buf
                buf = ""
            yield line[:size]
            line = line[size:]
        buf += line
    if buf:
        yield buf


def iter_deltas(text, event="message_delta", node_id=None, size=CHUNK_SIZE):
    """把整段解释切成「增量文本事件」生成器（事件形状对齐接口文档 §3.6）。

    产出形如：
        {"event": "message_delta", "text": "……", "index": 0, "final": False}
        {"event": "message_delta", "text": "", "index": 3, "final": True}   ← 收尾
    其中 node_id 非空时附带 "node_id"（阶段 3 的节点血缘用）。
    """
    pieces = list(chunk_text(text, size))
    if not pieces:
        pieces = [""]
    for i, piece in enumerate(pieces):
        event_obj = {"event": event, "text": piece, "index": i,
                     "final": i == len(pieces) - 1}
        if node_id is not None:
            event_obj["node_id"] = node_id
        yield event_obj


def stream_explainer(explainer, envelope, event="message_delta",
                     node_id=None, size=CHUNK_SIZE):
    """先算整段解释，再逐块产出事件（一次性计算 + 流式呈现）。"""
    text = explainer(envelope) if callable(explainer) else str(envelope)
    for item in iter_deltas(text, event=event, node_id=node_id, size=size):
        yield item


def stream_kind(kind, envelope, event="message_delta",
                node_id=None, size=CHUNK_SIZE):
    """按结果类别名（status/result/samples/schemes/error）流式产出。

    未注册的类别统一走 explain_error（如实报错，不臆造）。
    """
    explainer = EXPLAINERS.get(kind, explain_error)
    for item in stream_explainer(explainer, envelope, event=event,
                                 node_id=node_id, size=size):
        yield item
