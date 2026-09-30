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
  * 两种模式生成的命令与旧实现**逐字符一致**（单测锁定）；
  * 计数器合并语义照旧：submit_stage 模式返回 stderr 文本供调用方 parse_counters，
    裸 jar 模式返回 None（历史行为就是不合并，保留）；
  * 参数漂移（speculative / separator）的收敛**不做** —— 需要集群冒烟验证后
    定（拍板 #5：VM 侧小样本冒烟），此处只统一代码路径。
"""

import os
import re
import sys

from driver import run_task as rt

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def hdfs_env_exports():
    """伪分布式 hdfs 命令的环境注入（原本散在三处的 .vendor 前缀收敛于此）。

    只组装 export 段，具体命令由调用方拼接；行为与旧实现逐字符一致。
    """
    return ('export HADOOP_CONF_DIR="%s/hadoop/conf"; export JAVA_HOME="%s/.vendor/jdk-11"; '
            'export HADOOP_HOME="%s/.vendor/hadoop-3.3.6"; '
            'export PATH="$JAVA_HOME/bin:$HADOOP_HOME/bin:$PATH"; '
            % (REPO_ROOT, REPO_ROOT, REPO_ROOT))


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

    def __init__(self, task_dir, tid):
        self.task_dir = task_dir
        self.tid = tid

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
        files = [os.path.join(REPO_ROOT, "hadoop", "engine.zip"),
                 os.path.join(REPO_ROOT, "config", "cleaning_rules.v1.json"),
                 os.path.join(REPO_ROOT, "config", "scoring_scheme.v1.json"),
                 os.path.join(REPO_ROOT, "hadoop", "jobs", spec.script),
                 os.path.join(REPO_ROOT, "hadoop", "jobs", "_common.py")]
        if spec.extra_files:
            files += [f for f in spec.extra_files.split(",") if f]
        common = ("--rules cleaning_rules.v1.json --scoring scoring_scheme.v1.json "
                  "--task-id %s" % self.tid)
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