# -*- coding: utf-8 -*-
"""driver/hdfsio.py —— 作业提交唯一入口 + HDFS 环境注入（阶段四落地）。

背景（docs/hadoop/driver-refactor-plan.md §1.4 / §5）：
  旧 `Runner.job`（submit_stage.sh 单输入）与 `_raw_job`（多输入裸 jar）
  是两条分叉的提交代码，参数构造各写一份；hdfs 环境注入前缀散在
  `_raw_job / fetch / _hdfs` 三处。本文件把它们收敛为：
    - `hdfs_env_exports()`  环境注入唯一来源（run_task 的 fetch/_hdfs 亦复用）
    - `JobSpec`             一趟作业的完整参数包（数据类）
    - `StreamingSubmitter`  唯一提交入口（单输入脚本 / 多输入 jar 两种模式）

行为纪律（阶段四：清理代码分叉，不改行为）：
  * 两种模式生成的 hadoop 命令与旧实现**逐字符一致**（单测锁定）——
    唯一例外是自 v1.1 起"分发哪两份方案"按本次任务实际选中的文件来（见下），
    在沿用默认方案的情形下命令仍与旧实现完全相同；
  * 计数器合并语义照旧：submit_stage 模式返回 stderr 文本供调用方 parse_counters，
    裸 jar 模式返回 None（历史行为就是不合并，保留）；
  * 参数漂移（speculative / separator）的收敛**不做** —— 需要集群冒烟验证后
    定（拍板 #5：VM 侧小样本冒烟），此处只统一代码路径。

方案分发（为什么必须按"本次任务选中的"来）：
  作业侧是按**任务工作目录里的文件名**读配置的（jobs/_common.py 的 `load_schemes`）。
  此前两条提交路径都把仓库默认的两份配置写死进 `-files` 与 `--rules/--scoring`，
  于是用户上传自定义清洗方案并据此发起任务时：驱动端的校验、血缘、报告用的都是
  自定义方案，**作业却仍按默认方案清洗** —— 用户看到的结果与他上传的方案对不上，
  而且没有任何报错。现在改为按 `Runner.rules` / `Runner.scoring` 分发：
  完整路径进 `-files`，单层文件名进命令行参数（名字由同一个文件取 basename，
  保证"分发名"与"读取名"永远一致）。
"""

import os
import re
import sys

from driver import run_task as rt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 沿用默认方案时的两份配置文件（与 driver/commands.py 的默认值同义，此处为绝对路径）。
DEFAULT_RULES_PATH = os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json")
DEFAULT_SCORING_PATH = os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json")


def _shell_env(name, default):
    """os.environ → env.sh（源码执行一次 bash）→ 默认值。

    为什么还要看 env.sh：driver 常被 Agent 直接拉起（未经 source env.sh 的
    shell），此时 os.environ 里什么都没有；env.sh 是这些路径的**权威口径**
    （它自己就是「外部可覆盖、否则给默认」），照它读才能与手工跑脚本一致。
    """
    value = os.environ.get(name)
    if value:
        return value
    try:
        shell = rt.load_env_shell().get(name)
    except Exception:                                   # pragma: no cover —— 防御性
        shell = None
    return shell if shell else default


def hdfs_env_exports():
    """伪分布式 hdfs 命令的环境注入（原本散在三处的 .vendor 前缀收敛于此）。

    只组装 export 段，具体命令由调用方拼接。

    可移植性（清单⑫）：三项全部**环境变量优先**（JAVA_HOME / HADOOP_HOME /
    HADOOP_CONF_DIR），取不到才回落到仓库内 `.vendor` 自包含安装 —— 换机器
    （JDK/Hadoop 装在 /opt、conf 指向系统目录）只需在跑之前 export，不必改代码。
    优先级与 `hadoop/scripts/env.sh` 逐字同义，两边永远一致。
    """
    conf = _shell_env("HADOOP_CONF_DIR", os.path.join(REPO_ROOT, "hadoop", "conf"))
    java = _shell_env("JAVA_HOME", os.path.join(REPO_ROOT, ".vendor", "jdk-11"))
    hadoop = _shell_env("HADOOP_HOME", os.path.join(REPO_ROOT, ".vendor",
                                                    "hadoop-3.3.6"))
    return ('export HADOOP_CONF_DIR="%s"; export JAVA_HOME="%s"; '
            'export HADOOP_HOME="%s"; '
            'export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$PATH"; '
            % (conf, java, hadoop))


