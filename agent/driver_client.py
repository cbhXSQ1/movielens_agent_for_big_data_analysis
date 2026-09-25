# -*- coding: utf-8 -*-
"""第 5 节 Agent：底层调用 Hadoop 侧 driver CLI。

只做两件事：调用 driver、解析它 stdout 上的那个 JSON 信封。
不做任何数据加工，不补任何数字——拿不到就是拿不到。

driver 契约（docs/hadoop/agent-interface.md v1.0）：
    python3 hadoop/driver/run_task.py <子命令> [选项]
    stdout 只输出一个 JSON 信封，日志走 stderr
    退出码：0 成功 / 2 参数非法 / 3 任务失败 / 4 不存在 / 5 未完成 / 6 版本冲突
"""

import json
import os
import subprocess
import sys

# 仓库根目录：本文件在 <repo>/agent/ 下，上一级就是仓库根
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRIVER = os.path.join(REPO_ROOT, "hadoop", "driver", "run_task.py")
QUICK_CLEAN = os.path.join(REPO_ROOT, "hadoop", "tools", "quick_clean.py")

# 退出码（与接口文档 §2 一致）
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 3
EXIT_NOT_FOUND = 4
EXIT_NOT_FINISHED = 5
EXIT_CONFLICT = 6

# driver 输出不是 JSON、或根本没跑起来时，用这些错误码如实上报
# （不属于 v1.0 错误码表，是 Agent 侧的传输层错误）
CODE_NO_OUTPUT = "DRIVER_NO_OUTPUT"
CODE_BAD_OUTPUT = "DRIVER_BAD_OUTPUT"
CODE_UNREACHABLE = "DRIVER_UNREACHABLE"
CODE_TIMEOUT = "DRIVER_TIMEOUT"


def _tail(text, n=800):
    """截取日志尾部，避免错误信息过长。"""
    if not text:
        return ""
    return text[-n:]


def _run(script, args, timeout=None):
    """执行一个 Hadoop 侧脚本，返回 (JSON 信封 dict, 退出码)。"""
    cmd = [sys.executable, script] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {
            "code": CODE_TIMEOUT,
            "message": "driver 超过 %.0f 秒未返回" % (timeout or 0),
        }}, None
    except OSError as exc:
        return {"ok": False, "error": {
            "code": CODE_UNREACHABLE,
            "message": "无法执行 driver：%s" % exc,
            "cmd": " ".join(cmd),
        }}, None

    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": False, "error": {
            "code": CODE_NO_OUTPUT,
            "message": "driver stdout 为空（详情看 stderr）",
            "stderr": _tail(proc.stderr),
        }}, proc.returncode

    try:
        envelope = json.loads(out)
    except ValueError:
        return {"ok": False, "error": {
            "code": CODE_BAD_OUTPUT,
            "message": "driver stdout 不是合法 JSON",
            "raw": out[:500],
            "stderr": _tail(proc.stderr),
        }}, proc.returncode

    if not isinstance(envelope, dict):
        return {"ok": False, "error": {
            "code": CODE_BAD_OUTPUT,
            "message": "driver stdout 不是 JSON 对象",
            "raw": out[:500],
        }}, proc.returncode

    # 把退出码挂在信封上，方便上层判断（下划线开头 = Agent 侧附加字段）
    envelope["_exit_code"] = proc.returncode
    envelope["_stderr_tail"] = _tail(proc.stderr, 300)
    return envelope, proc.returncode


def call_driver(subcommand, options=None, timeout=None):
    """调用 driver 的 8 个契约子命令之一。

    subcommand: validate / schemes / start / status / result / samples / report / tasks
    options:    dict，键不带前导 --；值为 True 表示开关型参数（如 foreground）
    """
    args = [subcommand]
    for key, value in (options or {}).items():
        if value is None or value is False:
            continue
        args.append("--" + key.replace("_", "-"))
        if value is not True:
            args.append(str(value))
    return _run(DRIVER, args, timeout=timeout)[0]


def call_quick_clean(options=None, timeout=None):
    """调用附加工具 quick_clean（演示性清洗，不经过 Hadoop）。

    注意：它不维护任务状态、不做发布与版本冲突保护，只用于演示与快速取数。
    权威结果仍然是 start + result。
    """
    args = []
    for key, value in (options or {}).items():
        if value is None or value is False:
            continue
        args.append("--" + key.replace("_", "-"))
        if value is not True:
            args.append(str(value))
    return _run(QUICK_CLEAN, args, timeout=timeout)[0]


def is_ok(envelope):
    """信封是否表示成功。"""
    return bool(isinstance(envelope, dict) and envelope.get("ok"))


def error_of(envelope):
    """取出错误信息 dict；没有则返回 None。"""
    if not isinstance(envelope, dict):
        return None
    err = envelope.get("error")
    return err if isinstance(err, dict) else None
