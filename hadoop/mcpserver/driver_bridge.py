# -*- coding: utf-8 -*-
"""mcpserver/driver_bridge.py —— 本端调用 driver 的唯一通道（子进程 + 信封解析 + 失败分类）。

为什么要有这一层
----------------
`hadoop/driver/run_task.py` 的既有约定是「一次请求 → stdout 上一个 JSON 信封、
日志走 stderr、退出码带语义」（`docs/hadoop/agent-interface.md` §2）。本层把这个
约定收成一个函数：谁要执行能力，就调 `run_driver`，拿回一个已经解析好的
`DriverResult`；**不复制任何业务逻辑**，判定仍然全部发生在 driver 里（实现方案 §1
红线 2「判定在本端」指的是本端进程内，而不是另写一份判断）。

三条纪律（改这里之前请先读）
----------------------------
1. **绝不拼 shell 字符串**：一律用参数列表调用，路径里的空格、引号、通配符都不会被
   shell 解释。取值一律用 `--键=值` 的**单元素**写法（见 `option_argv`）：这是安全要点，
   不是风格偏好 —— 拆成「键、值」两个元素时，取值只要以两个连字符开头就会被执行侧的
   解析器当成开关，实测能绕过单任务互斥、切到另一条执行路径、甚至把任务跑成前台并
   被超时连带杀掉。以两个连字符开头的取值一律拒绝，理由同见 `_reject_option_like`。
2. **失败要分类，不许糊成一团**：超时 / 无输出 / 输出不是信封 / 跑不起来 各归各位
   （`DRIVER_TIMEOUT` / `DRIVER_NO_OUTPUT` / `DRIVER_BAD_OUTPUT` / `DRIVER_UNREACHABLE`）；
   信封里已经带了结构化码的，走 `contract.normalize_error` 原样保留，绝不降级。
3. **排障信息留在错误里**：stderr 尾部（有上限）、stdout 预览（有上限）、原退出码
   一起塞进错误细节，否则现场只剩一句「失败了」，没人能定位。

环境变量从哪来
--------------
`ML_VAR_DIR` / `HDFS_BASE` / `ML_RAW_DIR` 的权威来源是 `hadoop/scripts/env.sh`
（它自己也是「路径一律可被外部覆盖」的口径）。本层**不硬编码任何路径**：

  * 仓库根取 `ctx.repo_root`（没给时由本文件位置推导，仍然不是写死的字面量）；
  * 三个变量先看进程环境与 `ctx.env`，缺的再从 `env.sh` 读（`bash -c 'source …; env'`）；
  * `env.sh` 只做**兜底补充**，绝不覆盖外部显式给的取值 —— 运维换机器时改了外部变量，
    本端和 driver 必须看到同一个值。

超时口径
--------
发起类子命令（`start` / `score`）是**受理即返回**，不阻塞等作业跑完，因此它只需要
「读方案 + 写状态 + 起后台进程」的时间；`validate` / `schemes` 是纯同步命令。
两者给不同上限（见 `DEFAULT_TIMEOUTS`），并可由 `ctx.config["driver_timeouts"]`
按子命令覆盖（测试与调参都靠这条，不必改代码）。

本模块只用标准库；不导入官方协议包，也不导入 driver 的执行链。
"""

import json
import os
import shlex
import signal
import subprocess
import sys

from .contract import (
    CODE_DRIVER_BAD_OUTPUT,
    CODE_DRIVER_NO_OUTPUT,
    CODE_DRIVER_TIMEOUT,
    CODE_DRIVER_UNREACHABLE,
    CODE_USAGE,
    EXIT_OK,
    StructuredError,
    normalize_error,
)

# ---------------------------------------------------------------------------
# 子命令名（只登记本段用得到的；其余子命令由别的处理器负责）
# ---------------------------------------------------------------------------

CMD_VALIDATE = "validate"
CMD_SCHEMES = "schemes"
CMD_START = "start"
CMD_SCORE = "score"

#: driver 入口与 env 脚本的**相对**位置（相对仓库根；不是绝对路径）。
DRIVER_RELPATH = os.path.join("hadoop", "driver", "run_task.py")
ENV_SCRIPT_RELPATH = os.path.join("hadoop", "scripts", "env.sh")

