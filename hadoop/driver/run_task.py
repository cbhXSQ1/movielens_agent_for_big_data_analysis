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

`start --scope full|sample`：full=全量（**默认**，正式口径，约 8 分钟）；
sample=评分表前 2000 行（仅联调，页面会挂抽样横幅）。D-016。
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

#: 模块双身份统一：本文件可能以「脚本」或「包模块」两种方式加载；commands.py
#: 通过 `from driver import run_task` 引用本模块 —— 若这里不先登记别名，
#: 会生成第二份模块副本，导致 CliError 等类身份分裂、main() 的 except 兜不住异常。
if __name__ == "__main__":
    sys.modules.setdefault("driver.run_task", sys.modules["__main__"])
else:
    sys.modules.setdefault("driver.run_task", sys.modules[__name__])

from engine.config_loader import ConfigError, load_schemes  # noqa: E402
from engine.pipeline import (TABLE_FILES, final_prefix_len,  # noqa: E402
                             strip_final_prefix)
from engine.metrics import count_keys  # noqa: E402

#: 阶段一拆分（纯搬家）：参数形态解析在 cli.py，子命令类在 commands.py；
#: 入口只保留 main / 信封 / 执行链（Runner 阶段三迁 pipeline.py）。
#: 注意：commands.py 顶层 `from driver import run_task as rt`，与 run_task 存在
#: 互相引用 —— 因此 commands 的符号在 main() 内延迟导入（模块级会循环导入报错）。
from driver.cli import CliError, parse_args  # noqa: E402

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

#: CliError 阶段二起定义在 cli.py（解析层与执行层共用），此处经上方 import 再导出，
#: 保证 `driver.run_task.CliError`（测试引用）与 `driver.cli.CliError` 是同一个类。


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


# 执行链（Runner 保留在入口；阶段三迁 pipeline.py）


# ---------------------------------------------------------------------------
# 执行链
# ---------------------------------------------------------------------------

