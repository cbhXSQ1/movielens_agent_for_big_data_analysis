# -*- coding: utf-8 -*-
"""mcpserver/dispatch.py —— 参数校验、处理器注册与信封落地（唯一的调用入口）。

分工约定（各开发段落都按这条接线，别各写一套）：

  * **工具表**（`tools.py`）只描述"有什么能力、参数长什么样"；
  * **业务处理器**（writers / readers / tasks）只做判断与执行，签名统一为
        `handler(args: dict, ctx) -> dict`
    成功时返回**业务字段**（不含 `ok` / `interface_version`），失败时抛
    `contract.StructuredError`；
  * **本模块**负责：按工具表校验并补默认值 → 调用处理器 → 包成成功信封；
    处理器抛错 → 包成失败信封；任何未预期异常 → `INTERNAL_ERROR` 信封。

为什么要这层：参数校验与信封形状必须只有一处实现，否则二十个工具会长出二十种
参数容错方式；而"业务判定"留在处理器里，才能单独测试。

本模块不导入官方协议包；服务层（server.py）只做协议映射，调用本模块。
"""

import importlib
import json

from .contract import (
    CODE_FORBIDDEN_PATH,
    CODE_INTERNAL_ERROR,
    CODE_USAGE,
    StructuredError,
    envelope_error,
    envelope_ok,
    valid_task_id,
)
from .tools import TOOLS, get_tool

#: 处理器注册表：工具名 → 可调用对象。
HANDLERS = {}

#: 处理器模块清单（服务层启动时按此加载；测试可只加载需要的模块）。
HANDLER_MODULES = (
    "mcpserver.writers",
    "mcpserver.readers",
    "mcpserver.tasks",
    # 迭代二的机器学习能力（六个：三类训练、预测、两个只读查询）。
    # 放在这里作为唯一来源，服务层不必再自己拼清单。
    "mcpserver.ml",
)


class ToolContext(object):
    """一次调用共享的上下文（仓库根、环境、运行期配置、启动时间）。

    处理器需要"在哪里执行、按什么配置执行"时从这里取，**不要**各处自己算仓库根。
    """

    def __init__(self, repo_root, env=None, config=None, started_at=None):
        self.repo_root = repo_root
        self.env = dict(env or {})
        self.config = dict(config or {})
        self.started_at = started_at

    def as_server_info(self):
        """给能力自描述用的服务信息；只汇报真实存在的值，不填占位。"""
        info = {}
        for key in ("name", "version"):
            if self.config.get(key):
                info[key] = self.config[key]
        if self.started_at:
            info["started_at"] = self.started_at
        return info


def register(name, handler, module=None):
    """登记一个处理器；重名直接报错（避免两份实现悄悄互相覆盖）。

    `module` 只用于排障时说明处理器来自哪一段开发，不影响分派。
    """
    if name in HANDLERS:
        raise RuntimeError("工具处理器重复登记：%s" % name)
    if get_tool(name) is None:
        raise RuntimeError("登记了工具表里不存在的工具：%s" % name)
    HANDLERS[name] = handler
    HANDLERS_REGISTRY_META.setdefault(name, module or getattr(handler, "__module__", ""))


#: 工具名 → 来源模块（仅排障用）。
HANDLERS_REGISTRY_META = {}


def load_handlers(modules=HANDLER_MODULES):
    """按模块清单加载处理器；模块需导出 `HANDLERS` 字典。

    加载是幂等的：重复调用不会重复登记（第二次会跳过已登记的名字）。
    """
    loaded = []
    for mod_name in modules:
        if mod_name in loaded:
            continue
        module = importlib.import_module(mod_name)
        table = getattr(module, "HANDLERS", None)
        if not table:
            raise RuntimeError("处理器模块未导出 HANDLERS：%s" % mod_name)
        for name, fn in table.items():
            if name in HANDLERS:
                continue
            register(name, fn, module=mod_name)
        loaded.append(mod_name)
    return HANDLERS


def reset_handlers():
    """清空注册表（测试用，避免用例之间互相影响）。"""
    HANDLERS.clear()
    HANDLERS_REGISTRY_META.clear()


