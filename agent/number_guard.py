# -*- coding: utf-8 -*-
"""阶段 2（ag-tools）· T2.3：数字溯源护栏（字段 + 上下文级）。

从旧 LLM 循环（llm_loop.py）里抽出，并升级：不再是「用户原话 ∪ 工具结果」
的**字符串集合差集**，而是给每个可溯源数字建立**出处索引** —— 数字 → 它来自
哪个工具步骤、哪个字段路径（如 `get_task_result.counts.quarantine.total`）。
这样：

  * 正常路径放行（正向断言）：工具返回过的数字、用户原话里的数字、数组长度
    这类由结果**推导**出来的数字，都算合法；
  * 编造被拦（负向断言）：回答里出现索引里没有的数字，连同它在回答中的**上下文
    片段**一并报出，便于模型改写、便于人排查。

设计红线（与整个项目一致）：
  * 只做校验，**绝不修改回答**；
  * 不猜：索引里没有就是没有，不为「看起来合理」的数字放行；
  * 纯函数式：不碰网络、不碰驱动，输入输出全是普通 Python 结构。
"""

import re

#: 数字提取：先剥千分位逗号（100,830 → 100830）再取数，避免拆成两个数
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_THOUSAND_RE = re.compile(r"(?<=\d)[,，](?=\d)")

#: 结构性数字：**行首的有序列表序号**（`1.` / `1、` / `(1)` / `（1）`）。
#: 这些是排版编号，不是业务数据；不剥掉会把"1. 2. 3."误判成编造数字。
_LIST_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:\d{1,2}[.、)．]|[（(]\d{1,2}[）)])[ \t]*"
)

#: 报上下文片段时，数字左右各取多少字符
CONTEXT_WINDOW = 12

#: 用户原话里出现的数字，出处记这个固定标签
QUESTION_PATH = "<用户原话>"


def normalize(text):
    """把一段文本规范化成纯数字串（剥千分位符）。"""
    return _THOUSAND_RE.sub("", str(text if text is not None else ""))


def numbers_in_text(text):
    """从文本里取规范化数字串列表（保序，可能重复）。"""
    return _NUM_RE.findall(normalize(text))


def strip_structural_numbers(text):
    """去掉列表序号这类**结构性数字**，避免被当成编造的业务数据。

    只剥「行首的有序列表标记」（`1.` / `2、` / `(3)` / `（4）`），
    同一行后面的真实数字（如 `2. 共 888888 行` 里的 888888）仍然保留。
    """
    return _LIST_MARKER_RE.sub("", normalize(text))


class AuthorizedNumbers:
    """可溯源数字索引：数字 → 一串「出处」（字段路径 + 上下文）。

    同一数字可能有多处出处（例如 2 既可能是某数组长度，也可能是某字段值）；
    只要命中任意一处，该数字即被放行。
    """

    def __init__(self):
        self._index = {}          # token -> [{"path": ..., "context": ...}]

    def authorize(self, token, path, context):
        """登记一个可溯源数字（token 是规范化后的数字串）。"""
        token = normalize(token)
        if not token:
            return
        bucket = self._index.setdefault(token, [])
        entry = {"path": path, "context": context}
        if entry not in bucket:
            bucket.append(entry)

    def authorize_text(self, text, path, context):
        """把文本里出现的所有数字都登记为可溯源。"""
        for token in numbers_in_text(text):
            self.authorize(token, path, context)

    def has(self, token):
        return normalize(token) in self._index

    def provenance(self, token):
        """某数字的全部出处（未收录返回空列表）。"""
        return list(self._index.get(token, ()))

    def tokens(self):
        return set(self._index)

    def __contains__(self, token):
        return self.has(token)

    def __len__(self):
        return len(self._index)


def index_value(obj, authorized, path="", context=""):
    """递归把一个 JSON 信封里的数字登记进索引（带字段路径与上下文）。

    规则与旧实现一致，但保留了路径：
      * dict  —— 逐字段递归，路径用 `.` 连接；
      * list  —— **长度**也算可溯源数字（模型说「共 2 个方案」是从数组长度推的），
                 元素路径用 `[i]`；
      * bool  —— 跳过（True/False 不是业务数字）；
      * int/float —— 记其字符串值；
      * str   —— 记文本里出现的数字（如规则命中数文案）。
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = ("%s.%s" % (path, key)) if path else str(key)
            index_value(value, authorized, child, context)
    elif isinstance(obj, (list, tuple)):
        authorized.authorize(str(len(obj)), path or "<列表>", context)
        for i, value in enumerate(obj):
            index_value(value, authorized, "%s[%d]" % (path, i), context)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        authorized.authorize(str(obj), path or "<数值>", context)
    elif isinstance(obj, str):
        authorized.authorize_text(obj, path or "<文本>", context)


def build_authorized(question, steps):
    """由「用户原话 + 各步骤工具返回」构建可溯源数字索引。

    参数 steps 是循环的步骤列表，每步形如：
        {"tool": "...", "args": {...}, "ok": True, "envelope": {...}}
    只登记 envelope（工具真实返回），**不登记 args** —— args 是模型自己填的，
    不能把它当成「结果数字」给模型背书，否则编造可经参数绕道。
    """
    authorized = AuthorizedNumbers()
    authorized.authorize_text(question, QUESTION_PATH, "用户原话")
    for i, step in enumerate(steps or []):
        tool = step.get("tool") or "?"
        context = "步骤 %d（%s）" % (i + 1, tool)
        index_value(step.get("envelope"), authorized, path=tool, context=context)
    return authorized


def _snippet(text, start, end):
    """取数字在回答文本里的上下文片段（左右各留一点）。"""
    left = max(0, start - CONTEXT_WINDOW)
    right = min(len(text), end + CONTEXT_WINDOW)
    piece = text[left:right].strip()
    return ("…" + piece + "…") if (left > 0 or right < len(text)) else piece


def find_unauthorized(final, authorized):
    """找出回答里**未被索引收录**的数字。

    先剥掉列表序号（结构性数字），再扫描；真正的业务数字仍逐项校验。
    返回记录列表（按出现顺序、同数字去重）：
        {"number": "888888", "snippet": "…编号 888888 待定…"}
    """
    clean = strip_structural_numbers(final)
    seen = set()
    records = []
    for match in _NUM_RE.finditer(clean):
        token = match.group(0)
        if token in seen or authorized.has(token):
            continue
        seen.add(token)
        records.append({"number": token,
                        "snippet": _snippet(clean, match.start(), match.end())})
    return records


def unauthorized_numbers(final, question, steps):
    """便捷函数：直接返回未被授权的数字串集合（旧循环的用法，保持兼容）。"""
    authorized = build_authorized(question, steps)
    return {rec["number"] for rec in find_unauthorized(final, authorized)}


def describe(records, max_fields=3):
    """把未授权记录整理成给模型看的一句话说明（含上下文片段）。"""
    parts = []
    for rec in records:
        parts.append("%s（出现在「%s」）" % (rec["number"], rec["snippet"]))
    return "、".join(parts[:max_fields])
