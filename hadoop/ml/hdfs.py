# -*- coding: utf-8 -*-
"""ml/hdfs.py —— 从发布区取数（只用标准库，不依赖服务层）。

三条纪律：

1. **参数列表调用，不拼 shell 字符串**：命令与其参数作为位置参数传给 `bash -c`，
   路径与表名不进字符串拼接；
2. **两种失败必须分开**：「发布区没有这个文件」与「连不上集群」是两件事 —— 前者可能只是还没发布，
   后者是环境问题。做法是把子进程语言固定成英文（`LC_ALL=C`），再按报错文案判断；
3. **报错必带原因**：拿不到数据就如实说清是哪一步、缺什么，绝不回空数据冒充"没有数据"。
"""

import os
import subprocess

from .errors import TrainingError

#: 子进程超时（秒）：取三张表都是本机回环，正常几秒；给足余量但不无限等。
DEFAULT_TIMEOUT = 300

#: '文件不存在' 的报错特征（英文，见模块说明）。
_MISSING_MARKERS = (
    "no such file or directory",
    "file does not exist",
    "does not exist",
)

#: '连不上' 的报错特征。
_UNREACHABLE_MARKERS = (
    "connection refused",
    "connect timed out",
    "connection timed out",
    "call from",
    "retrying connect",
    "could not obtain block",
    "server is not running",
    "safemode",
    "access denied",
    "permission denied",
)


def env_script(repo_root):
    return os.path.join(repo_root, "hadoop", "scripts", "env.sh")


def build_command(repo_root, args):
    """拼出一次集群命令的调用方式：`bash -c 'source <env.sh>; exec "$@"' <env.sh> hdfs <args...>`。

    为什么不是 `hdfs dfs ...`：本仓库的 Hadoop 装在仓库内（`.vendor`），PATH/JAVA_HOME 都由
    `env.sh` 现算；source 之后 `exec` 换上真正的 hdfs 进程，命令与参数作为位置参数传入，
    不会被拼进 shell 字符串。
    """
    script = env_script(repo_root)
    if not os.path.isfile(script):
        raise TrainingError(
            "DEPENDENCY_MISSING",
            "找不到集群环境脚本（仓库内 hadoop/scripts/env.sh），无法读取发布区",
            script="hadoop/scripts/env.sh")
    env = dict(os.environ)
    env["LC_ALL"] = "C"          # 锁定英文报错，见模块说明第 2 条
    env.pop("LANGUAGE", None)
    command = ["bash", "-c", 'source "$0" >/dev/null 2>&1; exec "$@"', script, "hdfs"]
    command += [str(item) for item in args]
    return command, env


def _classify_failure(stderr_text, what):
    """把一次失败归类：文件不存在 / 连不上集群 / 其它。"""
    text = (stderr_text or "").lower()
    if any(marker in text for marker in _MISSING_MARKERS):
        return TrainingError(
            "DEPENDENCY_MISSING",
            "发布区里没有 %s：可能还没发布过这个数据版本" % what,
            missing=what, reason="not_published")
    if any(marker in text for marker in _UNREACHABLE_MARKERS):
        return TrainingError(
            "TASK_FAILED",
            "连不上集群，无法读取 %s；这不是「没有发布数据」，请确认集群是否在运行" % what,
            missing=what, reason="cluster_unreachable")
    return TrainingError(
        "TASK_FAILED",
        "读取 %s 失败：%s" % (what, _tail(stderr_text)),
        missing=what, reason="hdfs_error")


def _tail(text, limit=300):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def stream_to_file(repo_root, hdfs_path, local_path, what=None, timeout=DEFAULT_TIMEOUT):
    """把集群上的文件流式写到本机（`hdfs dfs -cat`）；失败抛结构化错误并清理半成品。"""
    what = what or hdfs_path
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    command, env = build_command(repo_root, ["dfs", "-cat", hdfs_path])
    tmp_path = local_path + ".part"
    try:
        with open(tmp_path, "wb") as fh:
            proc = subprocess.run(command, env=env, stdout=fh, stderr=subprocess.PIPE,
                                  timeout=timeout)
    except subprocess.TimeoutExpired:
        _safe_remove(tmp_path)
        raise TrainingError(
            "TASK_FAILED", "读取 %s 超时（%s 秒），已中断" % (what, timeout),
            missing=what, reason="hdfs_timeout")
    except OSError as exc:
        _safe_remove(tmp_path)
        raise TrainingError(
            "TASK_FAILED", "无法启动集群命令：%s" % (getattr(exc, "strerror", None) or exc),
            missing=what, reason="hdfs_unavailable")
    if proc.returncode != 0:
        _safe_remove(tmp_path)
        raise _classify_failure(proc.stderr.decode("utf-8", "replace"), what)
    if os.path.getsize(tmp_path) == 0:
        _safe_remove(tmp_path)
        raise TrainingError(
            "DEPENDENCY_MISSING", "发布区里的 %s 是空的，无法据此训练" % what,
            missing=what, reason="empty")
    os.replace(tmp_path, local_path)
    return local_path


def read_text(repo_root, hdfs_path, what=None, timeout=DEFAULT_TIMEOUT):
    """读一个小文本（例如发布区的内容清单）；不存在或连不上都如实归类。"""
    what = what or hdfs_path
    command, env = build_command(repo_root, ["dfs", "-cat", hdfs_path])
    try:
        proc = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TrainingError(
            "TASK_FAILED", "读取 %s 超时（%s 秒）" % (what, timeout),
            missing=what, reason="hdfs_timeout")
    except OSError as exc:
        raise TrainingError(
            "TASK_FAILED", "无法启动集群命令：%s" % (getattr(exc, "strerror", None) or exc),
            missing=what, reason="hdfs_unavailable")
    if proc.returncode != 0:
        raise _classify_failure(proc.stderr.decode("utf-8", "replace"), what)
    return proc.stdout.decode("utf-8", "replace")


def exists(repo_root, hdfs_path, timeout=DEFAULT_TIMEOUT):
    """判断集群上是否存在某路径（存在 True / 不存在 False / 连不上 抛错）。"""
    command, env = build_command(repo_root, ["dfs", "-test", "-e", hdfs_path])
    try:
        proc = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise TrainingError(
            "TASK_FAILED", "检查发布区路径时出错：%s" % (exc,),
            missing=hdfs_path, reason="hdfs_unavailable")
    if proc.returncode == 0:
        return True
    text = proc.stderr.decode("utf-8", "replace").lower()
    if not text.strip():
        # -test 失败时通常不输出任何东西：此时无法区分"不存在"与"连不上"，
        # 退回到一次 list 探测，用它的报错来判断（宁肯多一次调用，也不猜）。
        command, env = build_command(repo_root, ["dfs", "-ls", hdfs_path])
        try:
            probe = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise TrainingError(
                "TASK_FAILED", "检查发布区路径时出错：%s" % (exc,),
                missing=hdfs_path, reason="hdfs_unavailable")
        if probe.returncode == 0:
            return True
        probe_text = probe.stderr.decode("utf-8", "replace").lower()
        if any(marker in probe_text for marker in _MISSING_MARKERS) or not probe_text.strip():
            return False
        raise _classify_failure(probe.stderr.decode("utf-8", "replace"), hdfs_path)
    if any(marker in text for marker in _MISSING_MARKERS):
        return False
    raise _classify_failure(proc.stderr.decode("utf-8", "replace"), hdfs_path)


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
