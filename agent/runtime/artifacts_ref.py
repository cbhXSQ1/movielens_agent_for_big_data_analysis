# -*- coding: utf-8 -*-
"""回合产物引用（artifacts）：一次对话回合可确定地给出「这轮产生的文件引用列表」。

接口文档 §3.12。产物有两类来源：

  * **任务产物**（任务目录下的一套文件）：`result.json` /
    `metadata.json` / `counts.json` / `stats.json` / `status.json`、`report.md` /
    `report.json`、`metrics/{before,after,composite}.json`、`cleaned/<版本>/*.dat`、
    `quarantine/<版本>/*.dat` + `quarantine_summary.json` 等。任务现跑在远端，
    故有远端客户端时经 `get_task_result` 结果信封自带的 `artifacts[]`（取不到再退回
    远端 `list_task_files`）收集；无客户端时退回本机 `agent/resources.py`
    （`local_list_task_files`），**文件不存在就跳过**；
  * **方案依据**（方案文件）：本轮或该任务用到的清洗规则 / 评分方案
    （自定义或默认），作为「依据」附上；远端接入时以远端 `list_schemes` 判定存在性，
    未接时退回本机 `config/`。

产物引用对象（一条一个文件，稳定可去重）：

    {"id": "task:<task_id>:<relpath>" | "scheme:<name>",
     "title": "评估报告" 或文件名,
     "category": "数据|隔离|报告|指标|元数据|日志|其它"
                 "（远端结果信封自带时原样透传；本机回退按路径派生："
                 "报告|评分|证据|文件|依据|其它）",
     "kind": "json|md|dat|text|other|scheme",
     "source": "task_file" | "scheme",
     "task_id": "<task_id>" | null,
     "path": "<相对任务目录>" | null,
     "scheme_name": "<文件名>" | null}

本模块只做**纯组装**（不落库、不发事件、不触模型）：收集、分类、去重、合并。
持久化与事件推送在 `Runtime` 里按回合调用。
"""

import json
import os

from .. import resources

# ---------------------------------------------------------------------------
# 枚举常量（接口文档 §3.12）
# ---------------------------------------------------------------------------

CATEGORY_REPORT = "报告"
CATEGORY_SCORE = "评分"
CATEGORY_EVIDENCE = "证据"
CATEGORY_FILE = "文件"
CATEGORY_REFERENCE = "依据"
CATEGORY_OTHER = "其它"

SOURCE_TASK_FILE = "task_file"
SOURCE_SCHEME = "scheme"

KIND_SCHEME = "scheme"

#: 方案文件默认名（无法从 `tools` 取到时兜底；只用文件名，不带目录）。
_FALLBACK_DEFAULT_RULES = "cleaning_rules.v1.json"
_FALLBACK_DEFAULT_SCORING = "scoring_scheme.v1.json"

#: 友好标题表（按文件名；未命中一律回落到相对路径，不臆造）。
_BASE_TITLES = {
    "result.json": "运行结果",
    "metadata.json": "任务元数据",
    "counts.json": "数据量统计",
    "stats.json": "统计指标",
    "status.json": "任务状态",
    "published_hashes.json": "发布校验清单",
    "report.md": "评估报告",
    "report.json": "评估报告（结构化）",
    "quarantine_summary.json": "隔离汇总",
}

_METRIC_TITLES = {
    "before.json": "清洗前评分",
    "after.json": "清洗后评分",
    "composite.json": "综合评分",
}

_CLEANED_PREFIX = "清洗后"
_QUARANTINE_PREFIX = "隔离"

#: 内部文件（驱动 / 引擎自用，不给用户看）：不构建产物引用。
#:   * 按文件名：驱动日志、任务元数据、发布校验清单（`status.json` 保留）；
#:   * 按路径：`logs/` 目录下的全部文件（各作业的分步日志）。
_INTERNAL_BASENAMES = ("driver.log", "metadata.json", "published_hashes.json")
_INTERNAL_DIRS = ("logs",)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def normalize(relpath):
    """统一路径分隔符并去掉首尾斜杠（产物里一律用 `/`）。"""
    return str(relpath or "").replace("\\", "/").strip("/")


