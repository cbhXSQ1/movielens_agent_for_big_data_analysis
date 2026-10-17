# -*- coding: utf-8 -*-
"""agent/gateway/curl_gateway.py —— opencode-go 网关适配（**进程内**模块）。

原先这套适配逻辑跑在一个独立的小 HTTP 服务里（opencode_proxy.py）：把 Agent 的
OpenAI 兼容流式请求转发到 opencode-go 网关、补网关专属的 `x-opencode-session`
会话头、边收边转。现在取消独立进程，改成 **传输层按 URL 分派**：Agent 自己识别
opencode 网关（host 命中 opencode.ai）后走这里，其余标准端点仍走 urllib。

为什么不直接用 urllib 打 opencode.ai：
  * opencode.ai 在 Cloudflare 后按 TLS 指纹封禁 python-urllib（实测 403 error 1010），
    而 curl 的 TLS 指纹可正常通过 —— 故该分支转发用系统 curl。
  * 网关按 `x-opencode-session` 做会话路由（gateway-probe.md V2），标准 OpenAI 客户端
    不发这个头；由本模块把 Agent 的 `x-agent-session` 会话键转成它。

对外只暴露四样（transport.py 复用）：
    is_opencode_gateway(url)        —— 该 URL 是否命中 opencode 网关
    adapt_session_headers(headers)  —— x-agent-session → x-opencode-session（缺省补稳定 UUID）
    default_session_id()            —— 进程内稳定的缺省会话 UUID
    stream_via_curl(...)            —— curl 流式转发，逐行产出响应文本
"""

import subprocess
import urllib.parse
import uuid

#: opencode 网关的域名（含子域）；host 命中即走 curl 分支
OPENCODE_GATEWAY_HOST = "opencode.ai"

#: Agent 侧约定的会话键请求头
AGENT_SESSION_HEADER = "x-agent-session"
#: opencode 网关要求的会话请求头
GATEWAY_SESSION_HEADER = "x-opencode-session"

DEFAULT_CURL = "curl"
DEFAULT_TIMEOUT = 30.0

#: 进程内稳定的缺省会话 UUID：无会话键的多轮请求共享同一个值，保证网关侧连续。
_PROCESS_SESSION_ID = None


def default_session_id():
    """返回进程内稳定的缺省会话 UUID（首次调用时生成，之后不变）。"""
    global _PROCESS_SESSION_ID
    if _PROCESS_SESSION_ID is None:
        _PROCESS_SESSION_ID = str(uuid.uuid4())
    return _PROCESS_SESSION_ID


def is_opencode_gateway(url):
    """解析 host：命中 opencode 网关（opencode.ai 或其子域）返回 True。"""
    try:
        host = urllib.parse.urlsplit(url or "").hostname or ""
    except ValueError:
        return False
    host = host.lower()
    return host == OPENCODE_GATEWAY_HOST or host.endswith("." + OPENCODE_GATEWAY_HOST)


def adapt_session_headers(headers):
    """把 Agent 请求头适配成网关请求头：x-agent-session → x-opencode-session。

    * 若有 `x-agent-session`，取其值转成 `x-opencode-session`（值相同），并去掉前者；
    * 若没有，则补一个**进程内稳定的** UUID（不是每次随机），保证同进程多轮一致；
    * 已存在的 `x-opencode-session` 由转出的会话键覆盖（避免冲突）。
    """
    out = {}
    agent_key = None
    for name, value in (headers or {}).items():
        lowered = name.lower()
        if lowered == AGENT_SESSION_HEADER:
            agent_key = value
            continue
        if lowered == GATEWAY_SESSION_HEADER:
            continue
        out[name] = value
    out[GATEWAY_SESSION_HEADER] = agent_key or default_session_id()
    return out


def _fmt_timeout(timeout):
    value = DEFAULT_TIMEOUT if timeout is None else float(timeout)
    return "%g" % value


def build_curl_command(url, headers, timeout, curl_bin=DEFAULT_CURL):
    """拼出 curl 命令：POST、不缓冲、请求体来自 stdin。便于单测断言。"""
    cmd = [curl_bin, "-sS", "-N", "--no-buffer",
           "--max-time", _fmt_timeout(timeout),
           "-X", "POST", url]
    for name, value in adapt_session_headers(headers).items():
        cmd.extend(["-H", "%s: %s" % (name, value)])
    cmd.extend(["--data-binary", "@-"])
    return cmd


def _reap(proc):
    """收尾：确保子进程被终止回收（客户端断开 / 提前 break 都走这里）。"""
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except Exception:
        pass
    if proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
    for stream in (proc.stdout, proc.stderr):
        try:
            if stream:
                stream.close()
        except Exception:
            pass


def _stderr_text(proc):
    try:
        raw = proc.stderr.read() or b""
    except Exception:
        raw = b""
    return raw.decode("utf-8", "replace").strip()


def _raise_for_exit(proc, url):
    """curl 非零退出 → 抛可被 classify_exception 归类的异常。"""
    rc = proc.wait()
    if rc == 0:
        return
    detail = _stderr_text(proc)[:300]
    if rc == 28:                                    # curl: operation timeout
        raise TimeoutError("curl 转发超时（rc=28）：%s" % (detail or url))
    raise OSError("curl 转发失败（rc=%s）：%s" % (rc, detail or url))


def stream_via_curl(url, headers=None, body=None, timeout=None,
                    curl_bin=DEFAULT_CURL):
    """curl 流式转发：POST 请求体到网关，逐行产出响应文本片段（不缓冲）。

    * 请求体写进 curl 的 stdin；
    * **逐行读 stdout** 产出文本片段（SSE 的切行交给 SseStreamParser）；
    * 上游不是 SSE（错误 JSON / HTML）时整体包成一帧 `data:`，让 Agent 侧可见；
    * launch 失败 / 非零退出 / 超时都抛可被 classify_exception 归类的异常。
    """
    cmd = build_curl_command(url, headers, timeout, curl_bin)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise OSError("无法启动 curl 转发（curl 不可用？）：%s" % exc)
    try:
        try:
            if body:
                proc.stdin.write(body)
            proc.stdin.close()
        except Exception:
            pass

        # 先读第一行：是 SSE 还是错误体；也避免上游无响应时白等。
        first = proc.stdout.readline()
        if not first:
            _raise_for_exit(proc, url)
            return                                  # 正常但空响应
        if not first.lstrip().startswith(b"data:"):
            rest = proc.stdout.read() or b""
            blob = (first + rest).decode("utf-8", "replace").strip()
            yield "data: %s\n\n" % blob
            _raise_for_exit(proc, url)
            return

        yield first.decode("utf-8", "replace")
        for raw_line in proc.stdout:                # 逐行、边收边转
            yield raw_line.decode("utf-8", "replace")
        _raise_for_exit(proc, url)
    finally:
        _reap(proc)
