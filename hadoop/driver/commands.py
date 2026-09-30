# -*- coding: utf-8 -*-
"""driver/commands.py —— 各子命令一个类 + 命令注册表（阶段一/二落地）。

迁移说明：
  * 阶段一：本文件内容 == run_task.py 原 cmd_* 函数体**原样搬入**；
  * 阶段二：参数校验集中到各命令类的 `spec` 声明式规格（cli.parse_argv 统一
    应用默认值 / 必填 / 取值枚举 / 整型下限），命令体只留业务逻辑；
  * 例外（保序）：`report` 的 --format 校验**留在命令体** —— 旧实现先查任务
    存在（TASK_NOT_FOUND 优先于 USAGE），规格化前置会改变该顺序（阶段一至四
    不允许行为变更，见 driver-refactor-plan.md §6）；
  * 用到 run_task 模块的辅助函数处一律 `rt.<name>` 在**调用时**解析
    （避免与 run_task.py 的导入形成循环陷阱）；`__file__` 语义修正点：
    后台子进程入口必须指向 run_task.py 本身（`_RUN_TASK`）。
"""

import io
import json
import os
import subprocess
import sys

from engine.config_loader import ConfigError, load_schemes
from engine.pipeline import TABLE_FILES

from driver.cli import resolve_path
from driver import run_task as rt

DEFAULT_RULES = os.path.join("config", "cleaning_rules.v1.json")
DEFAULT_SCORING = os.path.join("config", "scoring_scheme.v1.json")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_RUN_TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_task.py")


class Command(object):
    """子命令基类：解析产物 (opts, pos) 进构造，执行入口 execute_command()。

    spec：声明式参数规格（cli.parse_argv 用）；空 dict = 无集中校验。
    """

    name = ""
    spec = {}

    def __init__(self, opts, pos):
        self.opts = opts
        self.pos = pos

    def execute_command(self):
        raise NotImplementedError("子类必须实现 execute_command")


def _scheme_files():
    d = os.path.join(_REPO_ROOT, "config")
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            yield os.path.join(d, name)


class ValidateCommand(Command):
    name = "validate"

    def execute_command(self):
        opts = self.opts
        rules = resolve_path(opts.get("rules"), DEFAULT_RULES)
        scoring = resolve_path(opts.get("scoring"), DEFAULT_SCORING)
        try:
            schemes = load_schemes(rules, scoring)
        except ConfigError as exc:
            return rt.emit_error("CONFIG_INVALID", "配置校验失败", details=list(exc.errors))
        except (IOError, OSError) as exc:
            return rt.emit_error("CONFIG_INVALID", "配置读取失败：%s" % exc)
        return rt.emit_ok(errors=[], warnings=[],
                          versions={"rule": schemes.rule_version,
                                    "scoring": schemes.scoring_version})


class SchemesCommand(Command):
    name = "schemes"

    def execute_command(self):
        out = []
        for path in _scheme_files():
            cfg = rt.read_json(path)
            if not isinstance(cfg, dict) or "scheme_id" not in cfg:
                continue
            out.append({"scheme_id": cfg["scheme_id"],
                        "type": cfg.get("config_type", ""),
                        "version": cfg.get("version", ""),
                        "status": cfg.get("status", ""),
                        "path": os.path.relpath(path, _REPO_ROOT),
                        "description": cfg.get("description", "")})
        return rt.emit_ok(schemes=out)