def is_internal_file(relpath):
    """该任务文件是否属**内部文件**（驱动/引擎自用，不进用户产物列表）。

    过滤规则（接口文档外的脚手架文件）：
      * 文件名命中 `driver.log` / `metadata.json` / `published_hashes.json`；
      * 相对路径的首个目录段为 `logs`（即 `logs/` 目录下的全部文件）。
    `status.json`、`result.json`、报告、评分、清洗/隔离数据等一律保留。
    """
    rel = normalize(relpath)
    if not rel:
        return True
    if basename(rel) in _INTERNAL_BASENAMES:
        return True
    return rel.split("/", 1)[0] in _INTERNAL_DIRS


def strip_internal(artifacts):
    """防御性再过滤：丢掉产物流水里的**内部文件**条目（返回新列表）。

    过滤规则上线前存下的旧对话，其产物列表里可能已经混进 `driver.log` /
    `logs/**` / `metadata.json` / `published_hashes.json`。读取 / 恢复时再过滤一次，
    保证它们不再出现（只处理任务产物，方案依据原样保留）。

    远端来源（`remote_source`，见 `build_task_artifact`）的条目**跳过**过滤：它们本就
    是服务端按同一套规则给出的**产品清单**，其中的日志是合法产物，不该在恢复时被误删。
    """
    out = []
    for item in artifacts or []:
        if not isinstance(item, dict):
            continue
        if not item.get("remote_source") \
                and item.get("source") == SOURCE_TASK_FILE \
                and is_internal_file(item.get("path")):
            continue
        out.append(dict(item))
    return out


def basename(path):
    """取路径最后一段（方案名统一按文件名归一）。"""
    rel = normalize(path)
    return rel.rsplit("/", 1)[-1] if rel else ""


# ---------------------------------------------------------------------------
# 分类 / 标题 / kind（确定性，只看文件名与路径）
# ---------------------------------------------------------------------------

def category_of(relpath):
    """按文件名 / 路径粗分产物类别（接口文档 §3.12）。

    `counts.json`（任意深度）→ 证据；`metrics/*` → 评分；`report.*` → 报告；
    `quarantine/*` → 证据；`cleaned/*` → 文件；其余 → 其它。
    """
    rel = normalize(relpath)
    base = basename(rel)
    if base == "counts.json":
        return CATEGORY_EVIDENCE
    if rel.startswith("metrics/"):
        return CATEGORY_SCORE
    if base in ("report.md", "report.json"):
        return CATEGORY_REPORT
    if rel.startswith("quarantine/"):
        return CATEGORY_EVIDENCE
    if rel.startswith("cleaned/"):
        return CATEGORY_FILE
    return CATEGORY_OTHER


def friendly_title(relpath):
    """给产物一个更友好的标题；不认识就回落成相对路径（不臆造）。"""
    rel = normalize(relpath)
    base = basename(rel)
    if base in _BASE_TITLES:
        return _BASE_TITLES[base]
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "metrics" and base in _METRIC_TITLES:
        return _METRIC_TITLES[base]
    if len(parts) >= 3 and parts[0] == "cleaned":
        return "%s %s（%s）" % (_CLEANED_PREFIX, os.path.splitext(base)[0],
                              parts[1])
    if len(parts) >= 3 and parts[0] == "quarantine":
        return "%s %s（%s）" % (_QUARANTINE_PREFIX, os.path.splitext(base)[0],
                              parts[1])
    return rel


def short_task_id(task_id):
    """任务编号的**短后缀**（同名产物标题去歧义用）：优先取最后一段。

    任务号形如 `20260101-000000-aaaaaa`（见 `constants.TASK_ID_PATTERN`），取
    末段 `aaaaaa`；不符合该形状的（如 Hadoop 编号 `2026-jd01`）退化为整串。
    """
    text = str(task_id or "").strip()
    if not text:
        return ""
    tail = text.rsplit("-", 1)[-1]
    return tail or text[-6:]


def _disambiguation_base(item):
    """同名去歧义用的**稳定基名**：优先保留条目已有标题（远端透传），缺省按路径派生。

    已经补过 `（<短任务号>）` 后缀的标题先剥掉后缀，保证多回合重复处理幂等，
    不会越加越长。
    """
    title = str(item.get("title") or "").strip()
    if not title:
        return friendly_title(item.get("path"))
    suffix = short_task_id(item.get("task_id"))
    if suffix:
        tail = "（%s）" % suffix
        if title.endswith(tail):
            return title[:-len(tail)]
    return title


