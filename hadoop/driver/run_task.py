#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hadoop/driver/run_task.py —— 迭代一 Hadoop 侧 driver / CLI（契约见 docs/hadoop/agent-interface.md）。

八个子命令：validate / schemes / start / status / result / samples / report / tasks。

硬性契约（`interface_version = "1.0"`）：
  * **stdout 只输出一个 JSON 信封**（`{"ok": true, ...}` 或
    `{"ok": false, "error": {...}}`），日志一律走 stderr
  * 退出码：0 成功 / 2 参数或配置非法 / 3 任务失败 / 4 任务不存在 /
    5 任务未完成 / 6 版本或发布冲突
  * 同一时刻只允许 1 个运行中任务（`start` 冲突时报 `TASK_ALREADY_RUNNING`）
  * **不编造**：任务未成功时 `result` 拒绝返回（退出码 3/5），只给状态与原因
  * 幂等：同输入 + 同配置重跑，cleaned 三表内容哈希一致；发布版本不可覆盖

执行方式：driver 本身不做数据加工，它只是把 `hadoop/scripts/upload_raw.sh` 与
`hadoop/scripts/submit_stage.sh` 按 §5.1/§5.2 的顺序串起来，收集
Streaming 的 `reporter:counter:` 计数器与各作业的产物，再调用
`engine.metrics.metrics_from_counts` 之外的**作业产出**组装
`metrics/*.json`（比率只在 `score_finalize` 里算，见 plan §5.2）。

`--exec local` 用本地 runner（`engine.pipeline.run_local`）跑同一条链，
用于演示与契约测试；`--exec cluster` 走真实 Streaming。
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO_ROOT, "hadoop"))

from engine.config_loader import ConfigError, load_schemes  # noqa: E402
from engine.pipeline import (TABLE_FILES, final_prefix_len,  # noqa: E402
                             strip_final_prefix)
from engine.metrics import count_keys  # noqa: E402

INTERFACE_VERSION = "1.0"
DEFAULT_RULES = os.path.join("config", "cleaning_rules.v1.json")
DEFAULT_SCORING = os.path.join("config", "scoring_scheme.v1.json")

#: 契约 §4.4 的阶段序列（10 个）
STAGES = ["queued", "clean_users", "clean_movies", "clean_ratings", "stats_marks",
          "score_before", "score_after", "finalize", "publish", "done"]

EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_NOT_FOUND, EXIT_NOT_FINISHED, EXIT_CONFLICT = \
    0, 2, 3, 4, 5, 6

ERROR_CODES = {
    "CONFIG_INVALID": EXIT_USAGE,
    "USAGE": EXIT_USAGE,
    "TASK_ALREADY_RUNNING": EXIT_USAGE,
    "TASK_FAILED": EXIT_FAILED,
    "TASK_NOT_FOUND": EXIT_NOT_FOUND,
    "TASK_NOT_FINISHED": EXIT_NOT_FINISHED,
    "VERSION_CONFLICT": EXIT_CONFLICT,
}

TABLES = ("users", "movies", "ratings")

#: 报告里的「规则口径说明」。原因见 decisions.md D-011：
#: 配置里 `evidence`（人写的测量备注，没有任何代码读它）与 `detect`
#: （机器读的、真正执行的判据）有 4 处对不上。
#: **处置行为一律以 detect 为准**；这里把两套数字并排写进报告，
#: 免得评审看到「M8 = 1 组」「R9 = 17 人」以为算错了。
#: 说明是**静态文本**：它描述的是「配置自相矛盾」这件事，不随单次运行变化；
#: 引用的都是全量 ml-1m 的实测值。
RULE_NOTES = [
    {
        "rule_id": "U2", "name": "用户属性编码校验", "action": "fix",
        "detect_semantics": "对每条用户记录，任一属性（Gender/Age/Occupation）"
                            "不在登记取值集合内即命中",
        "detect_observed": "命中 369 条记录 / 369 处字段"
                           "（Gender、Age、Occupation 各 123 条，三个集合互不相交）",
        "evidence_says": "records: 123、field_hits: 369",
        "why_different": "123 是**每个字段各自的**命中条数，不是记录数；"
                         "三个字段的非法记录集互不重叠，故记录数也是 123×3 = 369。"
                         "细节称「空值×23（三字段同记录）」，实际是每字段各 23 条、"
                         "共 69 条记录本就是空值，故真正被清空的是 369 − 69 = 300 处",
        "contract_impact": "U2 属 fix，不在 counts.fix 的 4 个契约键内；"
                           "受影响字段数（A3/C1）由真实数据变换决定，与基线一致",
    },
    {
        "rule_id": "U3", "name": "邮编格式修复", "action": "fix",
        "detect_semantics": r"邮编不匹配 ^\d{5}$ 即命中",
        "detect_observed": "命中 201 条 = ZIP+4 可截取 73 + 其余非法 106（清空）"
                           " + 邮编本就为空 22",
        "evidence_says": "measured_hits: 202（73 截取 + 129 置空）",
        "why_different": "总数差 1；且把「本来就为空、无内容可清」的 22 条也计入了「置空」"
                         "（实际清空 106 条）",
        "contract_impact": "counts.fix 的 U3_zip_plus4 = 73 与 §7.3 基线**完全一致**",
    },
    {
        "rule_id": "M8", "name": "同名电影标记", "action": "mark",
        "detect_semantics": "value=Title, key=MovieID, min_keys=2 → "
                            "「同一标题对应 >=2 个**不同 MovieID**」",
        "detect_observed": "清洗后 1 组（'Léon / Amélie (1994)'，25 个不同 MovieID）",
        "evidence_says": "groups: 218",
        "why_different": "218 只在「**原始**数据 + 同一标题出现 >=2 **行**」时成立"
                         "（原始数据按 detect 语义是 61 组）。"
                         "两者口径不同（行数 vs 不同 ID），量的时点也不同"
                         "（evidence 的细节写明「已由 M4/M5 处理」，即在清洗前量的）",
        "contract_impact": "M8 属 mark_only，不改数据、不在任何 counts 字典内",
    },
    {
        "rule_id": "R9", "name": "用户评分行为异常标记", "action": "mark",
        "detect_semantics": "按 UserID 聚合非法评分条数，min_matches = 1000",
        "detect_observed": "命中 17 人",
        "evidence_says": "matched_users: 30",
        "why_different": "「30 人」对应的是「只要有非法评分就算」这一更宽的口径："
                         "恰好 UserID 1..30 有非法评分共 43,885 条，"
                         "但每人 219–4,353 条不等，仅 17 人达到 1000 条",
        "contract_impact": "R9 属 mark_only，不改数据、不在任何 counts 字典内",
    },
]

