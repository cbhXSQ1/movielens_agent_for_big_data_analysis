# -*- coding: utf-8 -*-
"""第 5 节 Agent：大模型可选层 —— 意图解析与措辞润色（**只做这两件事**）。

硬红线（本文件是第一现场）
--------------------------
1. 大模型只能做两件事：① 输入侧的意图/参数理解；② 输出侧的措辞润色。
2. 不能生成清洗规则 —— 规则永远来自 config/*.json（见 cleaning_rules.v1.json:22）。
3. 不能产出任何数字 —— 分数 / 行数 / 隔离数一律来自 driver 的真实结果。
4. 不可用时如实回落，不用它编一段话冒充运行结果。
5. 配置只读 —— 本文件只读字符串，不碰 config/*.json。

三层护栏（逐层加严）
--------------------
第一层｜结构性隔离：只有白名单里的「无事实句」才允许润色；`explain` 产出的
        三块数字内容（数据量变化 / 隔离去重修复 / 五维分数）**根本不进入大模型**。
第二层｜占位符模板：润色前把数字换成 {v0} {v1}…，它眼里没有数字可编，
        回填的值 100% 来自它自己拿到的文本。
第三层｜输出后校验：占位符数量一致 ⇒ 不得出现任何新数字 ⇒ 不得出现黑名单词
        ⇒ 长度上限。四条任一不过 ⇒ **丢弃润色结果，原样返回未润色文本**。
"""

import json
import re

from . import intent, llm_client


def last_reason():
    """给 agent.py 写 llm.note 用。"""
    return llm_client.last_error()


# ---------------------------------------------------------------------------
# ① 输入侧：意图 / 参数理解
# ---------------------------------------------------------------------------

INTENT_SYSTEM = """你是一个严格的「意图分类与参数抽取」器，服务于一个数据治理 Agent。

你只能输出一个 JSON 对象；不要输出任何解释文字，不要输出 Markdown 代码块标记。

规则：
1) intent 必须是且只能是以下之一（原样输出这些英文键）：
   clean_evaluate, task_status, task_result, get_samples, get_report,
   list_schemes, list_tasks, quick_demo, help, unknown
2) params 只能包含以下键，句子里没有的就省略：
   task_id（形如 20260925-184303-379a4b；句子里没有就不要给）,
   n（整数 1..200，表示要看几条样例）,
   table（users / movies / ratings 之一）,
   sample_type（cleaned / quarantine 之一）,
   dimensions（由 Accurate, Complete, Unique, Up-to-date, Consistent 组成的数组）
3) 严禁输出任何其它字段。尤其是：严禁输出清洗规则、阈值、公式、分数、行数、
   隔离数等任何业务数据 —— 那些必须由程序真实运行得出，你不能参与。
4) 句子里没有的信息一律省略，不要补全、不要推测。
5) **优先归类**：用户说话再口语化，只要语义与某个意图对应，就输出该意图
   （参考下方示例）。只有与全部意图无关的闲聊（问天气、聊日常等）才输出
   {"intent":"unknown","params":{}}。

## 示例（学习口语与意图的对应；params 照句中真实出现的填，没有就省略）：
- "用默认规则把数据洗一下，看看效果" → {"intent":"clean_evaluate","params":{}}
- "跑完没有？现在到哪一步了" → {"intent":"task_status","params":{}}
- "最后弄出来多少分" / "这次搞得怎么样" → {"intent":"task_result","params":{}}
- "把不合格的行挑几条出来看看" → {"intent":"get_samples","params":{"sample_type":"quarantine","table":"ratings"}}
- "随机抽几条没通过校验的数据展示一下" → {"intent":"get_samples","params":{"sample_type":"quarantine","table":"ratings"}}
- "留下来干净的也给我看几条" → {"intent":"get_samples","params":{"sample_type":"cleaned"}}
- "为什么会隔离这些行" → {"intent":"get_samples","params":{}}
- "解释一下为什么是这个分数" → {"intent":"get_report","params":{}}
- "都定义了哪些清洗方案" → {"intent":"list_schemes","params":{}}
- "之前跑过的都在吗" → {"intent":"list_tasks","params":{}}
- "先快速来一遍看看效果" → {"intent":"quick_demo","params":{}}
- "你好" / "今天天气如何" 等无关闲聊 → {"intent":"unknown","params":{}}
"""

ALLOWED_INTENTS = tuple(intent.INTENT_CN.keys())
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def _strip_fence(text):
    m = _FENCE_RE.match((text or "").strip())
    return (m.group(1) if m else text).strip()