# ---------------------------------------------------------------------------
# 参数校验（唯一实现）
# ---------------------------------------------------------------------------

_MISSING = object()


def _coerce_int(param, value):
    """整数参数：收窄成真正的整数；非法取值一律报参数错误，不做静默兜底。"""
    if isinstance(value, bool):
        raise StructuredError(CODE_USAGE, "%s 需要整数，收到布尔值" % param["name"])
    if isinstance(value, int):
        num = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        num = int(value.strip())
    else:
        raise StructuredError(CODE_USAGE, "%s 需要整数，收到 %r" % (param["name"], value))
    if param.get("minimum") is not None and num < param["minimum"]:
        raise StructuredError(
            CODE_USAGE, "%s 不能小于 %s，收到 %s"
            % (param["name"], param["minimum"], num))
    if param.get("maximum") is not None and num > param["maximum"]:
        raise StructuredError(
            CODE_USAGE, "%s 不能大于 %s，收到 %s"
            % (param["name"], param["maximum"], num))
    return num


def _coerce_bool(param, value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "no", "off"):
        return False
    raise StructuredError(CODE_USAGE, "%s 需要布尔值，收到 %r" % (param["name"], value))


def validate_args(tool, args):
    """按工具表校验参数并补默认值；返回归一后的参数字典。

    规则（宁严不松，跨版本混用风险由本端挡住）：
      * 未声明的参数一律拒绝 —— 不静默忽略，否则用户的意图会被悄悄丢掉；
      * 必填缺失、枚举越界 → `USAGE`，说明里写清可选值；
      * 空串视同未提供（与 driver 的既有语义一致），回落默认值。
    """
    args = dict(args or {})
    declared = {param["name"]: param for param in tool["params"]}

    unknown = [key for key in args if key not in declared]
    if unknown:
        raise StructuredError(
            CODE_USAGE,
            "不认识的参数：%s。可用参数：%s"
            % ("、".join(sorted(unknown)),
               "、".join(sorted(declared)) or "（无）"))

    out = {}
    for name, param in declared.items():
        value = args.get(name, _MISSING)
        if value is None or (isinstance(value, str) and not value.strip()):
            value = _MISSING
        if value is _MISSING:
            if param.get("required"):
                raise StructuredError(CODE_USAGE, "缺少必填参数：%s" % name,
                                      param=name)
            if "default" in param and param["default"] is not None:
                out[name] = param["default"]
            continue
        if param["type"] == "enum":
            if value not in param.get("values", ()):
                raise StructuredError(
                    CODE_USAGE,
                    "%s 必须是 %s，收到 %r"
                    % (name, " 或 ".join(param.get("values", ())), value),
                    param=name)
            out[name] = value
        elif param["type"] == "int":
            out[name] = _coerce_int(param, value)
        elif param["type"] == "bool":
            out[name] = _coerce_bool(param, value)
        elif param["type"] == "object":
            # 对象参数（例如算法超参）：只接受真正的对象；其余形状一律拒绝 ——
            # 不替调用方猜"字符串里是不是塞了个 JSON"，那种宽容会让拼错的地方悄悄生效。
            if not isinstance(value, dict):
                raise StructuredError(
                    CODE_USAGE, "%s 需要对象（键值对），收到 %s" % (name, type(value).__name__),
                    param=name)
            out[name] = dict(value)
        else:
            # 字符串参数收到结构化值时的归一：协议层会把"看起来像 JSON 的字符串"
            # 自动解析成对象（例如方案内容 `{"a":1}`），到这里就成了字典。若直接
            # `str()` 会变成单引号的 Python 表示，业务侧再按 JSON 解析必然失败，
            # 而且报错会指向"内容不是合法 JSON"——真正的锅在参数形态上，极易误导。
            # 因此在唯一入口统一成 JSON 文本，业务侧只管按 JSON 处理。
            out[name] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    _check_pathish_params(out)
    _check_cross_param(tool["name"], out)
    return out


#: 需要做"单层目录名"形状校验的参数（执行侧会把它们直接拼进路径）。
_TASK_ID_PARAMS = ("task_id", "from_task", "model_id")
#: 需要做"纯文件名"形状校验的参数（方案文件名）。
_PLAIN_NAME_PARAMS = ("name",)