#: 报告里的一句话总纲
RULE_NOTES_HEADLINE = (
    "配置的 `evidence` 字段是人写的测量备注，没有任何代码读它；"
    "引擎只执行 `detect`。下列 4 条的 `evidence` 与它**自己那条** `detect` 对不上，"
    "本报告一律以 `detect` 为准，并把两套数字并列，便于核对。"
    "四处**都不影响** §7.3 的 counts 与 36 个指标值（已逐条验证）。"
)


# ---------------------------------------------------------------------------
# 信封与错误
# ---------------------------------------------------------------------------

class CliError(Exception):
    def __init__(self, code, message, **extra):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.extra = extra


def emit(obj, code=EXIT_OK):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return code


def emit_ok(**payload):
    body = {"ok": True, "interface_version": INTERFACE_VERSION}
    body.update(payload)
    return emit(body, EXIT_OK)


def emit_error(code, message, **extra):
    err = {"code": code, "message": message}
    err.update(extra)
    return emit({"ok": False, "interface_version": INTERFACE_VERSION, "error": err},
                ERROR_CODES.get(code, EXIT_USAGE))


def log(msg):
    sys.stderr.write("%s\n" % msg)
    sys.stderr.flush()


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 环境
# ---------------------------------------------------------------------------

def var_dir():
    return os.environ.get("ML_VAR_DIR") or os.path.join(REPO_ROOT, "var")


def hdfs_base():
    return os.environ.get("HDFS_BASE") or "/data"


def tasks_dir():
    return os.path.join(var_dir(), "tasks")


def task_dir(task_id):
    return os.path.join(tasks_dir(), task_id)


def load_env_shell():
    """把 hadoop/scripts/env.sh 的关键变量读进来（STREAMING_JAR / PYTHON_BIN / ML_RAW_DIR）。"""
    script = os.path.join(REPO_ROOT, "hadoop", "scripts", "env.sh")
    out = {}
    try:
        text = subprocess.check_output(
            ["bash", "-c", 'source "%s" >/dev/null 2>&1; '
                           'echo "$STREAMING_JAR"; echo "$PYTHON_BIN"; echo "$ML_RAW_DIR"; '
                           'echo "$HADOOP_HOME"' % script],
            stderr=subprocess.DEVNULL).decode("utf-8", "replace")
        keys = ["STREAMING_JAR", "PYTHON_BIN", "ML_RAW_DIR", "HADOOP_HOME"]
        for k, v in zip(keys, text.split("\n")):
            out[k] = v.strip()
    except Exception:                                   # pragma: no cover
        out = {}
    return out


def raw_dir():
    if os.environ.get("ML_RAW_DIR"):
        return os.environ["ML_RAW_DIR"]
    return load_env_shell().get("ML_RAW_DIR") or ""


def run_shell(args, log_path=None, check=True):
    """跑一条 shell 命令，返回 (rc, stdout, stderr)；日志同时落盘。"""
    proc = subprocess.Popen(args, cwd=REPO_ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = proc.communicate()
    if log_path:
        with io.open(log_path, "wb") as fh:
            fh.write(b"$ " + " ".join(args).encode("utf-8") + b"\n")
            fh.write(out)
            fh.write(err)
    if check and proc.returncode != 0:
        raise CliError("TASK_FAILED", "命令失败（rc=%d）：%s" % (proc.returncode,
                                                             " ".join(args)),
                       stderr=err.decode("utf-8", "replace")[-2000:])
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


#: 直接跑作业脚本时看到的原始协议行（本地 stdin→stdout 测试用）
_COUNTER_RE = re.compile(r"^reporter:counter:([^,]+),([^,]+),(\d+)$", re.M)
#: 经 Hadoop 提交后，Streaming 会把 `reporter:counter:` 收编成 Hadoop 计数器，
#: 日志里只剩人类可读的表格：
#:     \tquarantine
#:     \t\tP3=63
_COUNTER_GROUP_RE = re.compile(r"^\t([^\t=]+?)\s*$")
_COUNTER_ROW_RE = re.compile(r"^\t\t([^=\t]+?)=(\d+)\s*$")


def parse_counters(text):
    """从作业日志里抽取计数器。

    **两种形态都要认**：
      * 直接执行作业脚本 → stderr 上的 `reporter:counter:...` 原始行；
      * 经 Hadoop 提交 → Streaming 把那些行收编成 Hadoop 计数器，
        日志里只剩 `\t<组>` / `\t\t<名>=<值>` 的表格，原始行不再出现。
    只认前一种的话，集群侧的 counts 会**静默全空** —— 全量运行实测踩到过
    （cleaned 三表完全正确，但 counts.quarantine.total=0，很难一眼看出是解析问题）。
    Hadoop 自带的组（Job Counters / Map-Reduce Framework …）也会被收进来，
    无害：driver 只按自己的组名取值，而组名故意取得与内置组不冲突。
    """
    out = {}
    for m in _COUNTER_RE.finditer(text):
        key = (m.group(1), m.group(2))
        out[key] = out.get(key, 0) + int(m.group(3))
    if out:
        return out
    group = None
    for line in text.split("\n"):
        g = _COUNTER_GROUP_RE.match(line)
        if g:
            group = g.group(1)
            continue
        row = _COUNTER_ROW_RE.match(line)
        if row and group:
            key = (group, row.group(1))
            out[key] = out.get(key, 0) + int(row.group(2))
    return out


# ---------------------------------------------------------------------------
# 任务状态
# ---------------------------------------------------------------------------

def write_status(tid, **fields):
    d = task_dir(tid)
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, "status.json")
    cur = {}
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as fh:
            cur = json.load(fh)
    cur.update(fields)
    cur["task_id"] = tid
    cur["updated_at"] = now_utc()
    stage = cur.get("stage") or "queued"
    cur["stage_index"] = STAGES.index(stage) if stage in STAGES else 0
    cur["stage_total"] = len(STAGES) - 1
    cur["progress_percent"] = int(round(100.0 * cur["stage_index"] / (len(STAGES) - 1)))
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(cur, ensure_ascii=False, indent=2))
        fh.write(u"\n")
    return cur


def read_status(tid):
    path = os.path.join(task_dir(tid), "status.json")
    if not os.path.isfile(path):
        raise CliError("TASK_NOT_FOUND", "任务不存在：%s" % tid)
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))
        fh.write(u"\n")