class StartCommand(Command):
    name = "start"
    # exec 只补默认值、不做枚举校验：旧实现把未知值当 cluster 走（行为保持，
    # 校验收紧留到阶段四清理）。
    spec = {
        "scope": {"default": "full", "or_default": True,
                  "values": ("full", "sample"),
                  "values_msg": "--scope 必须是 full 或 sample，得到 %r"},
        "exec": {"default": "cluster"},
        "force": {"flag": True},
        "foreground": {"flag": True},
        "data-version": {},
        "tag": {},
        "task-id": {},
        "rules": {},
        "scoring": {},
    }

    def execute_command(self):
        opts = self.opts
        rules = resolve_path(opts.get("rules"), DEFAULT_RULES)
        scoring = resolve_path(opts.get("scoring"), DEFAULT_SCORING)
        scope = opts["scope"]
        try:
            schemes = load_schemes(rules, scoring)
        except ConfigError as exc:
            return rt.emit_error("CONFIG_INVALID", "配置校验失败", details=list(exc.errors))
        if opts.get("data-version") and opts["data-version"] != schemes.rules["data_version"]["id"]:
            return rt.emit_error("VERSION_CONFLICT",
                                 "请求的 data_version=%s 与配置声明的 %s 不一致"
                                 % (opts["data-version"], schemes.rules["data_version"]["id"]))

        busy = rt.running_task()
        if busy and not opts.get("force"):
            return rt.emit_error("TASK_ALREADY_RUNNING",
                                 "已有运行中的任务：%s（如需并行请加 --force）" % busy,
                                 task_id=busy)

        tid = opts.get("task-id") or rt.new_task_id()
        d = rt.task_dir(tid)
        if os.path.isdir(d) and not opts.get("force"):
            return rt.emit_error("TASK_ALREADY_RUNNING", "task_id 已存在：%s" % tid, task_id=tid)
        if not os.path.isdir(d):
            os.makedirs(d)

        started = rt.now_utc()
        rt.write_status(tid, status="queued", stage="queued", started_at=started,
                        message="queued", rules=rules, scoring=scoring,
                        data_version=schemes.rules["data_version"]["id"],
                        tag=opts.get("tag", ""), exec_mode=opts["exec"],
                        scope=scope, errors=[])

        if opts.get("foreground"):
            rt._execute(tid, rules, scoring, opts["exec"], scope=scope)
            st = rt.read_status(tid)
            if st["status"] != "succeeded":
                err = (st.get("errors") or [{}])[0]
                return rt.emit_error("TASK_FAILED", err.get("message", "任务失败"),
                                     task_id=tid, stage=err.get("stage", st.get("stage")),
                                     job=err.get("job"), exit_code=err.get("exit_code"))
            return rt.emit_ok(task_id=tid, status="succeeded", task_dir=d,
                              started_at=started)

        logf = io.open(os.path.join(d, "driver.log"), "ab")
        subprocess.Popen([sys.executable, _RUN_TASK, "_run",
                          "--task-id", tid, "--rules", rules, "--scoring", scoring,
                          "--exec", opts["exec"], "--scope", scope],
                         stdout=logf, stderr=logf, cwd=_REPO_ROOT,
                         start_new_session=True)
        return rt.emit_ok(task_id=tid, status="queued", task_dir=d, started_at=started)


class StatusCommand(Command):
    name = "status"
    spec = {"task-id": {"required": True}}

    def execute_command(self):
        opts = self.opts
        st = rt.read_status(opts["task-id"])
        return rt.emit_ok(task_id=opts["task-id"], status=st.get("status"),
                          stage=st.get("stage"),
                          stage_index=st.get("stage_index"),
                          stage_total=st.get("stage_total"),
                          progress_percent=st.get("progress_percent"),
                          message=st.get("message", ""), started_at=st.get("started_at"),
                          updated_at=st.get("updated_at"), errors=st.get("errors", []))


class TasksCommand(Command):
    name = "tasks"

    def execute_command(self):
        d = rt.tasks_dir()
        out = []
        if os.path.isdir(d):
            for tid in sorted(os.listdir(d), reverse=True)[:50]:
                st = rt.read_json(os.path.join(d, tid, "status.json"))
                if st:
                    out.append({"task_id": tid, "status": st.get("status"),
                                "started_at": st.get("started_at"),
                                "data_version": st.get("data_version")})
        return rt.emit_ok(tasks=out)


class ResultCommand(Command):
    name = "result"
    spec = {"task-id": {"required": True}}

    def execute_command(self):
        opts = self.opts
        tid = opts["task-id"]
        st = rt.read_status(tid)
        if st.get("status") == "failed":
            err = (st.get("errors") or [{}])[0]
            return rt.emit_error("TASK_FAILED", err.get("message", "任务失败"), task_id=tid,
                                 stage=err.get("stage"), job=err.get("job"),
                                 exit_code=err.get("exit_code"))
        if st.get("status") != "succeeded":
            return rt.emit_error("TASK_NOT_FINISHED",
                                 "任务尚未完成（当前 %s / %s）" % (st.get("status"), st.get("stage")),
                                 task_id=tid)

        d = rt.task_dir(tid)
        res = rt.read_json(os.path.join(d, "result.json"))
        if res is None:
            return rt.emit_error("TASK_NOT_FINISHED", "任务标记为成功但没有 result.json",
                                 task_id=tid)
        body = {"task_id": tid}
        body.update(res)
        return rt.emit_ok(**body)