def _looks_like_path(value):
    """是否像一条路径（含分隔符、上级跳转、绝对路径、反斜杠或空字节）。"""
    if not isinstance(value, str):
        return False
    if value.startswith(("/", "\\")) or ":" in value[:3] and value[1:2] == ":":
        return True
    return any(ch in value for ch in ("/", "\\", "\x00")) or ".." in value


def _check_pathish_params(args):
    """路径类参数的形状校验：**唯一入口**，在这里拦住越界用法。

    为什么放在分派层：执行侧会把任务编号与方案文件名直接拼进文件路径，而调用这些能力的
    处理器有好几个（有的还借执行侧命令）。此前只有"自己读文件"的那几个做了校验，
    借命令的那几个漏了 —— 于是「查某个任务的状态」可以被用来读任务根之外任意目录里
    名为 status.json 的文件（实测可行）。集中在这里判一次，二十几项能力全覆盖，
    也不会因为将来新增能力而漏掉。
    """
    for name in _TASK_ID_PARAMS:
        value = args.get(name)
        if value in (None, ""):
            continue
        if _looks_like_path(value):
            raise StructuredError(
                CODE_FORBIDDEN_PATH,
                "%s 看起来是一条路径（%r）：任务编号只能是单层编号，不接受分隔符、上级跳转或绝对路径"
                % (name, value), param=name)
        if not valid_task_id(value):
            raise StructuredError(
                CODE_USAGE,
                "%s 不是合法的任务编号：%r（合法形状为字母数字开头、可含点划线下划线、不超过 64 个字符）"
                % (name, value), param=name)

    for name in _PLAIN_NAME_PARAMS:
        value = args.get(name)
        if value in (None, ""):
            continue
        if _looks_like_path(value) or not value.endswith(".json"):
            raise StructuredError(
                CODE_FORBIDDEN_PATH,
                "%s 必须是方案文件名（形如 scoring_scheme.v1.json），收到 %r" % (name, value),
                param=name)


def _check_cross_param(tool_name, args):
    """跨参数的条件必填（工具表表达不了，集中在这里判，避免散落各处）。"""
    if tool_name == "score_task" and args.get("source") == "task" and not args.get("from_task"):
        raise StructuredError(
            CODE_USAGE,
            "评分对象为「指定任务」时必须给出 from_task（来源任务编号）",
            param="from_task")


# ---------------------------------------------------------------------------
# 调用入口
# ---------------------------------------------------------------------------

def call_tool(name, args, ctx=None):
    """统一调用入口：校验 → 执行 → 包信封。

    返回形如 `{"ok": true, "interface_version": …, **业务字段}`，失败时
    `{"ok": false, "interface_version": …, "error": {code, message, …}}`。
    """
    tool = get_tool(name)
    if tool is None:
        return envelope_error(
            CODE_USAGE,
            "不认识的能力：%s。可用能力：%s" % (name, "、".join(t["name"] for t in TOOLS)))
    handler = HANDLERS.get(name)

    try:
        kwargs = validate_args(tool, args)
        if handler is None:
            return envelope_error(
                CODE_INTERNAL_ERROR,
                "能力 %s 尚未接入执行（处理器未登记）" % name)
        payload = handler(kwargs, ctx) or {}
        if not isinstance(payload, dict):
            raise StructuredError(
                CODE_INTERNAL_ERROR,
                "能力 %s 的处理器返回了非字典结果" % name)
        return envelope_ok(**payload)
    except StructuredError as exc:
        return exc.to_envelope()
    except Exception as exc:                      # noqa: BLE001 —— 兜底不得吞掉信息
        return envelope_error(
            CODE_INTERNAL_ERROR,
            "能力 %s 执行时出现未预期的错误：%s: %s"
            % (name, type(exc).__name__, exc))


def available_tools():
    """已登记处理器、可以真正调用的工具名（用于健康检查与排障）。"""
    return tuple(name for name in (t["name"] for t in TOOLS) if name in HANDLERS)