def running_task():
    """当前是否有运行中的任务（契约：同一时刻只允许 1 个）。"""
    d = tasks_dir()
    if not os.path.isdir(d):
        return None
    for tid in sorted(os.listdir(d), reverse=True):
        st = read_json(os.path.join(d, tid, "status.json"))
        if st and st.get("status") in ("queued", "running"):
            return tid
    return None


def new_task_id():
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return "%s-%s" % (stamp, os.urandom(3).hex())


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def parse_args(argv):
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


def resolve_path(p, default):
    p = p or default
    return p if os.path.isabs(p) else os.path.join(REPO_ROOT, p)


# ---------------------------------------------------------------------------
# 子命令：validate / schemes
# ---------------------------------------------------------------------------

def cmd_validate(opts, _pos):
    rules = resolve_path(opts.get("rules"), DEFAULT_RULES)
    scoring = resolve_path(opts.get("scoring"), DEFAULT_SCORING)
    try:
        schemes = load_schemes(rules, scoring)
    except ConfigError as exc:
        return emit_error("CONFIG_INVALID", "配置校验失败", details=list(exc.errors))
    except (IOError, OSError) as exc:
        return emit_error("CONFIG_INVALID", "配置读取失败：%s" % exc)
    return emit_ok(errors=[], warnings=[],
                   versions={"rule": schemes.rule_version,
                             "scoring": schemes.scoring_version})


def _scheme_files():
    d = os.path.join(REPO_ROOT, "config")
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            yield os.path.join(d, name)


def cmd_schemes(_opts, _pos):
    out = []
    for path in _scheme_files():
        cfg = read_json(path)
        if not isinstance(cfg, dict) or "scheme_id" not in cfg:
            continue
        out.append({"scheme_id": cfg["scheme_id"],
                    "type": cfg.get("config_type", ""),
                    "version": cfg.get("version", ""),
                    "status": cfg.get("status", ""),
                    "path": os.path.relpath(path, REPO_ROOT),
                    "description": cfg.get("description", "")})
    return emit_ok(schemes=out)


# ---------------------------------------------------------------------------
# 子命令：start / status / tasks / result
# ---------------------------------------------------------------------------

def cmd_start(opts, _pos):
    rules = resolve_path(opts.get("rules"), DEFAULT_RULES)
    scoring = resolve_path(opts.get("scoring"), DEFAULT_SCORING)
    try:
        schemes = load_schemes(rules, scoring)
    except ConfigError as exc:
        return emit_error("CONFIG_INVALID", "配置校验失败", details=list(exc.errors))
    if opts.get("data-version") and opts["data-version"] != schemes.rules["data_version"]["id"]:
        return emit_error("VERSION_CONFLICT",
                          "请求的 data_version=%s 与配置声明的 %s 不一致"
                          % (opts["data-version"], schemes.rules["data_version"]["id"]))

    busy = running_task()
    if busy and not opts.get("force"):
        return emit_error("TASK_ALREADY_RUNNING",
                          "已有运行中的任务：%s（如需并行请加 --force）" % busy,
                          task_id=busy)

    tid = opts.get("task-id") or new_task_id()
    d = task_dir(tid)
    if os.path.isdir(d) and not opts.get("force"):
        return emit_error("TASK_ALREADY_RUNNING", "task_id 已存在：%s" % tid, task_id=tid)
    if not os.path.isdir(d):
        os.makedirs(d)

    started = now_utc()
    write_status(tid, status="queued", stage="queued", started_at=started,
                 message="queued", rules=rules, scoring=scoring,
                 data_version=schemes.rules["data_version"]["id"],
                 tag=opts.get("tag", ""), exec_mode=opts.get("exec", "cluster"),
                 errors=[])

    if opts.get("foreground"):
        _execute(tid, rules, scoring, opts.get("exec", "cluster"))
        st = read_status(tid)
        if st["status"] != "succeeded":
            err = (st.get("errors") or [{}])[0]
            return emit_error("TASK_FAILED", err.get("message", "任务失败"),
                              task_id=tid, stage=err.get("stage", st.get("stage")),
                              job=err.get("job"), exit_code=err.get("exit_code"))
        return emit_ok(task_id=tid, status="succeeded", task_dir=d,
                       started_at=started)

    logf = io.open(os.path.join(d, "driver.log"), "ab")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "_run",
                      "--task-id", tid, "--rules", rules, "--scoring", scoring,
                      "--exec", opts.get("exec", "cluster")],
                     stdout=logf, stderr=logf, cwd=REPO_ROOT,
                     start_new_session=True)
    return emit_ok(task_id=tid, status="queued", task_dir=d, started_at=started)


def cmd_status(opts, _pos):
    tid = opts.get("task-id")
    if not tid:
        raise CliError("USAGE", "status 需要 --task-id")
    st = read_status(tid)
    return emit_ok(task_id=tid, status=st.get("status"), stage=st.get("stage"),
                   stage_index=st.get("stage_index"), stage_total=st.get("stage_total"),
                   progress_percent=st.get("progress_percent"),
                   message=st.get("message", ""), started_at=st.get("started_at"),
                   updated_at=st.get("updated_at"), errors=st.get("errors", []))


def cmd_tasks(_opts, _pos):
    d = tasks_dir()
    out = []
    if os.path.isdir(d):
        for tid in sorted(os.listdir(d), reverse=True)[:50]:
            st = read_json(os.path.join(d, tid, "status.json"))
            if st:
                out.append({"task_id": tid, "status": st.get("status"),
                            "started_at": st.get("started_at"),
                            "data_version": st.get("data_version")})
    return emit_ok(tasks=out)


def cmd_result(opts, _pos):
    tid = opts.get("task-id")
    if not tid:
        raise CliError("USAGE", "result 需要 --task-id")
    st = read_status(tid)
    if st.get("status") == "failed":
        err = (st.get("errors") or [{}])[0]
        return emit_error("TASK_FAILED", err.get("message", "任务失败"), task_id=tid,
                          stage=err.get("stage"), job=err.get("job"),
                          exit_code=err.get("exit_code"))
    if st.get("status") != "succeeded":
        return emit_error("TASK_NOT_FINISHED",
                          "任务尚未完成（当前 %s / %s）" % (st.get("status"), st.get("stage")),
                          task_id=tid)

    d = task_dir(tid)
    res = read_json(os.path.join(d, "result.json"))
    if res is None:
        return emit_error("TASK_NOT_FINISHED", "任务标记为成功但没有 result.json",
                          task_id=tid)
    body = {"task_id": tid}
    body.update(res)
    return emit_ok(**body)


