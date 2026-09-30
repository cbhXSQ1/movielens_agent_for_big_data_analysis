# -*- coding: utf-8 -*-
"""driver/cli.py —— 参数解析（阶段一：形态拆分；阶段二：声明式规格校验）。

契约：parse_args 只做**形态拆分**（--key value / --key=value / --flag / 位置参数）；
parse_argv 在此基础上按「声明式规格表」集中做默认值、必填、取值枚举、整型/下限
校验 —— 校验失败的报错文案与旧实现**逐字一致**（阶段二验收：错误信封抽样比对
USAGE / TASK_NOT_FOUND / CONFIG_INVALID 各一例，见 driver-refactor-plan.md §6）。

规格表格式（每命令一个 dict，见 commands.py 各命令类的 spec 属性）：
    "task-id":     {"required": True}                          必填；缺失报 "<cmd> 需要 --task-id"
    "scope":       {"default": "full", "values": (...),        缺省值 + 枚举
                    "values_msg": "--scope 必须是 full 或 sample，得到 %r"}
    "n":           {"default": "5", "int": True, "min": 0,     整型转换 + 下界
                    "int_msg": "--n 必须是整数", "min_msg": "--n 不能为负"}
    "force":       {"flag": True}                              纯开关（不应用默认值）
    未列出的选项：照常透传，不校验（未知选项的收紧留到阶段四清理，避免行为变更）。

说明：CliError 从 run_task.py 迁到这里（解析层抛出的异常与执行层共用同一个类；
run_task.py 顶部 `from driver.cli import CliError` 再导出，测试的 rt.CliError 不受影响）。
"""

import os


class CliError(Exception):
    """用户输入/配置用法错误；main() 捕获后输出 USAGE 类 JSON 信封。"""

    def __init__(self, code, message, **extra):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.extra = extra


def parse_args(argv):
    """把 argv 拆成 (选项 dict, 位置参数 list)。"""
    opts, i, positional = {}, 0, []
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
        else:
            positional.append(a)
        i += 1
    return opts, positional


class ParsedCommand(object):
    """一次解析的完整产物：命令名 + 选项（已应用默认值/转换）+ 位置参数。"""

    def __init__(self, name, options, operands):
        self.name = name
        self.options = options
        self.operands = operands


def parse_argv(argv, name, spec):
    """按命令规格解析参数；校验失败抛 CliError("USAGE", ...)（文案逐字对齐旧实现）。

    空串语义对齐旧代码的三种写法（阶段二红线：行为不变）：
      * `--task-id ""`        → 视同缺失（旧 `if not tid` 报缺参）；
      * scope 类 `or 默认`    → 空串回落默认（spec 标 or_default）；
      * type 类 `get(默认)`   → 空串保留、走取值校验（旧实现报 USAGE）。
    """
    opts, operands = parse_args(argv)
    for key, rule in (spec or {}).items():
        if rule.get("flag"):
            continue
        present = key in opts
        if rule.get("required"):
            if not (present and opts[key] not in ("", None)):
                raise CliError("USAGE", rule.get("required_msg")
                               or "%s 需要 --task-id" % name)
        elif not present or (rule.get("or_default") and opts.get(key) in ("", None)):
            if "default" in rule:
                opts[key] = rule["default"]
            else:
                continue
            # 注：应用默认值后**不跳出**，继续走 values/int 校验 ——
            # 默认值同样要兑现类型（如 n 的 int 转换）。审查发现：
            # 早期版本在这里 continue，导致 samples 缺省 --n 时 n 保持
            # 字符串 "5"，rows[:n] 抛 TypeError（退出码 1、无信封）。
        value = opts[key]
        if "values" in rule and value not in rule["values"]:
            msg = rule["values_msg"]
            raise CliError("USAGE", msg % value if "%" in msg else msg)
        if rule.get("int"):
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise CliError("USAGE", rule.get("int_msg", "--%s 必须是整数" % key))
            if "min" in rule and value < rule["min"]:
                raise CliError("USAGE", rule.get("min_msg", "--%s 不能小于 %s"
                                                 % (key, rule["min"])))
            opts[key] = value
    return ParsedCommand(name, opts, operands)


def resolve_path(p, default):
    """相对路径以仓库根为基准（与 run_task.py 原实现同义）。"""
    p = p or default
    if os.path.isabs(p):
        return p
    return os.path.join(_repo_root(), p)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))