class JobSpec(object):
    """一趟 Streaming 作业的参数包（名词只留给数据类，见命名约定 §2.3）。"""

    def __init__(self, script, inputs, output, reduces=0, mapper_args="",
                 reducer_args=None, extra_files="", key_fields=None,
                 job_name=None, extra_d=None):
        self.script = script
        # 统一成列表：单输入字符串也归一，模式选择只看长度（>1 走裸 jar）
        self.inputs = list(inputs) if isinstance(inputs, (list, tuple)) else [inputs]
        self.output = output
        self.reduces = reduces
        self.mapper_args = mapper_args
        self.reducer_args = reducer_args
        self.extra_files = extra_files
        self.key_fields = key_fields
        self.job_name = job_name
        self.extra_d = extra_d or []


class StreamingSubmitter(object):
    """唯一作业提交入口。

    使用（与旧 Runner.job 同签名语义）：
        err = StreamingSubmitter(task_dir, tid).submit_job(spec)
        if err is not None: merge(parse_counters(err))
    """

    def __init__(self, task_dir, tid, rules=None, scoring=None):
        """`rules` / `scoring`：本次任务实际使用的两份方案（可为相对路径或绝对路径）。

        为空表示沿用仓库默认方案（此时行为与改动前完全一致）。
        """
        self.task_dir = task_dir
        self.tid = tid
        self.rules = rules or DEFAULT_RULES_PATH
        self.scoring = scoring or DEFAULT_SCORING_PATH

    def _scheme_files(self):
        """本次要分发的两份方案：返回 `(清洗路径, 清洗名, 评分路径, 评分名)`。

        - 路径：相对路径按仓库根解析（与 `driver/cli.resolve_path` 同义），
          因为 `-files` 需要真实存在的本地文件；
        - 名字：一律取 `basename`。作业侧按"任务工作目录里的文件名"读配置，
          所以分发用的名字与命令行里给的名字必须同一个 —— 由同一个文件推导即保证。

        两份文件同名时直接拒绝：它们会被分发到同一个名字下、互相覆盖，
        作业可能拿着清洗规则去当评分方案读，结果错得毫无提示。
        """
        def _one(path, default):
            text = path or default
            if not os.path.isabs(text):
                # normpath：调用方给的相对路径可能带正斜杠（`config/x.json`），
                # 直接 join 在 Windows 上会产出混合分隔符；归一化后各平台一致。
                text = os.path.normpath(os.path.join(REPO_ROOT, text))
            return text, os.path.basename(text)

        rules_path, rules_name = _one(self.rules, DEFAULT_RULES_PATH)
        scoring_path, scoring_name = _one(self.scoring, DEFAULT_SCORING_PATH)
        if rules_name == scoring_name:
            raise rt.CliError(
                "USAGE",
                "本次选中的清洗方案与评分方案文件名相同（%s）：两者会被分发到作业的同一个"
                "文件名下互相覆盖，请先把其中一个改名后再发起" % rules_name)
        return rules_path, rules_name, scoring_path, scoring_name

    # -- 公共 ---------------------------------------------------------------

    def submit_job(self, spec):
        """按输入个数选提交模式；返回（如有）stderr 文本供计数器合并。"""
        logf = self._log_path(spec)
        if len(spec.inputs) > 1:
            self._submit_raw_job(spec, logf)
            return None
        return self._submit_script_job(spec, logf)

    def _log_path(self, spec):
        """日志名必须区分「哪一趟」：keep 趟与 quarantine 趟用的是同一个脚本，
        只用脚本名会让后一趟**覆盖**前一趟的日志，而 fix / dedupe 这些计数器
        只在 keep 趟上报、隔离命中只在 quarantine 趟上报 —— 覆盖掉就等于丢了
        一半的 counts（全量运行实测踩到：隔离数齐全而 fix/dedupe 全空）。
        """
        tag = re.sub(r"[^A-Za-z0-9]+", "", "%s%s%s" % (
            spec.job_name or "", spec.mapper_args or "",
            spec.reducer_args or ""))[:40]
        logf = os.path.join(self.task_dir, "logs",
                            "%s.%s.log" % (spec.script.replace(".py", ""), tag))
        logd = os.path.dirname(logf)
        if not os.path.isdir(logd):
            os.makedirs(logd)
        return logf

    # -- 单输入：submit_stage.sh ---------------------------------------------

    def _submit_script_job(self, spec, logf):
        rules_path, _rules_name, scoring_path, _scoring_name = self._scheme_files()
        args = [os.path.join(REPO_ROOT, "hadoop", "scripts", "submit_stage.sh"),
                spec.script]
        args += spec.inputs
        args += [spec.output, "--reduce", str(spec.reduces)]
        if spec.mapper_args:
            args += ["--mapper-args", spec.mapper_args]
        if spec.reducer_args is not None:
            args += ["--reducer-args", spec.reducer_args]
        if spec.extra_files:
            args += ["--files", spec.extra_files]
        # 分发并读取"本次选中的"两份方案；脚本内部按 basename 生成作业侧名字，
        # 缺省（不传）时仍是仓库默认的两份，行为与改动前一致。
        args += ["--rules-file", rules_path, "--scoring-file", scoring_path]
        if spec.job_name:
            args += ["--job-name", spec.job_name]
        if spec.key_fields:
            args += ["-D", "stream.num.map.output.key.fields=%d" % spec.key_fields]
        for kv in spec.extra_d:
            args += kv
        _rc, _o, err = rt.run_shell(["bash"] + args, logf)
        return err

    # -- 多输入：裸 hadoop jar（stats_marks 的 cleaned 趟用）--------------------

    def _submit_raw_job(self, spec, logf):
        env = rt.load_env_shell()
        jar = env.get("STREAMING_JAR") or os.environ.get("STREAMING_JAR", "")
        py = env.get("PYTHON_BIN") or sys.executable
        rules_path, rules_name, scoring_path, scoring_name = self._scheme_files()
        files = [os.path.join(REPO_ROOT, "hadoop", "engine.zip"),
                 rules_path, scoring_path,
                 os.path.join(REPO_ROOT, "hadoop", "jobs", spec.script),
                 os.path.join(REPO_ROOT, "hadoop", "jobs", "_common.py")]
        if spec.extra_files:
            files += [f for f in spec.extra_files.split(",") if f]
        common = ("--rules %s --scoring %s --task-id %s"
                  % (rules_name, scoring_name, self.tid))
        cmd = ["hadoop", "jar", jar,
               "-D", "mapreduce.job.name=%s" % (spec.job_name or spec.script),
               "-D", "mapreduce.job.reduces=%d" % spec.reduces]
        if spec.key_fields:
            cmd += ["-D", "stream.num.map.output.key.fields=%d" % spec.key_fields]
        for kv in spec.extra_d:
            cmd += list(kv)
        if spec.reduces > 0:
            cmd += ["-D", "mapreduce.output.textoutputformat.separator="]
        cmd += ["-files", ",".join(files)]
        for i in spec.inputs:
            cmd += ["-input", i]
        cmd += ["-output", spec.output,
                "-mapper", "%s %s %s %s" % (py, spec.script, spec.mapper_args, common)]
        cmd += ["-reducer", ("%s %s --reduce %s %s"
                             % (py, spec.script, spec.reducer_args or "", common))
                if spec.reduces > 0 else "cat"]
        # 提交前清掉同名输出与历史日志；命令在 env 注入的 bash 里执行。
        bash = ('rm -rf "%s" 2>/dev/null; '
                'hdfs dfs -rm -r -f "%s" >/dev/null 2>&1 || true; '
                + hdfs_env_exports() + '%s') % (
                    logf, spec.output, " ".join('"%s"' % c for c in cmd))
        rt.run_shell(["bash", "-c", bash], logf)