def cmd_samples(opts, _pos):
    tid = opts.get("task-id")
    if not tid:
        raise CliError("USAGE", "samples 需要 --task-id")
    # 先校验参数形状，再判任务是否存在：用法错误是请求本身的问题，
    # 不该被「任务不存在」盖住（否则 Agent 拿到的错误码会误导排查方向）。
    kind = opts.get("type", "cleaned")
    table = opts.get("table", "movies")
    if kind not in ("cleaned", "quarantine"):
        raise CliError("USAGE", "--type 必须是 cleaned 或 quarantine")
    if table not in TABLE_FILES:
        raise CliError("USAGE", "--table 必须是 %s" % " / ".join(sorted(TABLE_FILES)))
    try:
        n = int(opts.get("n", 5))
    except ValueError:
        raise CliError("USAGE", "--n 必须是整数")
    if n < 0:
        raise CliError("USAGE", "--n 不能为负")
    read_status(tid)

    d = task_dir(tid)
    version = read_status(tid).get("data_version", "")
    if kind == "cleaned":
        path = os.path.join(d, "cleaned", version, TABLE_FILES[table])
        if not os.path.isfile(path):
            return emit_error("TASK_NOT_FINISHED", "cleaned 产物不存在", task_id=tid)
        with io.open(path, encoding="iso-8859-1") as fh:
            rows = [l.rstrip("\n") for l in fh if l.strip()]
        return emit_ok(task_id=tid, type=kind, table=table,
                       total_available=len(rows),
                       samples=[{"line": i + 1, "raw_line": r}
                                for i, r in enumerate(rows[:n])])

    path = os.path.join(d, "quarantine", version, TABLE_FILES[table])
    total = read_json(os.path.join(d, "counts.json"), {}) \
        .get("quarantine", {}).get("by_rule", {})
    rows = []
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    return emit_ok(task_id=tid, type=kind, table=table,
                   total_available=sum(total.values()) if total else len(rows),
                   samples=[{"line_no": r["line_no"], "raw_line": r["raw_line"],
                             "rule_id": r["rule_id"], "stage": r["stage"],
                             "reason": r["reason"]} for r in rows[:n]])


def cmd_report(opts, _pos):
    tid = opts.get("task-id")
    if not tid:
        raise CliError("USAGE", "report 需要 --task-id")
    read_status(tid)
    fmt = opts.get("format", "json")
    d = task_dir(tid)
    if fmt == "md":
        path = os.path.join(d, "report.md")
        if not os.path.isfile(path):
            return emit_error("TASK_NOT_FINISHED", "报告尚未生成", task_id=tid)
        with io.open(path, encoding="utf-8") as fh:
            return emit_ok(task_id=tid, format="md", report=fh.read())
    if fmt != "json":
        raise CliError("USAGE", "--format 必须是 md 或 json")
    rep = read_json(os.path.join(d, "report.json"))
    if rep is None:
        return emit_error("TASK_NOT_FINISHED", "报告尚未生成", task_id=tid)
    return emit_ok(task_id=tid, format="json", report=rep)


# ---------------------------------------------------------------------------
# 执行链
# ---------------------------------------------------------------------------