def disambiguate_titles(artifacts):
    """同名产物去歧义：**不同任务**的同名文件在标题上补短任务号后缀。

    同一段对话里两个任务各产出一个 `status.json`（基础标题都是「任务状态」）时，
    标题会变得一模一样；此处对**基础标题**（优先取条目已有标题，缺省按相对路径
    重算；已带本模块后缀的先剥掉，故跨回合幂等）按任务分组，只有该标题来自多个
    任务时才补 `（<短任务号>）`，单一任务保持原标题。
    只处理任务产物（`source=task_file`）；返回新列表，不改动入参。
    """
    items = list(artifacts or [])
    groups = {}
    for item in items:
        if item.get("source") != SOURCE_TASK_FILE:
            continue
        base = _disambiguation_base(item)
        groups.setdefault(base, set()).add(item.get("task_id"))
    out = []
    for item in items:
        entry = dict(item)
        if entry.get("source") == SOURCE_TASK_FILE:
            base = _disambiguation_base(entry)
            if len(groups.get(base) or ()) > 1:
                suffix = short_task_id(entry.get("task_id"))
                entry["title"] = ("%s（%s）" % (base, suffix)) if suffix else base
            else:
                entry["title"] = base
        out.append(entry)
    return out


def kind_of_task_file(relpath):
    """任务产物文件的 kind（复用 §3.11.4 的 `resources.kind_of`）。"""
    return resources.kind_of(basename(relpath))


# ---------------------------------------------------------------------------
# 构造一条产物引用
# ---------------------------------------------------------------------------

def build_task_artifact(task_id, relpath, kind=None, title=None, category=None,
                        remote_source=False):
    """造一条任务产物引用（`id` 稳定：`task:<task_id>:<relpath>`）。

    `title` / `category` 用于**透传远端**给的值（远端结果信封自带）；缺省时才按
    路径派生（本机回退保持原行为）。

    `remote_source=True` 时附一个**加性**内部字段 `remote_source: True`：标记该条来自
    远端服务端的产品清单（已按同一套规则过滤），供 `strip_internal` 在恢复时跳过内部
    过滤。前端类型忽略未知字段，故不受影响；本机来源的条目不含该字段，行为与既有一致。
    """
    rel = normalize(relpath)
    if kind is None:
        kind = kind_of_task_file(rel)
    item = {
        "id": "task:%s:%s" % (task_id, rel),
        "title": title if title else friendly_title(rel),
        "category": category if category else category_of(rel),
        "kind": kind,
        "source": SOURCE_TASK_FILE,
        "task_id": task_id,
        "path": rel,
        "scheme_name": None,
    }
    if remote_source:
        item["remote_source"] = True
    return item


def build_scheme_artifact(name):
    """造一条方案依据引用（`id` 稳定：`scheme:<文件名>`）。"""
    base = basename(name)
    return {
        "id": "scheme:%s" % base,
        "title": base,
        "category": CATEGORY_REFERENCE,
        "kind": KIND_SCHEME,
        "source": SOURCE_SCHEME,
        "task_id": None,
        "path": None,
        "scheme_name": base,
    }


# ---------------------------------------------------------------------------
# 收集：任务产物 / 方案依据
# ---------------------------------------------------------------------------

def _call_remote(client, name, args):
    """安全调远端：任何异常 / 非对象信封都吞掉返回 None（绝不让收集拖垮回合）。"""
    try:
        env = client.call_tool(name, args)
    except Exception:
        return None
    return env if isinstance(env, dict) else None


def remote_task_entries(client, task_id, result=None):
    """远端任务的产条目清单。

    优先用 `get_task_result` 结果信封自带的 `artifacts[]`（服务端已按同一套排除规则
    过滤控制文件 / 隐藏文件 / 校验和 / 临时残留），取不到再退回远端 `list_task_files`。
    两者都取不到就返回空表（不抛）。
    """
    env = result
    if not (isinstance(env, dict) and env.get("ok")
            and isinstance(env.get("artifacts"), list)):
        env = _call_remote(client, "get_task_result", {"task_id": task_id})
    if isinstance(env, dict) and env.get("ok") \
            and isinstance(env.get("artifacts"), list):
        return env["artifacts"]
    listing = _call_remote(client, "list_task_files", {"task_id": task_id})
    if isinstance(listing, dict) and listing.get("ok"):
        return listing.get("files") or []
    return []


