# -*- coding: utf-8 -*-
"""第 5 节 Agent：自然语言意图解析（规则式，不依赖任何模型 / 网络）。

把用户的一句中文/英文需求，解析成「要调哪个工具 + 什么参数」。

设计说明：
- 用规则匹配而不是大模型，原因是**离线可跑、结果确定、便于答辩解释**；
  规则表全部集中在下面的 KEYWORDS / PATTERNS 里，改起来是一张表的事。
- 解析不出来就如实返回 intent=unknown，并给出可用说法提示，**不猜、不编造**。
"""

import re

# ---------------------------------------------------------------------------
# 意图关键词表（改这张表就能扩展说法）
# ---------------------------------------------------------------------------
KEYWORDS = {
    "clean_evaluate": ("清洗", "评估", "评分", "打分", "质量", "治理", "处理",
                       "clean", "score", "evaluate", "quality"),
    "task_status":   ("进度", "状态", "跑到", "到哪", "执行到", "还在跑", "完成没",
                      "status", "progress"),
    "task_result":   ("结果", "多少分", "分数", "提升了", "对比", "变化", "得分",
                      "result", "score"),
    "get_samples":   ("样例", "样本", "例子", "给我看", "看一下", "异常记录", "脏数据",
                      "被隔离", "为什么", "sample", "example"),
    "get_report":    ("报告", "完整", "详细报告", "report"),
    "list_schemes":  ("方案", "规则有哪些", "有哪些规则", "默认方案", "配置有哪些",
                      "scheme", "rule"),
    "list_tasks":    ("历史任务", "之前跑过", "任务列表", "跑过哪些", "tasks"),
    "quick_demo":    ("快速", "演示", "秒级", "先看看", "预演", "quick", "demo"),
    "help":          ("你能干什么", "能做什么", "怎么用", "帮助", "help"),
}

# 意图优先级：先匹配具体的，再匹配笼统的
PRIORITY = ("quick_demo", "get_samples", "get_report", "task_status",
            "task_result", "list_schemes", "list_tasks", "help", "clean_evaluate")

INTENT_CN = {
    "clean_evaluate": "发起清洗 + 五维评估任务",
    "task_status":    "查询任务执行进度",
    "task_result":    "获取任务结果（五维对比 / 数据量 / 隔离）",
    "get_samples":    "取样本（清洗后 / 隔离区）",
    "get_report":     "获取完整评估报告",
    "list_schemes":   "列出已登记方案",
    "list_tasks":     "列出历史任务",
    "quick_demo":     "快速演示（不经 Hadoop）",
    "help":           "查看可用说法",
    "unknown":        "未识别",
}

# 表名词映射
TABLE_WORDS = {
    "ratings": ("评分", "评分表", "rating", "ratings"),
    "users":   ("用户", "用户表", "user", "users"),
    "movies":  ("电影", "电影表", "影片", "movie", "movies"),
}

# 样本类型词
QUARANTINE_WORDS = ("隔离", "异常", "脏", "问题记录", "被剔除", "quarantine", "bad")
CLEANED_WORDS = ("清洗后", "干净", "留下的", "正常", "cleaned", "good")

# task_id 形如 20260925-184303-379a4b
_TASK_ID_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")
# 「5 条 / 10 行 / 3 个」
_COUNT_RE = re.compile(r"(\d+)\s*(?:条|行|个)")

DIMS = ("Accurate", "Complete", "Unique", "Up-to-date", "Consistent")


def _contains(low, word):
    """判断词是否命中。

    英文词要用词边界匹配，否则 "movie" 会命中 "MovieLens"、
    "user" 会命中 "users" 之外的各种词——实测踩到过。
    """
    w = word.lower()
    if w.isascii():
        return re.search(r"\b" + re.escape(w) + r"\b", low) is not None
    return w in low


def parse(text):
    """解析一句自然语言，返回意图结构。

    返回：
        {"intent": ..., "intent_cn": ..., "params": {...}, "matched": [...], "confidence": 0~1}
    """
    if not isinstance(text, str) or not text.strip():
        return {"intent": "unknown", "intent_cn": INTENT_CN["unknown"],
                "params": {}, "matched": [], "confidence": 0.0}

    low = text.lower()

    # ---- 1. 命中的关键词 ----
    hits = {}
    for intent, words in KEYWORDS.items():
        hit = [w for w in words if _contains(low, w)]
        if hit:
            hits[intent] = hit

    intent = "unknown"
    for cand in PRIORITY:
        if cand in hits:
            intent = cand
            break
    if intent == "unknown" and hits:
        intent = sorted(hits, key=lambda k: -len(hits[k]))[0]

    # ---- 2. 抽参数 ----
    params = {}

    m = _TASK_ID_RE.search(text)
    if m:
        params["task_id"] = m.group(0)

    m = _COUNT_RE.search(text)
    if m:
        params["n"] = int(m.group(1))

    for key, words in TABLE_WORDS.items():
        if any(_contains(low, w) for w in words):
            params["table"] = key
            break

    if any(_contains(low, w) for w in QUARANTINE_WORDS):
        params["sample_type"] = "quarantine"
    elif any(_contains(low, w) for w in CLEANED_WORDS):
        params["sample_type"] = "cleaned"

    dims = [d for d in DIMS if d.lower() in low]
    if dims:
        params["dimensions"] = dims

    if any(w in low for w in ("默认规则", "默认方案", "default")):
        params["use_default"] = True

    # ---- 3. 置信度：命中词越多越可信；有明确 task_id 加成 ----
    conf = 0.0
    if intent != "unknown":
        conf = min(0.5 + 0.15 * len(hits.get(intent, [])), 0.9)
        if params.get("task_id"):
            conf = min(conf + 0.1, 1.0)
        if params.get("table") or params.get("sample_type"):
            conf = min(conf + 0.05, 1.0)

    return {
        "intent": intent,
        "intent_cn": INTENT_CN.get(intent, intent),
        "params": params,
        "matched": sorted({w for ws in hits.values() for w in ws}),
        "confidence": round(conf, 2),
    }


def help_text():
    """可用的说法提示（解析不出来时给用户看）。"""
    return "\n".join([
        "你可以这样问我：",
        "  · 用默认规则清洗 MovieLens 1M 并评估五维质量",
        "  · 任务 20260925-184303-379a4b 跑到哪一步了",
        "  · 看看这次的清洗结果",
        "  · 给我看 5 条被隔离的评分记录",
        "  · 出一份完整评估报告",
        "  · 现在有哪些已登记的清洗方案",
        "  · 快速演示一下（不走 Hadoop，几秒钟）",
    ])