class Runner(object):
    """按 §5.1/§5.2 顺序跑完整链；两种执行后端共用同一份顺序定义。"""

    def __init__(self, tid, rules, scoring, mode):
        self.tid = tid
        self.rules = rules
        self.scoring = scoring
        self.mode = mode
        self.schemes = load_schemes(rules, scoring)
        self.d = task_dir(tid)
        self.hdfs = "%s/tasks/%s" % (hdfs_base(), tid)
        self.counters = {}
        self.raw_hdfs = "%s/raw/%s" % (hdfs_base(), self.schemes.rules["data_version"]["id"])
        # 日志目录必须先建：local 后端不提交作业，但 publish 也要往这里写日志，
        # 否则会在收尾阶段因为目录不存在而失败（实测踩到）。
        logs = os.path.join(self.d, "logs")
        if not os.path.isdir(logs):
            os.makedirs(logs)
        self.report_result = None

    # -- 基础设施 -----------------------------------------------------------

    def stage(self, name):
        log("stage %s" % name)
        write_status(self.tid, stage=name, status="running", message="running %s" % name)

    def job(self, script, inputs, output, mapper_args="", reducer_args=None,
            reduces=0, extra_files="", key_fields=None, name=None, extra_d=None):
        """提交一趟 Streaming 作业（cluster 后端）。"""
        args = [os.path.join(REPO_ROOT, "hadoop", "scripts", "submit_stage.sh"),
                script]
        args += list(inputs) if isinstance(inputs, (list, tuple)) else [inputs]
        args += [output, "--reduce", str(reduces)]
        if mapper_args:
            args += ["--mapper-args", mapper_args]
        if reducer_args is not None:
            args += ["--reducer-args", reducer_args]
        if extra_files:
            args += ["--files", extra_files]
        if name:
            args += ["--job-name", name]
        if key_fields:
            args += ["-D", "stream.num.map.output.key.fields=%d" % key_fields]
        for kv in (extra_d or []):
            args += kv
        # 日志名必须区分「哪一趟」：keep 趟与 quarantine 趟用的是同一个脚本，
        # 只用脚本名会让后一趟**覆盖**前一趟的日志，而 fix / dedupe 这些计数器
        # 只在 keep 趟上报、隔离命中只在 quarantine 趟上报 —— 覆盖掉就等于丢了
        # 一半的 counts（全量运行实测踩到：隔离数齐全而 fix/dedupe 全空）。
        tag = re.sub(r"[^A-Za-z0-9]+", "", "%s%s%s" % (
            name or "", mapper_args or "", reducer_args or ""))[:40]
        logf = os.path.join(self.d, "logs", "%s.%s.log" % (script.replace(".py", ""), tag))
        if not os.path.isdir(os.path.dirname(logf)):
            os.makedirs(os.path.dirname(logf))
        # submit_stage.sh 只接受一个 -input；多输入用它自带的裸提交
        if isinstance(inputs, (list, tuple)) and len(inputs) > 1:
            self._raw_job(script, inputs, output, mapper_args, reducer_args,
                          reduces, extra_files, key_fields, name, logf, extra_d)
        else:
            _rc, _o, err = run_shell(["bash"] + args, logf)
            self._merge(parse_counters(err))
        return output

    def _raw_job(self, script, inputs, output, mapper_args, reducer_args, reduces,
                 extra_files, key_fields, name, logf, extra_d=None):
        """多输入（stats_marks 的 cleaned 趟）用裸 hadoop jar。"""
        env = load_env_shell()
        jar = env.get("STREAMING_JAR") or os.environ.get("STREAMING_JAR", "")
        py = env.get("PYTHON_BIN") or sys.executable
        files = [os.path.join(REPO_ROOT, "hadoop", "engine.zip"),
                 os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json"),
                 os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json"),
                 os.path.join(REPO_ROOT, "hadoop", "jobs", script),
                 os.path.join(REPO_ROOT, "hadoop", "jobs", "_common.py")]
        if extra_files:
            files += [f for f in extra_files.split(",") if f]
        common = ("--rules cleaning_rules.v1.json --scoring scoring_scheme.v1.json "
                  "--task-id %s" % self.tid)
        cmd = ["hadoop", "jar", jar,
               "-D", "mapreduce.job.name=%s" % (name or script),
               "-D", "mapreduce.job.reduces=%d" % reduces]
        if key_fields:
            cmd += ["-D", "stream.num.map.output.key.fields=%d" % key_fields]
        for kv in (extra_d or []):
            cmd += list(kv)
        if reduces > 0:
            cmd += ["-D", "mapreduce.output.textoutputformat.separator="]
        cmd += ["-files", ",".join(files)]
        for i in inputs:
            cmd += ["-input", i]
        cmd += ["-output", output,
                "-mapper", "%s %s %s %s" % (py, script, mapper_args, common)]
        cmd += ["-reducer", ("%s %s --reduce %s %s" % (py, script, reducer_args or "", common))
                if reduces > 0 else "cat"]
        run_shell(["bash", "-c", 'rm -rf "%s" 2>/dev/null; '
                                'hdfs dfs -rm -r -f "%s" >/dev/null 2>&1 || true; '
                                'export HADOOP_CONF_DIR="%s/hadoop/conf"; '
                                'export JAVA_HOME="%s/.vendor/jdk-11"; '
                                'export HADOOP_HOME="%s/.vendor/hadoop-3.3.6"; '
                                'export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$PATH"; %s'
                                % (logf, output, REPO_ROOT, REPO_ROOT, REPO_ROOT,
                                   " ".join('"%s"' % c for c in cmd))], logf)

    def _merge(self, counters):
        for k, v in counters.items():
            self.counters[k] = self.counters.get(k, 0) + v

    def fetch(self, hdfs_path, local_path, prefix_table=None):
        """把 HDFS 目录取回本地；必要时剥掉 clean_finalize 的零填充键前缀。

        用 `hdfs dfs -getmerge`（把目录下所有 part 文件按序拼成一个本地文件），
        不自己拼 `cat $(hdfs dfs -stat ...)`：那个写法在 part 文件数量变化、
        `_SUCCESS` 存在、或 glob 未命中时会静默产出空文件或 rc=1，
        错误信息只有一句「命令失败」，排查成本很高（实测踩到）。
        """
        tmp = local_path + ".raw"
        if not os.path.isdir(os.path.dirname(local_path)):
            os.makedirs(os.path.dirname(local_path))
        run_shell(["bash", "-c",
                   'export HADOOP_CONF_DIR="%s/hadoop/conf"; export JAVA_HOME="%s/.vendor/jdk-11"; '
                   'export HADOOP_HOME="%s/.vendor/hadoop-3.3.6"; '
                   'export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$PATH"; '
                   'rm -f "%s"; hdfs dfs -getmerge "%s" "%s"'
                   % (REPO_ROOT, REPO_ROOT, REPO_ROOT, tmp, hdfs_path, tmp)],
                  os.path.join(self.d, "logs", "fetch_%s.log" % os.path.basename(local_path)))
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise CliError("TASK_FAILED",
                           "取回 HDFS 目录失败或结果为空：%s（详见 logs/fetch.log）" % hdfs_path)
        if prefix_table:
            n = final_prefix_len(prefix_table)
            with io.open(tmp, encoding="iso-8859-1", newline="") as fh:
                text = fh.read()
            rows = [strip_final_prefix(prefix_table, l) for l in text.split("\n") if l]
            with io.open(local_path, "w", encoding="iso-8859-1", newline="\n") as fh:
                fh.write(u"".join(r + u"\n" for r in rows))
            os.remove(tmp)
        else:
            shutil.move(tmp, local_path)
        return local_path

    # -- 各阶段（cluster） --------------------------------------------------

    def run_cluster(self):
        R = self.hdfs
        sc = os.path.join(REPO_ROOT, "hadoop", "scripts")
        run_shell(["bash", os.path.join(sc, "upload_raw.sh")]
                  + ([] if os.environ.get("ML_FULL_RUN") else ["--sample", "2000"]),
                  os.path.join(self.d, "logs", "upload_raw.log"))

        # D-014：双模式两趟合并为一趟（K/Q 标签流），隔离区由收尾阶段从
        # 各表最后一道清洗作业的输出里按标签分拣出来。
        self.stage("clean_users")
        self.job("users_normalize.py", "%s/users.dat" % self.raw_hdfs, "%s/u_norm" % R)
        self.job("users_resolve.py", "%s/u_norm" % R, "%s/u_res" % R, reduces=1)
        self.job("clean_finalize.py", "%s/u_res" % R, "%s/u_final" % R, reduces=1,
                 mapper_args="--table users", reducer_args="--table users")

        self.stage("clean_movies")
        self.job("movies_normalize.py", "%s/movies.dat" % self.raw_hdfs, "%s/m_norm" % R)
        self.job("movies_resolve.py", "%s/m_norm" % R, "%s/m_res" % R, reduces=1)
        self.job("movies_residual.py", "%s/m_res" % R, "%s/m_resid" % R)
        self.job("clean_finalize.py", "%s/m_resid" % R, "%s/m_final" % R, reduces=1,
                 mapper_args="--table movies", reducer_args="--table movies")

        self.stage("clean_ratings")
        self.job("ratings_validate.py", "%s/ratings.dat" % self.raw_hdfs, "%s/r_val" % R)
        self.job("ratings_dedupe.py", "%s/r_val" % R, "%s/r_ded" % R, reduces=1)
        dims = self._dim_files()
        self.job("ratings_cross.py", "%s/r_ded" % R, "%s/r_cross" % R,
                 mapper_args="--users users_dim.jsonl --movies movies_dim.jsonl",
                 extra_files="%s,%s" % (dims["users"], dims["movies"]))
        self.job("clean_finalize.py", "%s/r_cross" % R, "%s/r_final" % R, reduces=1,
                 mapper_args="--table ratings", reducer_args="--table ratings")

        self.stage("stats_marks")
        self.job("stats_marks.py", "%s/ratings.dat" % self.raw_hdfs, "%s/stats_raw" % R,
                 reduces=1, mapper_args="--source raw-ratings",
                 reducer_args="--source raw-ratings")
        r9 = os.path.join(self.d, "r9_users.json")
        self.fetch("%s/stats_raw" % R, r9)
        self.job("stats_marks.py", ["%s/r_cross" % R, "%s/u_res" % R, "%s/m_resid" % R],
                 "%s/stats_clean" % R, reduces=1,
                 mapper_args="--source cleaned --r9-users r9_users.json",
                 reducer_args="--source cleaned", extra_files=r9)

        self._score("score_before", "raw", [self.raw_hdfs])
        self._score("score_after", "cleaned", [R])

        self.stage("finalize")
        # 交付格式的三表落回本地
        cleaned = os.path.join(self.d, "cleaned", self.schemes.rules["data_version"]["id"])
        for table, src in (("users", "u_final"), ("movies", "m_final"), ("ratings", "r_final")):
            self.fetch("%s/%s" % (R, src), os.path.join(cleaned, TABLE_FILES[table]),
                       prefix_table=table)
        # 隔离区：从各表**最后一道清洗作业**的输出里按 K/Q/D 标签分拣（D-014）。
        # 每个作业只保留一行流（users→u_res、movies→m_resid、ratings→r_cross），
        # Q 流按 (行号, 规则) 排序后写入 quarantine/<table>.dat —— 与本地 runner
        # 的隔离文件同序；D 流（去重移除）已经由计数器计进 counts.dedupe。
        self._assemble_quarantine(cleaned)

    def _dim_files(self):
        """把清洗后维表的 keep 产物取回本地，供 ratings_cross / 评分作业广播。"""
        d = os.path.join(self.d, "dims")
        if not os.path.isdir(d):
            os.makedirs(d)
        out = {"users": os.path.join(d, "users_dim.jsonl"),
               "movies": os.path.join(d, "movies_dim.jsonl")}
        if not os.path.isfile(out["users"]):
            self.fetch("%s/u_res" % self.hdfs, out["users"])
        if not os.path.isfile(out["movies"]):
            self.fetch("%s/m_resid" % self.hdfs, out["movies"])
        return out

    def _score(self, stage_name, source, inputs):
        """跑一侧的评分（D-014 合并版）：**一个** map+reduce 作业。

        mapper 按 `mapreduce_map_input_file` 分派三张表，发射 M/D/G/T 四种线；
        单 reducer 内存聚合后由 metrics_from_counts 出最终分数。
        比分两侧各 10 趟少了 18 次 AM/JVM 启动。

        A4 需要广播维表，且口径按侧区分：before 用**原始**维表（含假 ID），
        after 用**清洗后**维表 —— 混用会高估跨表引用有效率。
        `-files` 里的裸绝对路径会被 GenericOptionsParser 当成本地路径，
        必须显式 `hdfs:///`（空 authority 走 fs.defaultFS）。
        """
        self.stage(stage_name)
        R = self.hdfs
        if source == "raw":
            dim_args = "--users users.dat --movies movies.dat"
            dim_files = "hdfs://%s/users.dat,hdfs://%s/movies.dat" % (self.raw_hdfs,
                                                                     self.raw_hdfs)
            inputs = ["%s/users.dat" % self.raw_hdfs, "%s/movies.dat" % self.raw_hdfs,
                      "%s/ratings.dat" % self.raw_hdfs]
        else:
            local = self._dim_files()
            dim_args = "--users users_dim.jsonl --movies movies_dim.jsonl"
            dim_files = "%s,%s" % (local["users"], local["movies"])
            inputs = ["%s/u_res" % R, "%s/m_resid" % R, "%s/r_cross" % R]
        out = "%s/sc_%s" % (R, source)
        self.job("score_all.py", inputs, out, reduces=1,
                 mapper_args="--source %s %s" % (source, dim_args),
                 reducer_args="--source %s" % source,
                 extra_files=dim_files,
                 extra_d=[("-D", "mapreduce.reduce.memory.mb=2048")])
        local = os.path.join(self.d, "metrics", "%s.json" % ("before" if source == "raw"
                                                             else "after"))
        if not os.path.isdir(os.path.dirname(local)):
            os.makedirs(os.path.dirname(local))
        self.fetch(out, local)
        return local

    # -- local 后端 ---------------------------------------------------------

    def run_local(self):
        from engine.pipeline import run_local as engine_run_local
        self.stage("clean_users")
        stats = engine_run_local(raw_dir(), self.schemes, self.d, self.tid,
                                 processed_at=now_utc())
        for name in STAGES[1:]:
            self.stage(name)
        self.loc_stats = stats

    # -- 收尾 --------------------------------------------------------------

    def finish(self):
        """汇总 counts / metrics / report / publish。"""
        self.stage("finalize")
        meta = read_json(os.path.join(self.d, "metadata.json"), {}) or {}
        if self.mode == "cluster":
            counts = self._counts_from_counters()
            before = _unwrap_metrics(read_json(
                os.path.join(self.d, "metrics", "before.json"), {}))
            after = _unwrap_metrics(read_json(
                os.path.join(self.d, "metrics", "after.json"), {}))
            self._write_stats(counts, before, after)
        else:
            counts = self.loc_stats["counts"]
            before = read_json(os.path.join(self.d, "metrics", "before.json"), {})
            after = read_json(os.path.join(self.d, "metrics", "after.json"), {})
            write_json(os.path.join(self.d, "counts.json"), counts)

        self._write_report(counts, before, after, meta)
        self.stage("publish")
        published = self.publish(counts)
        self.stage("done")
        write_status(self.tid, status="succeeded", stage="done", message="done",
                     published=published, finished_at=now_utc())

    def _assemble_quarantine(self, cleaned_dir):
        """从各表最后的清洗作业输出里分拣隔离记录（D-014 的本地分拣步）。

        `u_res / m_resid / r_cross` 是 K/Q/D 混合流；Q 行是隔离记录 JSON，
        按与本地 runner 相同的键排序（line_no, rule_id）写入
        `quarantine/<data_version>/<table>.dat`；D 行只用于计数（计数器已有），
        这里不再落盘 —— 本地 runner 的隔离区同样不含去重记录。
        """
        version = self.schemes.rules["data_version"]["id"]
        qdir = os.path.join(self.d, "quarantine", version)
        if not os.path.isdir(qdir):
            os.makedirs(qdir)
        sources = {"users": "u_res", "movies": "m_resid", "ratings": "r_cross"}
        # kind 判定直接用字面量（K/Q/D 是 jobs._common 的约定，见 D-014）
        for table, dirname in sources.items():
            tmp = os.path.join(self.d, "logs", "quarantine_%s.tmp" % table)
            self.fetch("%s/%s" % (self.hdfs, dirname), tmp)
            rows = []
            with io.open(tmp, encoding="iso-8859-1") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if len(line) < 3 or line[1] != "\t":
                        continue
                    kind, payload = line[0], line[2:]
                    if kind == "Q":
                        rows.append(json.loads(payload))
            os.remove(tmp)
            rows.sort(key=lambda r: (r["line_no"], r["rule_id"]))
            path = os.path.join(qdir, TABLE_FILES[table])
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            log("quarantine %s: %d 条" % (table, len(rows)))

    def _counts_from_counters(self):
        c = self.counters
        by_rule = dict((k[1], v) for k, v in c.items() if k[0] == "quarantine")
        dedupe = dict((k[1], v) for k, v in c.items() if k[0] == "dedupe")
        fix = dict((k[1], v) for k, v in c.items() if k[0] == "fix")
        cleaned = {}
        for table in TABLES:
            cleaned[table] = self._count_lines(
                os.path.join(self.d, "cleaned",
                             self.schemes.rules["data_version"]["id"],
                             TABLE_FILES[table]))
        return {
            "input": self.input_counts(),
            "output": cleaned,
            "quarantine": {"total": sum(by_rule.values()), "by_rule": by_rule},
            "dedupe": dedupe, "fix": fix,
        }

    @staticmethod
    def _count_lines(path):
        if not os.path.isfile(path):
            return 0
        n = 0
        with io.open(path, encoding="iso-8859-1") as fh:
            for line in fh:
                if line.strip():
                    n += 1
        return n

    def input_counts(self):
        out = {}
        for table in TABLES:
            path = os.path.join(raw_dir(), TABLE_FILES[table])
            n = 0
            if os.path.isfile(path):
                with io.open(path, encoding="iso-8859-1") as fh:
                    for line in fh:
                        if line.strip():
                            n += 1
            out["%s_lines" % table] = n
        return out

    def _write_stats(self, counts, before, after, ):
        write_json(os.path.join(self.d, "counts.json"), counts)
        stats = {"task_id": self.tid, "counts": counts,
                 "rule_hits": {"quarantine": counts["quarantine"]["by_rule"],
                               "marks": dict((k[1], v) for k, v in self.counters.items()
                                             if k[0] == "marks"),
                               "groups": dict((k[1], v) for k, v in self.counters.items()
                                              if k[0] == "groups")},
                 "scores": {"before": before, "after": after},
                 "rule_notes": {"headline": RULE_NOTES_HEADLINE,
                                "notes": RULE_NOTES}}
        write_json(os.path.join(self.d, "stats.json"), stats)
        return stats

    def _write_report(self, counts, before, after, meta):
        digits = 2
        delta = {}
        for k, v in after.get("dimensions", {}).items():
            delta[k] = round(v - before.get("dimensions", {}).get(k, 0.0), digits)
        delta["composite"] = round(after.get("composite", 0.0) - before.get("composite", 0.0),
                                  digits)
        result = {
            "status": "succeeded",
            "data_version": self.schemes.rules["data_version"]["id"],
            "versions": {"rule": {"version": self.schemes.rule_version,
                                  "sha256": self.schemes.rule_hash},
                         "scoring": {"version": self.schemes.scoring_version,
                                     "sha256": self.schemes.scoring_hash},
                         "policy": {"sha256": self.schemes.policy_version},
                         "operator_library": self.schemes.rules["engine_compat"][
                             "operator_library"]},
            "time_boundaries": {"T1": self.schemes.t1, "T2": self.schemes.t2},
            "counts": counts,
            "scores": {
                "before": dict(before.get("dimensions", {}),
                               composite=before.get("composite", 0.0)),
                "after": dict(after.get("dimensions", {}),
                              composite=after.get("composite", 0.0)),
                "delta": delta,
                "metrics": {"before": before.get("metrics", {}),
                            "after": after.get("metrics", {})},
            },
            "quarantine_summary": [],
            "paths": {
                "task_dir": self.d,
                "cleaned_dir": os.path.join(self.d, "cleaned",
                                            self.schemes.rules["data_version"]["id"]),
                "quarantine_dir": os.path.join(self.d, "quarantine",
                                               self.schemes.rules["data_version"]["id"]),
                "metrics_dir": os.path.join(self.d, "metrics"),
                "report_md": os.path.join(self.d, "report.md"),
                "report_json": os.path.join(self.d, "report.json"),
                "published_dir": os.path.join(hdfs_base(), "published",
                                              self.schemes.rules["data_version"]["id"]),
            },
            "rule_notes": {"headline": RULE_NOTES_HEADLINE, "notes": RULE_NOTES},
            "limitations": [
                "用户属性为自愿填写、未经核验，A3/C1 不封顶是诚实口径",
                "U3/S4 等提升部分来自把无法判定的记录移出分母，而非真正修复，详见报告",
                "时效性以数据集发布语境评估；以现实时间衡量必然过时",
                "隔离数量只统计 action=quarantine 的规则；R6/M4/U5 属去重，单列于 counts.dedupe",
            ],
        }
        write_json(os.path.join(self.d, "result.json"), result)

        lines = ["# 迭代一 数据质量评估报告", "",
                 "- task_id：`%s`" % self.tid,
                 "- data_version：`%s`" % result["data_version"],
                 "- rule_version：`%s`（sha256 `%s`）" % (
                     self.schemes.rule_version, self.schemes.rule_hash[:16]),
                 "- scoring_scheme_version：`%s`" % self.schemes.scoring_version,
                 "- policy_version sha256：`%s`" % self.schemes.policy_version[:16],
                 "- T1 = %s，T2 = %s" % (self.schemes.t1, self.schemes.t2), "",
                 "## 1 数据量变化", "",
                 "| 表 | 输入行数 | 输出记录数 | 去重移除 |", "|---|---|---|---|"]
        for table in TABLES:
            lines.append("| %s | %s | %s | %s |" % (
                table, counts["input"].get("%s_lines" % table, "?"),
                counts["output"].get(table, "?"), counts["dedupe"].get(table, 0)))
        lines += ["", "## 2 规则命中与处置", "",
                  "| 规则 | 隔离数 |", "|---|---|"]
        for rid, n in sorted(counts["quarantine"]["by_rule"].items()):
            lines.append("| %s | %s |" % (rid, n))
        lines += ["", "隔离总数：**%s**" % counts["quarantine"]["total"], "",
                  "修复计数：" + "、".join("%s=%s" % (k, v)
                                       for k, v in sorted(counts["fix"].items())), "",
                  "## 3 五维评分（同一公式两侧）", "",
                  "| 维度 | 清洗前 | 清洗后 | 变化 |", "|---|---|---|---|"]
        for k in list(after.get("dimensions", {})) + ["composite"]:
            lines.append("| %s | %.2f | %.2f | %+.2f |" % (
                k, before.get("dimensions", {}).get(k, before.get("composite", 0.0)),
                after.get("dimensions", {}).get(k, after.get("composite", 0.0)),
                delta.get(k, 0.0)))
        lines += ["", "## 4 评价局限", ""]
        lines += ["- " + x for x in result["limitations"]]
        lines += ["", "## 5 规则口径说明（evidence 与 detect 的差异）", "",
                  RULE_NOTES_HEADLINE, "",
                  "| 规则 | 名称 | 处置 | detect 实测 | 配置 evidence |",
                  "|---|---|---|---|---|"]
        for n in RULE_NOTES:
            lines.append("| %s | %s | %s | %s | %s |" % (
                n["rule_id"], n["name"], n["action"], n["detect_observed"],
                n["evidence_says"]))
        lines.append("")
        for n in RULE_NOTES:
            lines += ["**%s %s**" % (n["rule_id"], n["name"]), "",
                      "- detect 口径：%s" % n["detect_semantics"],
                      "- 差异原因：%s" % n["why_different"],
                      "- 对契约的影响：%s" % n["contract_impact"], ""]
        with io.open(os.path.join(self.d, "report.md"), "w", encoding="utf-8",
                     newline="\n") as fh:
            fh.write(u"\n".join(lines) + u"\n")
        write_json(os.path.join(self.d, "report.json"), result)

    def publish(self, counts):
        """发布到 /data/published/<data_version>/；已存在则比对内容哈希。"""
        version = self.schemes.rules["data_version"]["id"]
        target = "%s/published/%s" % (hdfs_base(), version)
        cleaned = os.path.join(self.d, "cleaned", version)
        hashes = {}
        for table in TABLES:
            path = os.path.join(cleaned, TABLE_FILES[table])
            h = hashlib.sha256()
            with io.open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            hashes[table] = h.hexdigest()
        local_meta = os.path.join(self.d, "published_hashes.json")
        prev = read_json(local_meta)
        exists = self._hdfs_exists(target)
        if exists and prev and prev != hashes:
            raise CliError(
                "VERSION_CONFLICT",
                "发布版本 %s 已存在且内容哈希不一致：配置或输入已变化，必须升 data_version"
                % version)
        write_json(local_meta, hashes)
        run_shell(["bash", "-c",
                   'export HADOOP_CONF_DIR="%s/hadoop/conf"; export JAVA_HOME="%s/.vendor/jdk-11"; '
                   'export HADOOP_HOME="%s/.vendor/hadoop-3.3.6"; '
                   'export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$PATH"; '
                   'hdfs dfs -mkdir -p "%s" && hdfs dfs -put -f "%s"/* "%s/"'
                   % (REPO_ROOT, REPO_ROOT, REPO_ROOT, target, cleaned, target)],
                  os.path.join(self.d, "logs", "publish.log"))
        return {"dir": target, "reused": bool(exists), "content_hashes": hashes}

    @staticmethod
    def _hdfs_exists(path):
        rc, _o, _e = run_shell(["bash", "-c",
                                'export HADOOP_CONF_DIR="%s/hadoop/conf"; '
                                'export JAVA_HOME="%s/.vendor/jdk-11"; '
                                'export HADOOP_HOME="%s/.vendor/hadoop-3.3.6"; '
                                'export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$PATH"; '
                                'hdfs dfs -test -e "%s"'
                                % (REPO_ROOT, REPO_ROOT, REPO_ROOT, path)], None,
                               check=False)
        return rc == 0