def collect_task_artifacts(task_id, lister=None, client=None, result=None):
    """列出某任务的产物文件并各造一条引用；目录/文件不存在就跳过。

    有远端客户端（`client`）时从**远端**收集：优先 `get_task_result` 结果信封自带的
    `artifacts[]`，取不到再退回远端 `list_task_files`。远端结果信封的 `artifacts[]`
    已是**产品清单**（服务端已排除控制文件 / 隐藏文件 / 校验和 / 临时残留，且其中
    本就含日志），故远端路径**不再叠加**本地「内部文件」过滤，原样信任服务端清单。
    无客户端时退回本机 `lister(task_id)`（缺省 `resources.local_list_task_files`，
     可注入假实现供单测），本机路径仍用 `is_internal_file` 剔除驱动 / 日志等内部文件。
    远端路径造出的引用带加性标记 `remote_source=True`，供 `strip_internal` 在恢复时
    跳过内部过滤（二者配套，保证远端日志刷新后不消失）。
    无论哪条路径，每条引用都带 `task_id`；任何错误都吞掉返回空表。
    """
    if not task_id:
        return []
    if client is not None:
        entries = remote_task_entries(client, task_id, result=result)
        remote = True
    else:
        lister = lister or resources.local_list_task_files
        try:
            entries = (lister(task_id) or {}).get("files") or []
        except Exception:
            return []
        remote = False
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path")
        if not rel:
            continue
        if remote:
            # 远端已按同一套规则给出类别 / 标题（远端词汇）；原样透传，缺失才派生。
            out.append(build_task_artifact(
                task_id, rel, kind=entry.get("kind"),
                title=entry.get("title"), category=entry.get("category"),
                remote_source=True))
        else:
            if is_internal_file(rel):
                continue
            out.append(build_task_artifact(task_id, rel, kind=entry.get("kind")))
    return out