def _sanitize(obj):
    """白名单校验：不认识的意图 / 越界的参数全部丢弃；非法结构返回 None ⇒ 回落规则。"""
    if not isinstance(obj, dict):
        return None
    name = obj.get("intent")
    if name not in ALLOWED_INTENTS:
        return None

    params = obj.get("params")
    if params in (None, ""):
        params = {}
    if not isinstance(params, dict):
        return None

    clean = {}

    tid = params.get("task_id")
    if isinstance(tid, str):
        m = intent.TASK_ID_RE.search(tid)
        if m:
            clean["task_id"] = m.group(0)     # 只认严格格式，防止塞怪东西

    n = params.get("n")
    if not isinstance(n, bool):
        try:
            iv = int(n)
            if 1 <= iv <= 200:                # 取样条数：既是"请求"，也要有上限
                clean["n"] = iv
        except (TypeError, ValueError):
            pass

    if params.get("table") in ("users", "movies", "ratings"):
        clean["table"] = params["table"]
    if params.get("sample_type") in ("cleaned", "quarantine"):
        clean["sample_type"] = params["sample_type"]

    dims = params.get("dimensions")
    if isinstance(dims, list):
        ok = [d for d in dims if d in intent.DIMS]
        if ok:
            clean["dimensions"] = ok

    return {
        "intent": name,
        "intent_cn": intent.INTENT_CN[name],
        "params": clean,
        "matched": [],
        "confidence": 0.75,
        "source": "llm",
    }


def parse_llm(text, cfg):
    """用大模型解析一句自然语言。**失败一律返回 None**，由调用方用规则结果。"""
    if cfg is None or not getattr(cfg, "usable", False):
        return None
    content, err = llm_client.chat(cfg, INTENT_SYSTEM, "用户原话：\n" + (text or ""),
                                   max_tokens=getattr(cfg, "max_tokens", 1024))
    if not content:
        return None                     # err 已由 llm_client 写入 last_error
    try:
        obj = json.loads(_strip_fence(content))
    except ValueError:
        llm_client.set_last_error("大模型输出不是合法 JSON（L4）")
        return None
    result = _sanitize(obj)
    if result is None:
        llm_client.set_last_error("大模型输出了白名单外的意图/参数，已整条丢弃")
    return result


# ---------------------------------------------------------------------------
# ② 输出侧：措辞润色（三层护栏）
# ---------------------------------------------------------------------------

POLISH_SYSTEM = """你是中文技术写作助手，负责把一段已经写好的中文提示语改得更通顺、更像人话。

硬性约束：
1) 只能润色措辞，**不得新增、删除或改动任何事实**（尤其不得改动数字、表名、任务状态、规则名）。
2) 文本中的占位符 {v0} {v1} 等必须原样保留，数量与顺序都不得改变。
3) 不得出现"修复了 / 已修复 / 修好 / 全部恢复正常"这类表述 —— 数据治理里「隔离」不等于「修复」。
4) 长度不得超过原文的 2 倍且不超过 300 字；不要加小标题、编号或 Markdown 符号。
5) 只输出润色后的文本本身，不要任何前言后记。
"""

#: 允许润色的发布点（白名单）。全部是**不含任何运行结果数字**的自述句。
#: 含数字的 explain.* 产出物一律不在此列 —— 它们物理上不会被送到大模型那里。
POLISH_SITES = ("task_submitted", "intent_unknown", "help", "parse_only")

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_PLACEHOLDER_RE = re.compile(r"\{v\d+\}")
#: 黑名单词：命中即丢弃润色结果（隔离 ≠ 修复；也不许暗示推测）
_BAD_PHRASES = (
    r"修复了", r"已修复", r"修好了", r"全部恢复", r"已经解决",
    r"根据我的经验", r"我猜测", r"我推断", r"通常情况下",
)


def _mask_numbers(text):
    """把数字换成占位符，返回 (模板, 原值列表)。**大模型因此看不到任何数字。**"""
    values = []

    def rep(m):
        values.append(m.group(0))
        return "{v%d}" % (len(values) - 1)

    return _NUM_RE.sub(rep, text), values


def _unmask(text, values):
    for i, v in enumerate(values):
        text = text.replace("{v%d}" % i, v)
    return text


def polish(text, site, cfg):
    """润色一段自述句。任何一道护栏不过，都**返回原文本**。

    返回 (最终文本, 失败原因 or None)。
    """
    if not text or cfg is None or not getattr(cfg, "usable", False):
        return text, "大模型不可用"
    if "polish" not in getattr(cfg, "use_for", ()):
        return text, "未开启润色"
    if site not in POLISH_SITES:
        return text, "不在润色白名单内（含结果的文本永不润色）"

    template, values = _mask_numbers(text)
    got, err = llm_client.chat(
        cfg, POLISH_SYSTEM,
        "场合：%s\n待润色文本：\n%s" % (site, template),
        max_tokens=min(getattr(cfg, "max_tokens", 400), 320))
    if not got:
        return text, err or "调用失败"

    got = _strip_fence(got).strip()

    # 先剥掉合法占位符再查数字 —— 否则 {v0} 里的 "0" 会被当成"新数字"，
    # 导致所有正常润色都被误判丢弃（实测踩到过这个坑）。
    residual = _PLACEHOLDER_RE.sub("", got)
    if len(_PLACEHOLDER_RE.findall(got)) != len(values):
        return text, "占位符被改动，已丢弃"
    if _NUM_RE.search(residual):
        return text, "润色结果中出现未 mask 的数字，已丢弃"
    for bad in _BAD_PHRASES:
        if re.search(bad, got):
            return text, "命中禁用措辞 %r，已丢弃" % bad
    if len(got) > max(2 * len(template), 300):
        return text, "润色结果过长，已丢弃"

    return _unmask(got, values), None