#: 从 env.sh 里认的变量：数据/产物位置三件套，加几个执行链会用到的位置。
#: 只收白名单，避免把整个 shell 环境（含口令类变量）照抄进子进程。
ENV_KEYS = (
    "ML_VAR_DIR",
    "HDFS_BASE",
    "ML_RAW_DIR",
    "ML_REPO_ROOT",
    "PYTHON_BIN",
    "STREAMING_JAR",
    "HADOOP_HOME",
    "HADOOP_CONF_DIR",
    "JAVA_HOME",
)

#: 同步命令的超时上限（秒）。读两份方案 JSON 的正常耗时是毫秒级；给到分钟级
#: 是留给「机器正被全量作业压满」的情况，真要这么久基本可以判定通道异常了。
TIMEOUT_SYNC_SECONDS = 120.0

#: 发起类命令的超时上限（秒）。受理即返回，不等作业；这里同样只覆盖
#: 「起后台进程之前」的那段工作，**不是**任务本身的耗时。
TIMEOUT_ACCEPT_SECONDS = 180.0

#: 子命令 → 默认超时。
DEFAULT_TIMEOUTS = {
    CMD_VALIDATE: TIMEOUT_SYNC_SECONDS,
    CMD_SCHEMES: TIMEOUT_SYNC_SECONDS,
    CMD_START: TIMEOUT_ACCEPT_SECONDS,
    CMD_SCORE: TIMEOUT_ACCEPT_SECONDS,
}

#: 错误里保留的 stderr 尾部字符数 / stdout 预览字符数（有上限，避免把整份日志
#: 塞进信封；作业日志另有专门的工具可取）。
STDERR_TAIL_CHARS = 2000
STDOUT_PREVIEW_CHARS = 400

#: env.sh 解析结果缓存：解析要起一个 bash，没必要每次调用都付这笔钱。
_ENV_SH_CACHE = {}


# ---------------------------------------------------------------------------
# 上下文取值（仓库根 / 解释器 / 超时）
# ---------------------------------------------------------------------------

def _ctx_config(ctx):
    config = getattr(ctx, "config", None)
    return dict(config) if isinstance(config, dict) else {}


def _ctx_env(ctx):
    """上下文里的环境变量（只收非空字符串；空串按「未提供」处理）。"""
    raw = getattr(ctx, "env", None)
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if text.strip():
            out[key] = text
    return out