def read_task_result(task_id, client=None):
    """读任务的**真实结果**；不存在 / 损坏一律返回 None。

    有远端客户端时经远端 `get_task_result` 取结果信封；无客户端时读本机任务目录下的
    `result.json`。只读、不抛：无模型时的确定性收尾据此拼真实计数与五维分，取不到的
    部分不写。
    """
    if not task_id:
        return None
    if client is not None:
        return _call_remote(client, "get_task_result", {"task_id": task_id})
    try:
        path = resources.resolve_file(task_id, "result.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def scheme_path(name):
    """方案文件在 `config/` 下的真实路径。"""
    return os.path.join(resources.config_root(), basename(name))


def remote_scheme_names(client):
    """从远端 `list_schemes` 归纳已登记方案名（宽松形状）；取不到返回 None。

    schemes 条目既可能是字符串文件名，也可能是带 `name` / `file` 的对象。
    """
    env = _call_remote(client, "list_schemes", {})
    if not (isinstance(env, dict) and env.get("ok")):
        return None
    values = None
    for key in ("schemes", "items", "list", "data"):
        if isinstance(env.get(key), list):
            values = env[key]
            break
    if values is None:
        return set()
    names = set()
    for item in values:
        if isinstance(item, str):
            names.add(basename(item))
        elif isinstance(item, dict):
            names.add(basename(item.get("name") or item.get("file") or ""))
    names.discard("")
    return names


def scheme_exists(name, client=None, available=None):
    """方案是否确实存在（不存在就当没用到，不造引用）。

    有远端客户端时以远端 `list_schemes`（或调用方给的 `available` 集合）为准，
    远端方案不再依赖本机 `config/`；远端查不到时退回本机文件判定。
    """
    base = basename(name)
    if not base:
        return False
    if available is not None:
        return base in set(available)
    if client is not None:
        remote = remote_scheme_names(client)
        if remote is not None:
            return base in remote
    return os.path.isfile(scheme_path(base))


def collect_scheme_artifacts(names, client=None, available=None):
    """给每个存在的方案名造一条「依据」引用（保序去重；不存在跳过）。

    存在性判定：`available`（调用方已知方案集合）优先，其次远端 `list_schemes`，
    都取不到时退回本机 `config/`（未接远端的既有行为不变）。
    """
    if available is not None:
        remote = set(available)
    elif client is not None:
        remote = remote_scheme_names(client)
    else:
        remote = None
    out = []
    seen = set()
    for name in names or []:
        base = basename(name)
        if not base or base in seen:
            continue
        if remote is not None:
            exists = base in remote
        else:
            exists = os.path.isfile(scheme_path(base))
        if not exists:
            continue
        seen.add(base)
        out.append(build_scheme_artifact(base))
    return out


def default_scheme_names():
    """默认清洗规则 / 评分方案的文件名（唯一来源：`agent.tools` 常量）。"""
    try:
        from .. import tools
        return (basename(tools.DEFAULT_RULES),
                basename(tools.DEFAULT_SCORING))
    except Exception:                                  # pragma: no cover
        return _FALLBACK_DEFAULT_RULES, _FALLBACK_DEFAULT_SCORING


# ---------------------------------------------------------------------------
# 任务编号 / 方案名：从对话状态里归纳
# ---------------------------------------------------------------------------

def task_ids_of(context, long_tasks=None, extra=None):
    """归纳这段对话**已发起**的任务编号（保序去重）。

    来源：调用方补的 `extra` → 对话上下文 `task_ids`（本模块持久化）→
    上下文 `task_id`（最新）→ 长任务记录的 `hadoop_task_id`。
    """
    out = []

    def add(tid):
        if tid and tid not in out:
            out.append(tid)

    for tid in extra or []:
        add(tid)
    for tid in (context or {}).get("task_ids") or []:
        add(tid)
    add((context or {}).get("task_id"))
    for record in (long_tasks or {}).values():
        if isinstance(record, dict):
            add(record.get("hadoop_task_id"))
    return out


def _push_name(bucket, value):
    base = basename(value)
    if base and base not in bucket:
        bucket.append(base)


def scheme_names_of(opts, long_tasks=None, extra_args_list=None,
                    has_tasks=False):
    """归纳本轮 / 该任务用到的方案文件名（清洗规则 + 评分方案）。

    来源：对话运行参数快照 `opts` → 长任务记录的参数 → 调用方补的本轮参数。
    只要对话**发起过任务**（`has_tasks`），缺失的一侧用**默认方案**补齐
    （「自定义或默认」都要作为依据呈现）。
    """
    rules, scoring = [], []
    opts = opts or {}
    _push_name(rules, opts.get("rules"))
    _push_name(scoring, opts.get("scoring"))
    for record in (long_tasks or {}).values():
        if not isinstance(record, dict):
            continue
        args = record.get("args") or {}
        _push_name(rules, args.get("rules"))
        _push_name(scoring, args.get("scoring"))
    for args in extra_args_list or []:
        if not isinstance(args, dict):
            continue
        _push_name(rules, args.get("rules"))
        _push_name(scoring, args.get("scoring"))
    if has_tasks:
        default_rules, default_scoring = default_scheme_names()
        if not rules:
            _push_name(rules, default_rules)
        if not scoring:
            _push_name(scoring, default_scoring)
    return rules + scoring


# ---------------------------------------------------------------------------
# 去重 / 合并
# ---------------------------------------------------------------------------

def dedupe(artifacts):
    """按 `id` 去重（保留首次出现顺序）。"""
    seen = set()
    out = []
    for item in artifacts or []:
        aid = item.get("id")
        if aid in seen:
            continue
        seen.add(aid)
        out.append(item)
    return out


def merge(known, incoming):
    """把「已知列表」与「新收集列表」合并；返回 (完整列表, 新增列表)。

    * 完整列表按 `id` 去重（已知在前，顺序稳定）；
    * 新增列表只含 `id` 不在已知里的条目 —— 供 `artifact_added` 只发新增。
    """
    known = list(known or [])
    known_ids = set(a.get("id") for a in known)
    full = dedupe(known + list(incoming or []))
    added = [a for a in full if a.get("id") not in known_ids]
    return full, added