def _unwrap_metrics(obj):
    """score_finalize 产出的是 `{"side","counts","result"}` 信封，取内层 result。

    接口文档 §4.5 的 scores.before/after 是**扁平**的
    `{Accurate, Complete, ..., composite}` 加一个并列的 metrics，正是 result 的内容。
    直接把信封当结果用，会让 scores 全变成 0（字段取不到 → 默认值），
    而 tasks 又显示 succeeded —— 这类「安静地错」最难发现。
    """
    if isinstance(obj, dict) and "result" in obj:
        return obj["result"]
    return obj or {}


def _execute(tid, rules, scoring, mode):
    runner = Runner(tid, rules, scoring, mode)
    try:
        if mode == "local":
            runner.run_local()
        else:
            runner.run_cluster()
        runner.finish()
    except CliError as exc:
        write_status(tid, status="failed", message=exc.message,
                     errors=[{"stage": read_json(
                         os.path.join(task_dir(tid), "status.json"), {}).get("stage", ""),
                         "message": exc.message}])
        log("FAILED %s: %s" % (exc.code, exc.message))
        raise
    except Exception as exc:                            # pragma: no cover
        write_status(tid, status="failed", message=str(exc),
                     errors=[{"stage": "", "message": "%s: %s" % (type(exc).__name__, exc)}])
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        raise


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

COMMANDS = {
    "validate": cmd_validate, "schemes": cmd_schemes, "start": cmd_start,
    "status": cmd_status, "result": cmd_result, "samples": cmd_samples,
    "report": cmd_report, "tasks": cmd_tasks,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(__doc__)
        return EXIT_OK
    name = argv[0]
    if name == "_run":                      # 后台子进程入口（不出 JSON 信封）
        opts, _ = parse_args(argv[1:])
        try:
            _execute(opts["task-id"], opts["rules"], opts["scoring"],
                     opts.get("exec", "cluster"))
        except Exception:
            return EXIT_FAILED
        return EXIT_OK
    if name not in COMMANDS:
        return emit_error("USAGE", "未知子命令 %r；可用：%s"
                          % (name, " / ".join(sorted(COMMANDS))))
    opts, pos = parse_args(argv[1:])
    try:
        return COMMANDS[name](opts, pos)
    except CliError as exc:
        extra = dict(exc.extra)
        return emit_error(exc.code, exc.message, **extra)
    except ConfigError as exc:
        return emit_error("CONFIG_INVALID", "配置校验失败", details=list(exc.errors))
    except (IOError, OSError) as exc:
        return emit_error("TASK_FAILED", "文件系统错误：%s" % exc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