def repo_root_of(ctx=None):
    """仓库根：**只从上下文取**；上下文没给时按本文件位置推导（不写死字面量）。"""
    root = getattr(ctx, "repo_root", None)
    if root:
        return os.path.abspath(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def driver_script(ctx=None):
    """driver 入口的绝对路径：默认 `<仓库根>/hadoop/driver/run_task.py`。

    `ctx.config["driver_script"]` 可覆盖 —— 测试用假脚本走这条，不必打桩模块。
    """
    override = _ctx_config(ctx).get("driver_script")
    if override:
        return os.path.abspath(str(override))
    return os.path.join(repo_root_of(ctx), DRIVER_RELPATH)


def python_for(ctx=None):
    """跑 driver 的解释器：默认与当前进程同一个，允许显式覆盖。

    用 `sys.executable` 而不是字面量 `python3`：服务若跑在仓库内独立环境里，
    子进程也该用同一个解释器；driver 只依赖标准库，换解释器不影响行为。
    """
    override = _ctx_config(ctx).get("driver_python")
    if override:
        return str(override)
    return sys.executable or "python3"


def timeout_for(subcommand, ctx=None):
    """子命令的超时上限：默认见 `DEFAULT_TIMEOUTS`，可被上下文覆盖。"""
    overrides = _ctx_config(ctx).get("driver_timeouts") or {}
    if subcommand in overrides and overrides[subcommand] is not None:
        return float(overrides[subcommand])
    return DEFAULT_TIMEOUTS.get(subcommand, TIMEOUT_ACCEPT_SECONDS)


# ---------------------------------------------------------------------------
# 环境变量
# ---------------------------------------------------------------------------

def _parse_env_text(text):
    """把 `env` 的输出解析成字典，只留白名单里的键。"""
    values = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in ENV_KEYS and value.strip():
            values[key] = value.strip()
    return values


def _bash_env_runner(script):
    """`source <env.sh>; env` —— env.sh 的正确用法就是 source，不是执行。"""
    command = ["bash", "-c", "source %s >/dev/null 2>&1; env" % shlex.quote(script)]
    try:
        output = subprocess.check_output(command, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return output.decode("utf-8", "replace")


def read_env_sh(repo_root=None, runner=None):
    """读 `hadoop/scripts/env.sh` 的导出变量；文件不在或解析失败就回空字典。

    **不抛错**是有意的：缺 env.sh 的环境里（比如只跑契约测试的精简 checkout），
    本端仍应能用「进程环境 + 上下文」给的取值工作，而不是直接起不来。
    """
    script = os.path.join(repo_root or repo_root_of(None), ENV_SCRIPT_RELPATH)
    if script in _ENV_SH_CACHE:
        return dict(_ENV_SH_CACHE[script])
    if not os.path.isfile(script):
        _ENV_SH_CACHE[script] = {}
        return {}
    text = (runner or _bash_env_runner)(script)
    values = _parse_env_text(text)
    _ENV_SH_CACHE[script] = values
    return dict(values)


def clear_env_cache():
    """清空 env.sh 解析缓存（测试用，避免用例之间互相污染）。"""
    _ENV_SH_CACHE.clear()


def build_env(ctx=None):
    """给子进程准备环境：进程环境 < 上下文 < env.sh 兜底。

    优先级说明：外部显式配置（进程环境、`ctx.env`）永远优先；`env.sh` 只补空缺。
    这样运维改了 `ML_VAR_DIR`（运维脚本与本端用同一个值），driver 看到的就是同一个
    目录，不会出现「本端查 A 目录、driver 写 B 目录」这种最难查的错位。
    """
    env = dict(os.environ)
    env.update(_ctx_env(ctx))
    for key, value in read_env_sh(repo_root_of(ctx)).items():
        if value and not env.get(key):
            env[key] = value
    return env


def env_value(name, ctx=None, default=None):
    """取解析后的环境变量（给需要知道产物目录/原始区位置的处理器复用）。"""
    return build_env(ctx).get(name) or default


# ---------------------------------------------------------------------------
# 命令行装配
# ---------------------------------------------------------------------------

#: 取值里一律不许出现的前缀。执行侧的解析器把"下一个词以两个连字符开头"当成开关
#: （`hadoop/driver/cli.py` 的 `parse_args`），所以只要取值以两个连字符开头，
#: 无论怎么拼参数，它都可能被下游当成选项而不是取值。
OPTION_LIKE_PREFIX = "--"


def _reject_option_like(key, text):
    """取值看起来像选项就拒绝 —— 让「选项集合」永远不受数据影响。

    为什么不"转义后放行"：转义只在**我们知道下游怎么解释**时才可靠，而取值一路会经过
    若干层（桥接 → 执行侧解析器 → 作业命令行）。拒绝是唯一不依赖下游实现细节的做法，
    而且失败点就在调用者眼前，说明里直接告诉他怎么改。
    """
    if text.startswith(OPTION_LIKE_PREFIX):
        raise StructuredError(
            CODE_USAGE,
            "%s 的取值不能以两个连字符开头（收到 %r）：这种写法在命令行里会被当成开关，"
            "而不是取值。请去掉开头的连字符再试。" % (key, text),
            param=key,
            value=text,
            reason="option_like_value")


def _assert_option_argv(options, argv):
    """结构自检：参数列表里只允许出现「我们自己生成的选项 + 用等号携带的取值」。

    这是给将来改动的护栏。审查实测过旧写法的三种后果：`tag="--force"` 能绕过单任务
    互斥、`tag="--exec"` 会切到另一条执行路径、`--foreground` 会让任务在前台跑并被
    桥接的超时连同进程组杀掉，留下"状态说在跑、进程其实没了"的残留占用。护栏让这类
    回归在开发期就炸掉，而不是等对端发现"某个标签把任务跑成了另一种形态"。
    """
    flags = set("--%s" % key for key, value in (options or {}).items() if value is True)
    known = set(options or {})
    for item in argv:
        if not isinstance(item, str) or not item.startswith("--"):
            raise StructuredError(
                CODE_DRIVER_BAD_OUTPUT,
                "参数列表里出现了不是选项的元素：%r" % (item,))
        if "=" in item:
            key = item[2:].split("=", 1)[0]
            if not key or key.startswith("-") or key not in known:
                raise StructuredError(
                    CODE_DRIVER_BAD_OUTPUT, "参数列表里出现了非法选项键：%r" % (item,))
            continue
        if item not in flags:
            # 既不是等号写法、也不是我们确实要发的开关 —— 说明有人把取值单独塞进来了
            raise StructuredError(
                CODE_DRIVER_BAD_OUTPUT,
                "参数列表里出现了非预期的裸选项 %r：取值必须用「--键=值」的写法，"
                "否则取值会被执行侧解析成开关" % (item,))


def option_argv(options):
    """把选项字典摊成参数列表：`None`/空串跳过，`True` 摊成开关，`False` 整个不收。

    键名直接用 driver 的选项名（带连字符，如 `data-version`），不做重命名 ——
    这一层只负责搬运，不负责起第二套命名。

    **取值一律用 `--键=值` 的单元素写法，这是本模块的安全要点。** 执行侧的解析器先按
    等号切分（`hadoop/driver/cli.py` 的 `parse_args`：`if "=" in key: key, val = ...`），
    所以取值里出现连字符不会被当成开关。反过来，旧写法把键与取值拆成两个元素传，取值
    只要以两个连字符开头就会被解析成独立选项 —— 那条通道正是参数走私的入口，现在从根上
    堵死：取值再也无法改变选项集合。
    """
    argv = []
    for key, value in (options or {}).items():
        if value is None:
            continue
        if value is True:
            argv.append("--%s" % key)
            continue
        if value is False:
            continue
        text = value if isinstance(value, str) else str(value)
        if not text.strip():
            continue                       # 空串视同未提供（与 driver 既有语义一致）
        _reject_option_like(key, text)
        argv.append("--%s=%s" % (key, text))
    _assert_option_argv(options, argv)
    return argv


def build_argv(subcommand, options=None, ctx=None):
    """`<解释器> <driver 入口> <子命令> [选项…]` —— 全参数列表，没有任何 shell 拼接。"""
    argv = [python_for(ctx), driver_script(ctx), subcommand]
    argv.extend(option_argv(options))
    return argv


# ---------------------------------------------------------------------------
# 一次调用的结果
# ---------------------------------------------------------------------------

class DriverResult(object):
    """一次 driver 调用的结果：信封 + 退出码 + 两侧输出（供排障）。

    只包住「已经成功解析出信封」的情况；传输层故障（超时/无输出/输出不合法/跑不起来）
    在 `run_driver` 里就已经抛成 `StructuredError`，不会流到这里 ——
    否则处理器要同时处理两种失败形态，很容易漏掉一种。
    """

    def __init__(self, subcommand, argv, returncode, envelope, stdout, stderr):
        self.subcommand = subcommand
        self.argv = list(argv)
        self.returncode = returncode
        self.envelope = dict(envelope)
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self):
        return bool(self.envelope.get("ok"))

    @property
    def payload(self):
        """业务字段（去掉 `ok` / `interface_version` 外壳，由分派层重新包）。"""
        return {key: value for key, value in self.envelope.items()
                if key not in ("ok", "interface_version")}

    def error(self):
        """失败信封 → 归一后的错误字典；成功时返回 None。

        走 `contract.normalize_error`：`CONFIG_INVALID` / `TASK_ALREADY_RUNNING` /
        `DEPENDENCY_MISSING` 这类 driver 自产码原样保留，并补上原退出码留档。
        """
        if self.ok:
            return None
        err = self.envelope.get("error")
        if not isinstance(err, dict):
            err = {"message": str(err) if err is not None else ""}
        return normalize_error(err, self.returncode)

    def require_ok(self):
        """成功返回业务字段；失败抛 `StructuredError`。"""
        return require_ok(self)

    def stderr_tail(self):
        return _tail(self.stderr)


def _tail(text, limit=STDERR_TAIL_CHARS):
    text = text or ""
    return text[-limit:].strip()


def _preview(text, limit=STDOUT_PREVIEW_CHARS):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def structured_error(result, message=None, **extra):
    """把 `ok=false` 的调用结果整体转成 `StructuredError`；成功时返回 None。

    处理器需要「在 driver 的结论上补说明」时用它：先拿到异常对象、改 `message`
    或补字段，再抛出去 —— 结论（code）始终是 driver 的，不在这里改写。
    """
    err = result.error()
    if err is None:
        return None
    detail = {key: value for key, value in err.items() if key not in ("code", "message")}
    detail.update(extra)
    code = err.get("code") or CODE_DRIVER_BAD_OUTPUT
    text = message or err.get("message") or ""
    return StructuredError(code, text, **detail)


def require_ok(result):
    """成功返回业务字段；失败抛 `StructuredError`。处理器最常用的入口。"""
    exc = structured_error(result)
    if exc is not None:
        raise exc
    return result.payload


# ---------------------------------------------------------------------------
# 调用
# ---------------------------------------------------------------------------

def _terminate(proc):
    """超时后终止**整个进程组**。

    driver 的发起类命令自己会用新的会话起后台作业（`start_new_session=True`），
    所以按进程组杀只会带走我们自己起的那个 driver 进程，不波及已经在跑的任务。
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:                # pragma: no cover —— 进程已经没了
            pass


def _interpret(subcommand, argv, returncode, stdout, stderr):
    """把一次调用的原始产物解释成 `DriverResult`，或抛传输层错误。

    判定顺序是刻意的：先说「有没有输出」，再说「是不是信封」，最后才看退出码 ——
    这样「driver 崩了、半个 JSON 都没吐」不会被误报成业务失败（`TASK_FAILED`）。
    """
    text = (stdout or "").strip()
    detail = {
        "subcommand": subcommand,
        "driver_code": returncode,
        # 只留选项部分，省得把解释器与入口路径塞进信封
        "argv_options": list(argv[3:]),
    }

    if not text:
        raise StructuredError(
            CODE_DRIVER_NO_OUTPUT,
            "driver 的 %s 没有在标准输出上给出任何内容（退出码 %d），"
            "无法判断这次调用的结果；请重试，持续出现请检查本端的执行通道"
            % (subcommand, returncode),
            stderr_tail=_tail(stderr), **detail)

    try:
        envelope = json.loads(text)
    except ValueError:
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 的 %s 输出不是合法 JSON（退出码 %d），可能是执行通道被其它输出污染；"
            "请重试，持续出现请检查本端的执行通道" % (subcommand, returncode),
            stdout_preview=_preview(text), stderr_tail=_tail(stderr), **detail)

    if not isinstance(envelope, dict):
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 的 %s 输出是 JSON，但不是信封对象（收到 %s）"
            % (subcommand, type(envelope).__name__),
            stdout_preview=_preview(text), stderr_tail=_tail(stderr), **detail)

    if "ok" not in envelope:
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 的 %s 输出缺少 ok 字段，不构成合法信封" % subcommand,
            stdout_preview=_preview(text), stderr_tail=_tail(stderr), **detail)

    if envelope.get("ok") and returncode != EXIT_OK:
        # 信封说成功、退出码说非零：两者矛盾时按协议故障处理，**不猜**。
        # 若把它当成功，可能把一次中途出错的调用当成跑通了；当业务失败又是在编造结论。
        raise StructuredError(
            CODE_DRIVER_BAD_OUTPUT,
            "driver 的 %s 回了成功信封，但退出码是 %d，两者矛盾；"
            "为避免把可疑结果当成功，按执行通道故障上报" % (subcommand, returncode),
            stdout_preview=_preview(text), stderr_tail=_tail(stderr), **detail)

    return DriverResult(subcommand, argv, returncode, envelope,
                        stdout or "", stderr or "")


def run_driver(subcommand, options=None, ctx=None, timeout=None):
    """调用 driver 的一个子命令，返回 `DriverResult`；传输层故障抛 `StructuredError`。

    `options` 的键是 driver 的选项名（带连字符），值给 `True` 就是开关；
    `None` / 空串会被跳过（交给 driver 的默认值，本端不重复一套默认值）。
    """
    argv = build_argv(subcommand, options, ctx)
    limit = timeout if timeout is not None else timeout_for(subcommand, ctx)
    cwd = repo_root_of(ctx)

    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=build_env(ctx),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError as exc:
        raise StructuredError(
            CODE_DRIVER_UNREACHABLE,
            "起不了 driver 子进程（%s）：%s；请检查本端服务所在机器的解释器与仓库文件是否完好"
            % (subcommand, exc),
            subcommand=subcommand, driver_code=None)

    try:
        out, err = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        _terminate(proc)
        try:
            out, err = proc.communicate()
        except (OSError, ValueError):          # pragma: no cover —— 进程已不可读
            out, err = b"", b""
        raise StructuredError(
            CODE_DRIVER_TIMEOUT,
            "driver 的 %s 在 %.0f 秒内没有返回，已终止这次调用（不涉及已经在跑的任务）；"
            "请重试，持续出现请检查本端服务与 driver 所在机器的负载"
            % (subcommand, limit),
            subcommand=subcommand, timeout_seconds=limit,
            stderr_tail=_tail(_decode(err)),
            stdout_preview=_preview(_decode(out)))

    return _interpret(subcommand, argv, proc.returncode,
                      _decode(out), _decode(err))


def _decode(raw):
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return raw