class Runner(object):
    """按 §5.1/§5.2 顺序跑完整链；两种执行后端共用同一份顺序定义。"""

    def __init__(self, tid, rules, scoring, mode, scope="full"):
        self.tid = tid
        self.rules = rules
        self.scoring = scoring
        self.mode = mode
        self.scope = scope   # full=全量（正式口径，默认） / sample=评分表 2000 行（联调）
        self.schemes = load_schemes(rules, scoring)
        self.task_dir = task_dir(tid)
        self.hdfs = "%s/tasks/%s" % (hdfs_base(), tid)
        self.counters = {}
        self.raw_hdfs = "%s/raw/%s" % (hdfs_base(), self.schemes.rules["data_version"]["id"])
        # 日志目录必须先建：local 后端不提交作业，但收尾（fetch/报告等）要往
        # 这里写日志，否则会在收尾阶段因为目录不存在而失败（实测踩到）。
        # （D-015 后 local 不再发布到 HDFS，此目录只供本地收尾使用。）
        logs = os.path.join(self.task_dir, "logs")
        if not os.path.isdir(logs):
            os.makedirs(logs)
        self.report_result = None
        self.published = None           # PublishStage 写入；finish() 原样落 status
        # 阶段三：TaskContext 是阶段类的唯一依赖面（driver/pipeline.py）
        from driver.pipeline import TaskContext
        self.ctx = TaskContext(self)

    # -- 基础设施 -----------------------------------------------------------

    def stage(self, name):
        log("stage %s" % name)
        write_status(self.tid, stage=name, status="running", message="running %s" % name)

    def job(self, script, inputs, output, mapper_args="", reducer_args=None,
            reduces=0, extra_files="", key_fields=None, name=None, extra_d=None):
        """提交一趟 Streaming 作业（cluster 后端）—— 唯一入口在 hdfsio.

        阶段四：提交构造与日志命名迁至 StreamingSubmitter（单输入 submit_stage.sh /
        多输入裸 jar 两种模式合并一处）；计数器合并语义照旧 —— 仅脚本模式
        返回 stderr 参与合并，裸 jar 模式历史行为就是不合并。
        """
        from driver.hdfsio import JobSpec, StreamingSubmitter
        spec = JobSpec(script, inputs, output, reduces, mapper_args, reducer_args,
                       extra_files, key_fields, name, extra_d)
        err = StreamingSubmitter(self.task_dir, self.tid).submit_job(spec)
        if err is not None:
            self._merge(parse_counters(err))
        return output

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
        # 延迟导入：hdfsio 顶层引用本模块，运行期取避免循环导入
        from driver.hdfsio import hdfs_env_exports
        run_shell(["bash", "-c",
                   hdfs_env_exports() + 'rm -f "%s"; hdfs dfs -getmerge "%s" "%s"'
                   % (tmp, hdfs_path, tmp)],
                  os.path.join(self.task_dir, "logs", "fetch_%s.log" % os.path.basename(local_path)))
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

    # 阶段三：集群/本地执行链已迁至 driver/pipeline.py（Pipeline/Stage 类），
    # 模式判断收敛在 select_pipeline(ctx) 一处；Runner 保留服务实现（job/fetch/
    # _score/publish/…），阶段四再迁 hdfsio.py / task.py / report.py。
    def finish(self):
        """汇总 counts / metrics / report；收尾状态（publish 已由 PublishStage 完成）。"""
        self.stage("finalize")
        if self.mode == "cluster":
            counts = self._counts_from_counters()
            before = _unwrap_metrics(read_json(
                os.path.join(self.task_dir, "metrics", "before.json"), {}))
            after = _unwrap_metrics(read_json(
                os.path.join(self.task_dir, "metrics", "after.json"), {}))
            self._write_stats(counts, before, after)
        else:
            counts = self.loc_stats["counts"]
            before = read_json(os.path.join(self.task_dir, "metrics", "before.json"), {})
            after = read_json(os.path.join(self.task_dir, "metrics", "after.json"), {})
            write_json(os.path.join(self.task_dir, "counts.json"), counts)

        self._write_report(counts, before, after)
        self.stage("done")
        write_status(self.tid, status="succeeded", stage="done", message="done",
                     published=self.published, finished_at=now_utc())

    def _assemble_quarantine(self, cleaned_dir):
        """从各表最后的清洗作业输出里分拣隔离记录（D-014 的本地分拣步）。

        `u_res / m_resid / r_cross` 是 K/Q/D 混合流；Q 行是隔离记录 JSON，
        按与本地 runner 相同的键排序（line_no, rule_id）写入
        `quarantine/<data_version>/<table>.dat`；D 行只用于计数（计数器已有），
        这里不再落盘 —— 本地 runner 的隔离区同样不含去重记录。
        """
        version = self.schemes.rules["data_version"]["id"]
        qdir = os.path.join(self.task_dir, "quarantine", version)
        if not os.path.isdir(qdir):
            os.makedirs(qdir)
        sources = {"users": "u_res", "movies": "m_resid", "ratings": "r_cross"}
        # kind 判定直接用字面量（K/Q/D 是 jobs._common 的约定，见 D-014）
        for table, dirname in sources.items():
            tmp = os.path.join(self.task_dir, "logs", "quarantine_%s.tmp" % table)
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
                os.path.join(self.task_dir, "cleaned",
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
        write_json(os.path.join(self.task_dir, "counts.json"), counts)
        stats = {"task_id": self.tid, "counts": counts,
                 "rule_hits": {"quarantine": counts["quarantine"]["by_rule"],
                               "marks": dict((k[1], v) for k, v in self.counters.items()
                                             if k[0] == "marks"),
                               "groups": dict((k[1], v) for k, v in self.counters.items()
                                              if k[0] == "groups")},
                 "scores": {"before": before, "after": after},
                 "rule_notes": {"headline": RULE_NOTES_HEADLINE,
                                "notes": RULE_NOTES}}
        write_json(os.path.join(self.task_dir, "stats.json"), stats)
        return stats

    def _write_report(self, counts, before, after):
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
                "task_dir": self.task_dir,
                "cleaned_dir": os.path.join(self.task_dir, "cleaned",
                                            self.schemes.rules["data_version"]["id"]),
                "quarantine_dir": os.path.join(self.task_dir, "quarantine",
                                               self.schemes.rules["data_version"]["id"]),
                "metrics_dir": os.path.join(self.task_dir, "metrics"),
                "report_md": os.path.join(self.task_dir, "report.md"),
                "report_json": os.path.join(self.task_dir, "report.json"),
                "published_dir": None if self.mode != "cluster" else os.path.join(
                    hdfs_base(), "published",
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
        write_json(os.path.join(self.task_dir, "result.json"), result)

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
        with io.open(os.path.join(self.task_dir, "report.md"), "w", encoding="utf-8",
                     newline="\n") as fh:
            fh.write(u"\n".join(lines) + u"\n")
        write_json(os.path.join(self.task_dir, "report.json"), result)

    def publish(self):
        """发布到 HDFS /data/published/<data_version>/；已存在则比对内容哈希。

        D-017：哈希清单随发布目录保存（.published_hashes.json），发布前从
        HDFS 读回比对（跨任务生效），拦「改规则 → 同版本新任务重跑」。
        只对集群模式生效：发布是「数据版本分发到共享存储」的集群收尾环节，
        local 模式的产物全部在任务目录里（cleaned/metrics/quarantine/report），
        没有消费者需要它进 HDFS；本地任务必须在完全无 Hadoop 的环境里也能
        成功（D-015）。stage 序列保持不变 —— publish 在 local 下是 no-op，
        接口文档的 `… → finalize → publish → done` 两个模式一致。
        """
        if self.mode != "cluster":
            return None
        version = self.schemes.rules["data_version"]["id"]
        target = "%s/published/%s" % (hdfs_base(), version)
        cleaned = os.path.join(self.task_dir, "cleaned", version)
        hashes = {}
        for table in TABLES:
            path = os.path.join(cleaned, TABLE_FILES[table])
            h = hashlib.sha256()
            with io.open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            hashes[table] = h.hexdigest()
        # D-017：**权威比对源是发布目录里的 .published_hashes.json**（跨任务生效）——
        # 只读本任务目录的 published_hashes.json 拦不住「改规则 → 同 data_version
        # 用新 task_id 重跑」：新任务首次发布时本地记录为空，检查形同虚设。
        # 本地文件保留为排障副本，不再参与比对。旧发布（尚无元文件）退化为
        # 现算发布目录内容哈希，一次性迁移后所有发布都带元文件。
        exists = self._hdfs_exists(target)
        prev = self._published_hashes(target) if exists else None
        if prev and prev != hashes:
            raise CliError(
                "VERSION_CONFLICT",
                "发布版本 %s 已存在且内容哈希不一致：配置或输入已变化，必须升 data_version"
                % version)
        write_json(os.path.join(self.task_dir, "published_hashes.json"), hashes)
        run_shell(self._hdfs('hdfs dfs -mkdir -p "%s" && hdfs dfs -put -f "%s"/* "%s/"'
                             % (target, cleaned, target)),
                  os.path.join(self.task_dir, "logs", "publish.log"))
        self._put_published_hashes(target, hashes)
        return {"dir": target, "reused": bool(exists), "content_hashes": hashes}

    @staticmethod
    def _hdfs(cmd):
        """把一条 hdfs 命令包装成本机伪分布式可用的 bash（env 注入）。"""
        from driver.hdfsio import hdfs_env_exports    # 延迟导入：见 fetch 注释
        return ["bash", "-c", hdfs_env_exports() + cmd]

    def _published_hashes(self, target):
        """从发布目录读回权威哈希清单；无元文件（旧发布）则现算三表内容。

        三表是 ISO-8859-1 二进制，不能过 Python 的 utf-8 decode —— 哈希在
        shell 管道里算（`hdfs dfs -cat | sha256sum`），Python 只收 hex 文本。
        """
        rc, out, _e = run_shell(self._hdfs('hdfs dfs -cat "%s/.published_hashes.json"' % target),
                                None, check=False)
        if rc == 0 and out.strip():
            try:
                obj = json.loads(out)
                if isinstance(obj, dict) and set(obj) == set(TABLES):
                    return obj
            except ValueError:
                pass
        got = {}
        for table in TABLES:
            rc, out, _e = run_shell(self._hdfs(
                'hdfs dfs -cat "%s/%s" | sha256sum' % (target, TABLE_FILES[table])),
                None, check=False)
            if rc != 0:
                return None                     # 目录在但内容读不到 → 无从比对
            got[table] = out.split()[0]
        return got

    def _put_published_hashes(self, target, hashes):
        """把这次发布的哈希清单写进发布目录（下次任何任务发布前都比对它）。"""
        tmp = os.path.join(self.task_dir, "logs", "published_hashes.tmp")
        write_json(tmp, hashes)
        run_shell(self._hdfs('hdfs dfs -put -f "%s" "%s/.published_hashes.json"'
                             % (tmp, target)),
                  os.path.join(self.task_dir, "logs", "publish.log"))

    @staticmethod
    def _hdfs_exists(path):
        rc, _o, _e = run_shell(Runner._hdfs('hdfs dfs -test -e "%s"' % path), None,
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


def _execute(tid, rules, scoring, mode, scope="full"):
    from driver.pipeline import select_pipeline      # 阶段三：模式判断收敛于此
    runner = Runner(tid, rules, scoring, mode, scope=scope)
    try:
        select_pipeline(runner.ctx).execute_all_stages()
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



def main(argv):
    from driver.commands import command_names, find_command  # 延迟导入：见文件头注释
    from driver.cli import parse_argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(__doc__)
        return EXIT_OK
    name = argv[0]
    if name == "_run":                      # 后台子进程入口（不出 JSON 信封）
        opts, _ = parse_args(argv[1:])
        try:
            _execute(opts["task-id"], opts["rules"], opts["scoring"],
                     opts.get("exec", "cluster"), scope=opts.get("scope", "full"))
        except Exception:
            return EXIT_FAILED
        return EXIT_OK
    command_class = find_command(name)
    if command_class is None:
        return emit_error("USAGE", "未知子命令 %r；可用：%s"
                          % (name, " / ".join(command_names())))
    try:
        parsed = parse_argv(argv[1:], name, command_class.spec)
        return command_class(parsed.options, parsed.operands).execute_command()
    except CliError as exc:
        extra = dict(exc.extra)
        return emit_error(exc.code, exc.message, **extra)
    except ConfigError as exc:
        return emit_error("CONFIG_INVALID", "配置校验失败", details=list(exc.errors))
    except (IOError, OSError) as exc:
        return emit_error("TASK_FAILED", "文件系统错误：%s" % exc)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