class SamplesCommand(Command):
    name = "samples"
    # 参数形状校验在规格表（先于任务存在性判断）——与旧实现一致：
    # 「用法错误是请求本身的问题，不该被任务不存在盖住」。
    spec = {
        "task-id": {"required": True},
        "type": {"default": "cleaned", "values": ("cleaned", "quarantine"),
                 "values_msg": "--type 必须是 cleaned 或 quarantine"},
        "table": {"default": "movies", "values": tuple(sorted(TABLE_FILES)),
                  "values_msg": "--table 必须是 %s" % " / ".join(sorted(TABLE_FILES))},
        "n": {"default": "5", "int": True, "min": 0,
              "int_msg": "--n 必须是整数", "min_msg": "--n 不能为负"},
    }

    def execute_command(self):
        opts = self.opts
        tid = opts["task-id"]
        kind = opts["type"]
        table = opts["table"]
        n = opts["n"]
        rt.read_status(tid)

        d = rt.task_dir(tid)
        version = rt.read_status(tid).get("data_version", "")
        if kind == "cleaned":
            path = os.path.join(d, "cleaned", version, TABLE_FILES[table])
            if not os.path.isfile(path):
                return rt.emit_error("TASK_NOT_FINISHED", "cleaned 产物不存在", task_id=tid)
            with io.open(path, encoding="iso-8859-1") as fh:
                rows = [l.rstrip("\n") for l in fh if l.strip()]
            return rt.emit_ok(task_id=tid, type=kind, table=table,
                              total_available=len(rows),
                              samples=[{"line": i + 1, "raw_line": r}
                                       for i, r in enumerate(rows[:n])])

        path = os.path.join(d, "quarantine", version, TABLE_FILES[table])
        total = rt.read_json(os.path.join(d, "counts.json"), {}) \
            .get("quarantine", {}).get("by_rule", {})
        rows = []
        if os.path.isfile(path):
            with io.open(path, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        return rt.emit_ok(task_id=tid, type=kind, table=table,
                          total_available=sum(total.values()) if total else len(rows),
                          samples=[{"line_no": r["line_no"], "raw_line": r["raw_line"],
                                    "rule_id": r["rule_id"], "stage": r["stage"],
                                    "reason": r["reason"]} for r in rows[:n]])


class ReportCommand(Command):
    name = "report"
    # task-id 必填进规格表；--format 校验**留在命令体**：旧实现先查任务存在
    # （TASK_NOT_FOUND 优先于 USAGE），规格化前置会改变该顺序（行为不变红线）。
    spec = {"task-id": {"required": True}}

    def execute_command(self):
        opts = self.opts
        tid = opts["task-id"]
        rt.read_status(tid)
        fmt = opts.get("format", "json")
        d = rt.task_dir(tid)
        if fmt == "md":
            path = os.path.join(d, "report.md")
            if not os.path.isfile(path):
                return rt.emit_error("TASK_NOT_FINISHED", "报告尚未生成", task_id=tid)
            with io.open(path, encoding="utf-8") as fh:
                return rt.emit_ok(task_id=tid, format="md", report=fh.read())
        if fmt != "json":
            raise rt.CliError("USAGE", "--format 必须是 md 或 json")
        rep = rt.read_json(os.path.join(d, "report.json"))
        if rep is None:
            return rt.emit_error("TASK_NOT_FINISHED", "报告尚未生成", task_id=tid)
        return rt.emit_ok(task_id=tid, format="json", report=rep)


#: 命令注册表（含 v1.1 新命令的位置；v1.1 落地时在此追加）
COMMAND_CLASSES = [ValidateCommand, SchemesCommand, StartCommand, StatusCommand,
                   TasksCommand, ResultCommand, SamplesCommand, ReportCommand]


def find_command(name):
    """按命令名找命令类；找不到返回 None。"""
    for command_class in COMMAND_CLASSES:
        if command_class.name == name:
            return command_class
    return None


def command_names():
    """全部命令名（排序），供入口的错误提示用。"""
    return sorted(c.name for c in COMMAND_CLASSES)