# -*- coding: utf-8 -*-
"""第 5 节 Agent：零依赖 HTTP 接口（阶段 6 契约端点 + 既有直取端点）。

只用 Python 标准库，不需要 pip 装任何东西。

启动：
    python3 -m agent.http_api --port 8765

阶段 6（ag-contract）在本文件上实现 `docs/agent/Agent_接口文档.md` §3 的前端 ↔ Agent 契约：

    健康检查            GET  /health
    新建对话            POST /api/conversations
    列出对话            GET  /api/conversations
    删除对话            DELETE /api/conversations/{cid}
    打开推送流(SSE)     GET  /api/conversations/{cid}/stream
    发消息              POST /api/conversations/{cid}/messages   （按 mode 分派 offline / llm）
    设置上下文          POST /api/conversations/{cid}/context
    工具表只读兜底      GET  /api/tools
    长任务进展(T6.2)    GET  /api/conversations/{cid}/tasks       与 …/{agent_task_id}
    结构化结果(T6.3)    GET  /api/conversations/{cid}/tasks/{agent_task_id}/result
    对话树(T7.2/7.3)    GET  /api/conversations/{cid}/tree
    读单节点(T7.3)      GET  /api/conversations/{cid}/nodes/{node_id}
    从节点继续(T7.3)    POST /api/conversations/{cid}/branches
    恢复产物(§3.12)     GET  /api/conversations/{cid}/artifacts
    消息历史(§3.13)     GET  /api/conversations/{cid}/messages

推送流从 `Runtime.open_stream(cid)` 取订阅，同步迭代 `for e in sub`，逐帧输出
`event: <type>\\ndata: <json>\\n\\n`；客户端断开即 `sub.close()`。事件类型与字段严格按
接口文档 §3.6–§3.9；长任务进展 / 结构化结果直接取阶段 4 的 `task_view` / `result_payload`，
不另造。

本文件同时实现 §3.11「资源与文件接口（直通，不经 LLM）」，文件系统逻辑在
`agent/resources.py`：

    列原始数据          GET  /api/data/raw
    列已发布数据        GET  /api/data/published
    取方案内容          GET  /api/schemes/content?name=
    上传方案            POST /api/schemes/upload       （multipart，.json）
    列任务产出文件      GET  /api/tasks/{tid}/files
    预览文件            GET  /api/files/preview?task_id=&path=
    下载产物文件        GET  /api/files/download?task_id=&path=
    下载已发布数据      GET  /api/data/published/download?data_version=&table=

既有 driver 直取端点（`/api/chat`、`/api/tasks…`、`/api/schemes`、`/api/validate`、
`/api/llm/test`）原样保留，互不冲突。

二进制下载不再读本机文件：改由 Agent 带口令经远端能力服务的两条只读路由
（`/download/task/…`、`/download/published/…`）**流式**中转给浏览器（见
`agent/mcp_download.py`）。

所有响应都是 JSON 信封，失败时如实返回错误码与原因，绝不造数。
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import explain, resources, tools

SERVER_VERSION = "agent-http-api/2.0"

#: SSE 空闲心跳间隔（秒）。Windows + 浏览器/代理下，定期写一帧注释既能保活，
#: 又能及时探测到客户端断开（写失败即断）。
SSE_HEARTBEAT_SECONDS = 15.0

#: /api/chat 允许透传的运行口径参数（其余字段一律丢弃，防脏参数透传给 driver）
OPTION_FIELDS = ("rules", "scoring", "data_version", "tag", "exec_mode", "scope")

#: 运行时单例（阶段 3a：自然语言入口接到 LangGraph 运行时）。
#: 延迟创建：即便没装 LangGraph，除 /api/chat 外的只读端点仍可用。
_RUNTIME = None



def _get_runtime():
    """取（或建）运行时单例。缺 LangGraph 依赖时向上抛，由调用方转结构化错误。

    生产装配（阶段 8 T8.9）：按 `mcp_config` 建真实远端客户端 —— 配齐地址与口令时用
    `McpHttpClient`，否则用 `NullMcpClient`（远端能力按 `MCP_UNAVAILABLE` 诚实报错，
    Agent 照常启动）。长任务后台进度轮询改走远端探针 `RemoteTaskProbe`（双编号纪律：
    一律用 `hadoop_task_id` 查状态/取结果）。地址与口令全部来自配置/环境变量，不写死。
    """
    global _RUNTIME
    if _RUNTIME is None:
        from . import mcp_config, mcp_contract
        from .mcp_http_client import McpHttpClient
        from .runtime import Runtime, RemoteTaskProbe
        cfg = mcp_config.load()
        client = McpHttpClient(cfg) if cfg.usable else mcp_contract.NullMcpClient()
        _RUNTIME = Runtime(mcp_client=client, task_probe=RemoteTaskProbe(client))
    return _RUNTIME


def _normalize_opts(source):
    """只保留认识的运行口径键；丢掉 None / 空串；exec_mode/scope 转小写。

    原样迁自已删除的 session.py（口径快照已被对话持久化取代，见重构计划 §5）。
    """
    out = {}
    if not isinstance(source, dict):
        return out
    for key in OPTION_FIELDS:
        value = source.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        if key in ("exec_mode", "scope") and isinstance(value, str):
            value = value.lower()
        out[key] = value
    return out


def _reasoning_effort_option(value):
    """把请求体顶层的 `reasoning_effort` 归一成可透传的思考档位。

    仅 `low`/`medium`/`high`（忽略大小写与首尾空白）会被透传到模型请求体；
    缺省 / 空串 / `default` / 其它任何值一律返回 None（忽略，不发送，也不报错）。
    归一逻辑与模型层同一份（`agent.llm.providers.normalize_reasoning_effort`）。
    """
    from .llm.providers import normalize_reasoning_effort
    return normalize_reasoning_effort(value)


def _decode_body(raw):
    """把请求体解码成字符串，**永不抛异常**。

    HTTP 规范上请求体应当是 UTF-8（浏览器一定如此），但 Windows 命令行工具
    （Git Bash 的 curl / Invoke-WebRequest）会按系统本地编码发，中文环境下是 **GBK**。
    硬按 UTF-8 解码会抛 UnicodeDecodeError，服务端直接 500 且**响应体是空的**，
    前端拿不到任何提示 —— 2026-09-29 实测踩到（0xbd 就是 GBK 的"今"）。

    策略：UTF-8 → GBK → 坏字节替换。三步都失败也保证返回字符串。
    """
    if not raw:
        return "{}"
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 实在解不出来：替换坏字节，宁可得到一段带问号的文本，也不要崩掉
    sys.stderr.write("[warn] 请求体既不是 UTF-8 也不是 GBK，已按替换模式解码\n")
    return raw.decode("utf-8", errors="replace")


def _load_llm_config(body):
    """加载大模型配置。没有 llm_config 模块就返回 None（= 模型层不可用）。"""
    try:
        from . import llm_config
    except Exception:
        return None
    raw = body.get("llm") if isinstance(body.get("llm"), dict) else None
    return llm_config.load(raw)


def _llm_unavailable(cfg):
    """自然语言入口在模型不可用时的结构化错误依据：返回 (code, message)。"""
    if cfg is None:
        return ("LLM_UNSUPPORTED", "当前后端未启用大模型层")
    if not cfg.supported:
        return ("LLM_DISABLED",
                "后端总开关未开：启动前需设置 AGENT_LLM_SUPPORTED=1")
    if cfg.mode == "off":
        return ("LLM_DISABLED", "当前请求已关闭大模型（mode=off）")
    if not cfg.usable:
        return ("LLM_NOT_CONFIGURED", "缺少 api_base / api_key / model")
    return ("LLM_UNAVAILABLE", "大模型不可用")


def _llm_error_envelope(opts, code, message):
    """模型不可用 / 编排失败时的结构化错误信封（与成功信封同形状）。

    按既定决策：自然语言入口只走 LLM 主控循环，**不回退纯规则解析**。
    """
    text = "自然语言入口需要大模型，但当前不可用：%s" % message
    return {
        "ok": False,
        "intent": "chat_llm",
        "intent_cn": "LLM 主控编排",
        "reply": text,
        "error": {"code": code, "message": message},
        "data": {"steps": []},
        "task_id": None,
        "task_started": False,
        "opts": dict(opts or {}),
        "engine": "llm",
        "llm": {"used": False, "steps": [], "note": message},
    }


def _result_envelope(result, opts, conversation_id):
    """把运行时结果映射成前端既定信封形状（ok/intent/reply/data/task_id/llm）。"""
    steps = result.get("steps") or []
    ok = bool(result.get("ok"))
    reply = result.get("reply") or result.get("final") or ""
    error = result.get("error")
    if not ok and not reply:
        reply = (error or {}).get("message") or "（模型未给出有效回答）"
    return {
        "ok": ok,
        "intent": "chat_llm",
        "intent_cn": "LLM 主控编排",
        "reply": reply,
        "error": error,
        "data": {"steps": steps},
        "task_id": result.get("task_id"),
        "task_started": bool(result.get("task_started")),
        "opts": dict(opts or {}),
        "engine": "llm",
        "conversation_id": conversation_id,
        "node_id": result.get("node_id"),
        "llm": {"used": True, "steps": steps,
                "note": ("由大模型编排 %d 步工具调用，数字均来自工具结果" % len(steps))
                        if ok else reply},
    }


def _run_runtime_chat(text, opts, conversation_id, conversation_id_created):
    """LLM 主控路径：走阶段 3a 的 LangGraph 运行时，输出与旧入口同形状的信封。

    - 运行时内部真实收集策略事实，并经策略校验 / 数字护栏；
    - 模型不可用或运行时缺依赖 → 结构化报错（**不静默回退**）；
    - 只有真正发起了任务，才把本轮口径记进对话上下文（替代旧 session 快照）。
    """
    try:
        runtime = _get_runtime()
    except Exception as exc:
        return _llm_error_envelope(
            opts, "LLM_UNSUPPORTED", "运行时不可用（%s）" % exc)
    if conversation_id_created:
        conversation_id = runtime.create_conversation(title=text[:40])
    result = runtime.run_turn(conversation_id, text)
    env = _result_envelope(result, opts, conversation_id)
    recalled = runtime.recall_opts(conversation_id)
    if recalled:
        env["opts"] = dict(recalled)
    if env["task_started"] and env["task_id"]:
        runtime.remember_opts(conversation_id, opts)
    return env


# ---------------------------------------------------------------------------
# 阶段 6 · 契约端点的公共小工具
# ---------------------------------------------------------------------------

def conversation_view(conv):
    """把存储层的对话行整理成接口文档 §3.2 的对外对象。"""
    if not conv:
        return None
    return {"conversation_id": conv.get("id"),
            "title": conv.get("title") or "",
            "created_at": conv.get("created_at"),
            "updated_at": conv.get("updated_at"),
            "current_node_id": conv.get("current_node_id")}


def node_view(node, full=False):
    """把存储层的节点行整理成接口文档 §3.5 的对外对象。

    默认（读树用）只给：编号、父节点、角色、时间、可行摘要、序号；
    `full=True`（读单节点用）再加完整内容与所处线程/检查点。
    """
    if not node:
        return None
    view = {"node_id": node.get("id"),
            "parent_id": node.get("parent_id"),
            "role": node.get("role"),
            "created_at": node.get("created_at"),
            "summary": node.get("summary") or "",
            "seq": node.get("seq")}
    if full:
        view.update({"conversation_id": node.get("conversation_id"),
                     "content": node.get("content") or "",
                     "content_ref": node.get("content_ref"),
                     "thread_id": node.get("thread_id"),
                     "checkpoint_id": node.get("checkpoint_id")})
    return view


def message_view(node):
    """接口文档 §3.13「消息历史」里的单条消息。

    与 `node_view`（读树用）不同：这里给**节点完整内容**（不是摘要），供前端在
    刷新 / 切换对话后把整段对话的可见内容重新铺出来。
    """
    if not node:
        return None
    return {"node_id": node.get("id"),
            "parent_id": node.get("parent_id"),
            "role": node.get("role"),
            "content": node.get("content") or "",
            "created_at": node.get("created_at"),
            "seq": node.get("seq")}


def branch_view(branch):
    """接口文档 §3.5「从节点继续」返回的分支对象。"""
    if not branch:
        return None
    return {"branch_id": branch.get("id"),
            "from_node_id": branch.get("from_node_id"),
            "thread_id": branch.get("thread_id"),
            "parent_thread": branch.get("parent_thread"),
            "created_at": branch.get("created_at")}


def _tool_sources(rows):
    """工具表「来源」字段的并集（健康检查用；当前只可能是 local）。"""
    seen = []
    for row in rows:
        src = row.get("source")
        if src and src not in seen:
            seen.append(src)
    return seen


def _tool_sources_table():
    """接口文档 §3.7 的工具表（单一权威来源：`runtime.stream.tool_table`）。"""
    from .runtime.stream import tool_table
    return tool_table()


def _error_envelope(code, message, **detail):
    """接口文档 §3.10 的错误信封（HTTP 状态码一律 200，判据是 ok）。"""
    err = {"code": code, "message": message}
    for key, value in detail.items():
        if value is not None:
            err[key] = value
    return {"ok": False, "error": err}


def _sse_frame(event):
    """把一个事件信封序列化成一帧 SSE（接口文档 §3.6 线上格式）。"""
    etype = event.get("event") or "message"
    data = json.dumps(event, ensure_ascii=False)
    return ("event: %s\ndata: %s\n\n" % (etype, data)).encode("utf-8")


def _provider_from_body(body):
    """按请求体里的 `llm` 配置造一个模型 provider。

    返回 (provider, error)：`llm` 缺省时 provider 为 None（交给运行时默认工厂，
    没配就结构化报错）；给了但不完整时返回结构化错误，**不静默回落**。
    """
    raw = body.get("llm") if isinstance(body.get("llm"), dict) else None
    if raw is None:
        return None, None
    try:
        from . import llm
    except Exception:
        return None, _error_envelope("LLM_UNSUPPORTED", "当前后端未启用大模型层")
    cfg = _load_llm_config({"llm": raw})
    if cfg is None:
        return None, _error_envelope("LLM_UNSUPPORTED", "当前后端未启用大模型层")
    if not cfg.usable or cfg.mode == "off":
        code, message = _llm_unavailable(cfg)
        return None, _error_envelope(code, message)
    try:
        return llm.build_provider(cfg), None
    except Exception as exc:                            # pragma: no cover
        return None, _error_envelope("LLM_UNSUPPORTED", "模型层装配失败：%s" % exc)


# ---------------------------------------------------------------------------
# §3.11 · multipart/form-data 最小解析（Python 3.14 已移除 cgi，不引新依赖）
# ---------------------------------------------------------------------------

def multipart_boundary(content_type):
    """从 Content-Type 取 boundary；非 multipart 返回 None。"""
    if not content_type or "multipart/form-data" not in content_type.lower():
        return None
    for token in content_type.split(";")[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key.strip().lower() == "boundary":
            return value.strip().strip('"')
    return None


def _disposition_param(header, key):
    """从 `Content-Disposition` 头里取 name= / filename= 参数值。"""
    idx = header.lower().find(key + "=")
    if idx < 0:
        return None
    rest = header[idx + len(key) + 1:]
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end >= 0 else rest[1:]
    end = rest.find(";")
    return (rest if end < 0 else rest[:end]).strip() or None


def parse_multipart(body, boundary):
    """按 boundary 切分 multipart/form-data，返回 [{name, filename, data(bytes)}]。

    极简实现，只覆盖浏览器/curl 会发的常规形态：每个分段是若干头 + 空行 + 数据；
    数据里若恰好含 boundary 字节会被误切（常规上传不会发生，boundary 由客户端随机选）。
    """
    if not boundary:
        return []
    delimiter = b"--" + boundary.encode("latin-1", "replace")
    parts = []
    for chunk in body.split(delimiter)[1:]:
        if chunk.startswith(b"--"):                 # 结束分段
            break
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        elif chunk.endswith(b"\n"):
            chunk = chunk[:-1]
        sep = chunk.find(b"\r\n\r\n")
        skip = 4
        if sep < 0:
            sep = chunk.find(b"\n\n")
            skip = 2
        if sep < 0:
            continue
        header_block = chunk[:sep]
        data = chunk[sep + skip:]
        name = filename = None
        for line in header_block.split(b"\n"):
            text = line.strip().decode("latin-1")
            if not text:
                continue
            if text.lower().startswith("content-disposition:"):
                name = _disposition_param(text, "name")
                filename = _disposition_param(text, "filename")
        parts.append({"name": name, "filename": filename, "data": data})
    return parts


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION
    #: HTTP/1.1：普通响应带 Content-Length 可复用连接；SSE 帧不带长度、以关连接收尾。
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- 基础
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code, body, content_type, filename=None,
                    disposition="attachment"):
        """发送二进制内容（§3.11.4 下载）；带文件名时补 Content-Disposition。"""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename is not None:
            ascii_name = filename.encode("ascii", "replace").decode("ascii")
            ascii_name = ascii_name.replace('"', "_").replace("\\", "_")
            self.send_header(
                "Content-Disposition",
                '%s; filename="%s"; filename*=UTF-8\'\'%s'
                % (disposition, ascii_name, quote(filename)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _flush(self):
        """SSE 必须及时 flush：Windows 下 buffered writer 会把整条流攒住，
        前端一直收不到事件（表现为「连接建立了但没内容」）。"""
        try:
            self.wfile.flush()
        except AttributeError:                          # pragma: no cover
            pass

    def _runtime_or_error(self):
        """取运行时单例；缺 LangGraph 依赖时返回结构化错误（不抛出）。"""
        try:
            return _get_runtime(), None
        except Exception as exc:
            return None, _error_envelope(
                "RUNTIME_UNAVAILABLE", "运行时不可用（%s）" % exc)

    # ------------------------------------------------------------ 推送流(SSE)
    def serve_stream(self, conversation_id):
        """为某段对话持续输出 SSE，直到客户端断开。

        订阅来自 `Runtime.open_stream(cid)`（首帧是完整 `tools_snapshot`）；
        逐帧写 `event: …\\ndata: …\\n\\n` 并 flush。空闲期发注释心跳保活并探测断开。
        断开（写失败）即退出循环，`finally` 里 `sub.close()` 释放订阅。
        """
        runtime, err = self._runtime_or_error()
        if err is not None:
            return self._send(200, err)
        if runtime.get_conversation(conversation_id) is None:
            return self._send(200, _error_envelope(
                "CONVERSATION_NOT_FOUND", "对话不存在：%s" % conversation_id))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # Connection: close —— SSE 帧没有 Content-Length，以关连接界定消息体；
        # 同时把 close_connection 置真，连接断开后不再复用。
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")     # 让反向代理不缓冲
        self._cors()
        self.end_headers()
        self._flush()

        sub = runtime.open_stream(conversation_id)
        try:
            while True:
                event = sub.get(timeout=SSE_HEARTBEAT_SECONDS)
                if event is None:
                    if sub.closed:                      # 中枢关流 → 正常结束
                        break
                    self.wfile.write(b": keep-alive\n\n")
                    self._flush()
                    continue
                self.wfile.write(_sse_frame(event))
                self._flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                OSError, ValueError):
            pass                                        # 客户端断开：正常收尾
        finally:
            try:
                sub.close()
            except Exception:                           # pragma: no cover
                pass
            self.close_connection = True

    # ------------------------------------------------------------ 长任务视图(T6.2)
    def _task_view(self, runtime, conversation_id, agent_task_id):
        """取某长任务的接口文档 §3.8 视图（数据直接取阶段 4 的 task_view）。"""
        from .runtime.long_task import task_view       # 延迟导入：缺依赖时不炸只读端点
        record = runtime.long_tasks.get(conversation_id, agent_task_id)
        return record, (task_view(record) if record else None)

    # ------------------------------------------------------------ 资源上传(§3.11)
    def _post_upload(self, length):
        """`POST /api/schemes/upload`（multipart）。

        原始数据上传已下线：远端能力服务不对外提供上传，Agent 只做只读列举。
        """
        if length > resources.MAX_REQUEST_BYTES:
            return self._send(200, _error_envelope(
                "UPLOAD_TOO_LARGE", "上传体积超过上限 %d 字节"
                % resources.MAX_REQUEST_BYTES))
        body = self.rfile.read(length) if length else b""
        boundary = multipart_boundary(self.headers.get("Content-Type") or "")
        if not boundary:
            return self._send(200, _error_envelope(
                "USAGE", "上传端点需要 multipart/form-data 请求"))
        parts = parse_multipart(body, boundary)
        try:
            payload = resources.save_scheme(parts)
            ok = bool((payload.get("validation") or {}).get("ok"))
            # 校验不过时 ok=false，但**仍回传 saved**，便于用户据校验明细修正。
            return self._send(200, dict({"ok": ok}, **payload))
        except resources.ResourceError as exc:
            return self._send(200, exc.envelope())

    # ------------------------------------------------------------ 对话相关 GET
    def _get_conversations(self, parts, query):
        """`/api/conversations…` 的 GET 分派（接口文档 §3.2 / §3.6 / §3.8 / §3.9）。"""
        # GET /api/conversations —— 列出对话
        if len(parts) == 2:
            runtime, err = self._runtime_or_error()
            if err is not None:
                return self._send(200, err)
            convs = [conversation_view(c) for c in runtime.list_conversations()]
            return self._send(200, {"ok": True, "conversations": convs})

        cid = parts[2]

        # GET /api/conversations/{cid}/stream —— 打开推送流（SSE）
        if len(parts) == 4 and parts[3] == "stream":
            return self.serve_stream(cid)

        # GET /api/conversations/{cid}/tree —— 读整棵对话树（接口文档 §3.5）
        if len(parts) == 4 and parts[3] == "tree":
            runtime, err = self._runtime_or_error()
            if err is not None:
                return self._send(200, err)
            tree = runtime.read_tree(cid)
            if tree is None:
                return self._send(200, _error_envelope(
                    "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))
            return self._send(200, {
                "ok": True, "conversation_id": cid,
                "nodes": [node_view(n) for n in tree["nodes"]],
                "current_node_id": tree["current_node_id"],
                "current_branch": tree["current_branch"],
                "branches": [branch_view(b) for b in tree["branches"]]})

        # GET /api/conversations/{cid}/artifacts —— 恢复产物列表（接口文档 §3.12）
        if len(parts) == 4 and parts[3] == "artifacts":
            runtime, err = self._runtime_or_error()
            if err is not None:
                return self._send(200, err)
            conv = runtime.get_conversation(cid)
            if conv is None:
                return self._send(200, _error_envelope(
                    "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))
            artifacts = runtime.visible_artifacts(cid) or []
            return self._send(200, {"ok": True, "conversation_id": cid,
                                    "artifacts": artifacts})

        # GET /api/conversations/{cid}/messages —— 消息历史（接口文档 §3.13）
        # 刷新 / 切换对话后据节点表重建可见内容；按 seq 升序，content 为完整内容。
        if len(parts) == 4 and parts[3] == "messages":
            runtime, err = self._runtime_or_error()
            if err is not None:
                return self._send(200, err)
            if runtime.get_conversation(cid) is None:
                return self._send(200, _error_envelope(
                    "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))
            messages = [message_view(n) for n in runtime.store.nodes_of(cid)]
            return self._send(200, {"ok": True, "conversation_id": cid,
                                    "messages": messages})

        # GET /api/conversations/{cid}/nodes/{node_id} —— 读单节点（接口文档 §3.5）
        if len(parts) == 5 and parts[3] == "nodes":
            runtime, err = self._runtime_or_error()
            if err is not None:
                return self._send(200, err)
            if runtime.get_conversation(cid) is None:
                return self._send(200, _error_envelope(
                    "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))
            try:
                node_id = int(parts[4])
            except (TypeError, ValueError):
                return self._send(200, _error_envelope(
                    "USAGE", "节点编号应为整数：%r" % parts[4]))
            node = runtime.store.get_node(node_id)
            if node is None or node.get("conversation_id") != cid:
                return self._send(200, _error_envelope(
                    "NODE_NOT_FOUND", "节点不存在或不属于该对话：%s" % parts[4]))
            return self._send(200, {"ok": True, "conversation_id": cid,
                                    "node": node_view(node, full=True)})

        # 其余长任务端点都要求对话存在（失败如实报错，不落节点）
        if len(parts) >= 4 and parts[3] == "tasks":
            runtime, err = self._runtime_or_error()
            if err is not None:
                return self._send(200, err)
            if runtime.get_conversation(cid) is None:
                return self._send(200, _error_envelope(
                    "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))
            return self._get_tasks(runtime, cid, parts[4:])

        return self._send(404, _error_envelope(
            "USAGE", "未知路径：%s" % self.path))

    def _get_tasks(self, runtime, cid, tail):
        """长任务查询（接口文档 §3.8 / §3.9）：列 / 取单个 / 取结构化结果。"""
        from .runtime.long_task import result_payload, task_view

        # GET …/tasks —— 列该对话的长任务
        if not tail:
            tasks = [task_view(t) for t in runtime.long_tasks.tasks_of(cid)]
            return self._send(200, {"ok": True, "conversation_id": cid,
                                    "tasks": tasks})

        agent_task_id = tail[0]
        record = runtime.long_tasks.get(cid, agent_task_id)
        if record is None:
            return self._send(200, _error_envelope(
                "TASK_NOT_FOUND", "任务不存在：%s" % agent_task_id))

        # GET …/tasks/{aid} —— 单个任务视图
        if len(tail) == 1:
            return self._send(200, {"ok": True, "task": task_view(record)})

        # GET …/tasks/{aid}/result —— 结构化结果（接口文档 §3.9）
        if len(tail) == 2 and tail[1] == "result":
            if record.get("result") is None:
                return self._send(200, _error_envelope(
                    "TASK_NOT_FINISHED",
                    "任务尚未产出结果（当前 %s）"
                    % (record.get("status") or "queued")))
            return self._send(200, result_payload(record, record.get("result")))

        return self._send(404, _error_envelope(
            "USAGE", "未知路径：%s" % self.path))

    # ------------------------------------------------------------ 对话相关 POST
    def _post_conversations(self, parts, body):
        """`/api/conversations…` 的 POST 分派（接口文档 §3.2 / §3.3 / §3.4）。"""
        runtime, err = self._runtime_or_error()
        if err is not None:
            return self._send(200, err)

        # POST /api/conversations —— 新建对话
        if len(parts) == 2:
            title = body.get("title")
            cid = runtime.create_conversation(title=title)
            conv = runtime.get_conversation(cid)
            return self._send(200, {"ok": True, "conversation": conversation_view(conv)})

        cid = parts[2]
        # 校验对话存在（新建之后的所有 POST 都要求它存在）
        if runtime.get_conversation(cid) is None:
            return self._send(200, _error_envelope(
                "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))

        # POST /api/conversations/{cid}/branches —— 从某节点另起一条支（接口文档 §3.5）
        if len(parts) == 4 and parts[3] == "branches":
            from_node_id = body.get("from_node_id")
            if from_node_id is None:
                # 缺省：从当前所在节点另起一条支
                from_node_id = runtime.get_conversation(cid).get("current_node_id")
            if from_node_id is None:
                return self._send(200, _error_envelope(
                    "USAGE", "该对话还没有节点，无法分支（请先发消息）"))
            try:
                from_node_id = int(from_node_id)
            except (TypeError, ValueError):
                return self._send(200, _error_envelope(
                    "USAGE", "from_node_id 应为整数：%r" % body.get("from_node_id")))
            result = runtime.branch_from(cid, from_node_id)
            if not result.get("ok"):
                return self._send(200, _error_envelope(
                    result["error"]["code"], result["error"]["message"]))
            conv = runtime.get_conversation(cid)
            return self._send(200, {
                "ok": True, "conversation_id": cid,
                "branch": branch_view(result["branch"]),
                "from_node_id": result["current_node_id"],
                "current_node_id": result["current_node_id"],
                "conversation": conversation_view(conv)})

        # POST /api/conversations/{cid}/context —— 设置上下文
        if len(parts) == 4 and parts[3] == "context":
            runtime.set_context(cid, task_id=body.get("task_id"),
                                data_version=body.get("data_version"))
            conv = runtime.get_conversation(cid)
            return self._send(200, {"ok": True, "conversation_id": cid,
                                    "context": conv.get("context") or {}})

        # POST /api/conversations/{cid}/messages —— 发消息（按 mode 分派）
        if len(parts) == 4 and parts[3] == "messages":
            return self._post_message(runtime, cid, body)

        return self._send(404, _error_envelope(
            "USAGE", "未知路径：%s" % self.path))

    def _post_message(self, runtime, cid, body):
        """发消息：`offline` → run_offline；`llm` → run_turn；两者同形信封。"""
        mode = (body.get("mode") or "").strip().lower()
        if mode not in ("offline", "llm"):
            return self._send(200, _error_envelope(
                "USAGE", "mode 必须是 offline 或 llm（收到：%r）" % body.get("mode")))

        # 本次上下文覆盖（写入该对话上下文，同 §3.4）
        override = body.get("context")
        if isinstance(override, dict):
            runtime.set_context(cid, task_id=override.get("task_id"),
                                data_version=override.get("data_version"))

        if mode == "offline":
            tool = body.get("tool")
            if not tool or not isinstance(tool, str):
                return self._send(200, _error_envelope(
                    "USAGE", "offline 模式必须提供工具名 tool"))
            params = body.get("params")
            if params is not None and not isinstance(params, dict):
                return self._send(200, _error_envelope(
                    "USAGE", "params 必须是对象"))
            result = runtime.run_offline(cid, tool, params or {})
        else:
            text = body.get("text")
            if not text or not str(text).strip():
                return self._send(200, _error_envelope(
                    "USAGE", "llm 模式必须提供文本 text"))
            provider, perr = _provider_from_body(body)
            if perr is not None:
                return self._send(200, perr)
            options = None
            effort = _reasoning_effort_option(body.get("reasoning_effort"))
            if effort is not None:
                options = {"reasoning_effort": effort}
            result = runtime.run_turn(cid, text, provider=provider,
                                      options=options)
        return self._send(200, result)

    def handle_one_request(self):
        """兜底：任何未捕获异常都返回 JSON 错误，**绝不返回空响应**。

        BaseHTTPRequestHandler 的默认行为是异常后直接断连，客户端拿到的是空响应，
        连一句错误提示都没有 —— 2026-09-29 实测：请求体编码不对时，
        服务端抛 UnicodeDecodeError，curl 那边只显示"啥都没有"，极难排障。
        """
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except Exception as exc:
            sys.stderr.write("[error] 处理请求时出错：%r\n" % (exc,))
            try:
                self._send(500, {"ok": False, "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务端处理出错：%s" % exc}})
            except Exception:
                pass                      # 已经发过响应头了，只能断连
            self.close_connection = True

    def log_message(self, fmt, *args):      # 日志走 stderr，不污染 stdout
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _delete_conversation(self, cid):
        """`DELETE /api/conversations/{cid}`（接口文档 §3.2）：删掉整段对话。

        成功 → `{"ok": true, "conversation_id": …}`；不存在 → `CONVERSATION_NOT_FOUND`
        （与实际删到的行数一致，重复删除如实报不存在）。
        """
        runtime, err = self._runtime_or_error()
        if err is not None:
            return self._send(200, err)
        if not runtime.delete_conversation(cid):
            return self._send(200, _error_envelope(
                "CONVERSATION_NOT_FOUND", "对话不存在：%s" % cid))
        return self._send(200, {"ok": True, "conversation_id": cid})

    def do_DELETE(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if parts[:2] == ["api", "conversations"] and len(parts) == 3:
            return self._delete_conversation(parts[2])
        return self._send(404, _error_envelope(
            "USAGE", "未知路径：%s" % u.path))

    # ---------------------------------------------------------------- 路由
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]

        if u.path == "/health":
            cfg = _load_llm_config({})
            try:
                rows = _tool_sources_table()
                tool_info = {"count": len(rows), "sources": _tool_sources(rows),
                             "available": True}
            except Exception:                           # pragma: no cover
                tool_info = {"count": 0, "sources": [], "available": False}
            return self._send(200, {
                "ok": True,
                "service": SERVER_VERSION,
                "scope_default": tools.DEFAULT_SCOPE,
                # tools：可用工具来源（接口文档 §3.1）。当前只会是 local。
                "tools": tool_info,
                # supported：后端总开关（AGENT_LLM_SUPPORTED）有没有开 —— **默认关**。
                # configured：当前有没有真的配齐（enabled + base + key + model）。
                "llm": {"supported": bool(cfg is not None and cfg.supported),
                        "configured": bool(cfg is not None and cfg.usable)},
            })

        # -------- 阶段 6 契约：工具表只读兜底（接口文档 §3.7）--------
        if u.path == "/api/tools":
            try:
                return self._send(200, {"ok": True, "tools": _tool_sources_table()})
            except Exception as exc:                    # pragma: no cover
                return self._send(200, _error_envelope(
                    "RUNTIME_UNAVAILABLE", "工具表不可构建：%s" % exc))

        # -------- 阶段 6 契约：对话管理 / 推送流 / 长任务（接口文档 §3.2/§3.6/§3.8/§3.9）
        if parts[:2] == ["api", "conversations"]:
            return self._get_conversations(parts, q)

        # -------- §3.11 资源与文件接口（直通，不经 LLM）--------
        return self._get_resources(parts, u, q)

    def _get_resources(self, parts, u, q):
        """`§3.11` 资源端点的 GET 分派（原始数据 / 已发布 / 方案 / 文件）。

        这些端点改由远端能力服务提供（`Runtime.mcp_client`），本层只做字段映射，
        出口形状与前端既有契约一致；远端失败如实回结构化错误信封。
        """
        def one(key):
            values = q.get(key) or []
            return values[0] if values else None

        if u.path == "/api/data/raw":
            return self._resource_call(resources.list_raw)

        if u.path == "/api/data/published":
            return self._resource_call(resources.list_published)

        # GET /api/schemes —— 列已登记方案（远端 `list_schemes`；宽松形状）
        if u.path == "/api/schemes":
            return self._resource_call(resources.list_schemes)

        if u.path == "/api/schemes/content":
            return self._resource_call(resources.scheme_content, one("name"))

        if u.path == "/api/files/preview":
            return self._resource_call(resources.preview_file,
                                       one("task_id"), one("path"))

        # GET /api/files/download —— 产物下载：带口令经远端只读路由**流式**中转
        if u.path == "/api/files/download":
            from . import mcp_download
            return self._relay_download(
                lambda config: mcp_download.open_task_stream(
                    config, one("task_id"), one("path")))

        # GET /api/data/published/download —— 已发布数据下载：同样流式中转
        if u.path == "/api/data/published/download":
            from . import mcp_download
            return self._relay_download(
                lambda config: mcp_download.open_published_stream(
                    config, one("data_version"), one("table")))

        # GET /api/tasks/{tid}/files —— 列任务产出文件（§3.11.4；tid 为 hadoop_task_id）
        if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "files":
            return self._resource_call(resources.list_task_files, parts[2])

        return self._get_legacy(parts, u, q)

    def _resource_call(self, func, *args):
        """调用资源函数（首参为远端客户端）并把结果包成 `{"ok": true, ...}`。

        客户端取自运行时（`Runtime.mcp_client`）；运行时不可用或远端失败时，
        如实回结构化错误信封（`ResourceError`），**不造空成功、不编数字**。
        """
        try:
            runtime, err = self._runtime_or_error()
            if err is not None:
                raise resources.ResourceError(
                    err["error"]["code"], err["error"]["message"])
            client = getattr(runtime, "mcp_client", None)
            payload = func(client, *args)
        except resources.ResourceError as exc:
            return self._send(200, exc.envelope())
        return self._send(200, dict({"ok": True}, **payload))

    def _relay_download(self, opener):
        """带口令从远端取回并**流式**转给浏览器（不把整份文件读进内存）。

        浏览器不持有口令：命中 Agent 的下载 URL，由 Agent 用运行时客户端的配置
        （`Runtime.mcp_client.config`）带 `Authorization: Bearer <口令>` 去远端
        取流，再原样转发。未接入 / 未配置（如 `NullMcpClient`）如实回
        `MCP_UNAVAILABLE`；远端结构化错误按同一套映射转成 HTTP 状态 + 错误信封。
        """
        from . import mcp_download

        runtime, err = self._runtime_or_error()
        config = None
        if err is None:
            client = getattr(runtime, "mcp_client", None)
            config = getattr(client, "config", None)
        iterator, headers, error = opener(config)
        if error is not None:
            return self._send(mcp_download.status_for_code(error.get("code")),
                              {"ok": False, "error": error})
        return self._stream_response(iterator, headers)

    def _stream_response(self, iterator, headers):
        """写状态行 + 远端透传的下载头，然后分块转发响应体。"""
        disposition = headers.get("Content-Disposition")
        length = headers.get("Content-Length")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        if length:
            self.send_header("Content-Length", str(length))
        else:
            # 远端没给长度：用关连接界定消息体（HTTP/1.1 合法），不臆造 Content-Length。
            self.send_header("Connection", "close")
            self.close_connection = True
        self._cors()
        self.end_headers()
        try:
            for chunk in iterator:
                self.wfile.write(chunk)
            self._flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                OSError, ValueError):
            pass                                    # 浏览器断开：正常收尾
        finally:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()                          # 触发远端响应/客户端关闭
                except Exception:                    # pragma: no cover
                    pass

    def _get_legacy(self, parts, u, q):
        """既有 driver 直取端点的 GET 分派（/api/schemes、/api/tasks…）。"""
        if parts[:2] == ["api", "schemes"]:
            return self._send(200, tools.list_schemes())

        if parts[:2] == ["api", "tasks"] and len(parts) == 2:
            return self._send(200, tools.list_tasks())

        if len(parts) >= 4 and parts[:2] == ["api", "tasks"]:
            tid = parts[2]
            what = parts[3]
            if what == "status":
                return self._send(200, tools.get_task_status(tid))
            if what == "result":
                env = tools.get_task_result(tid)
                if q.get("explain", ["0"])[0] in ("1", "true", "yes"):
                    env["_explanation"] = explain.explain_result(env)
                return self._send(200, env)
            if what == "samples":
                return self._send(200, tools.get_samples(
                    tid,
                    q.get("type", ["cleaned"])[0],
                    q.get("table", ["movies"])[0],
                    int(q.get("n", ["5"])[0])))
            if what == "report":
                fmt = q.get("format", ["md"])[0]
                env = tools.get_report(tid, fmt)
                if env.get("ok") and fmt == "md":
                    return self._send_text(200, env.get("report", ""))
                return self._send(200, env)

        return self._send(404, {"ok": False, "error": {
            "code": "USAGE", "message": "未知路径：%s" % u.path}})

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)

        # -------- §3.11 上传端点：multipart/form-data（不能按 JSON 解析）--------
        if u.path == "/api/schemes/upload":
            return self._post_upload(length)

        raw = _decode_body(self.rfile.read(length)) if length else "{}"
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return self._send(400, {"ok": False, "error": {
                "code": "USAGE", "message": "请求体不是合法 JSON"}})

        # -------- 阶段 6 契约：对话管理 / 发消息 / 上下文（接口文档 §3.2/§3.3/§3.4）
        parts = [p for p in u.path.split("/") if p]
        if parts[:2] == ["api", "conversations"]:
            return self._post_conversations(parts, body)

        if u.path == "/api/tasks":
            opts = _normalize_opts(body)
            env = tools.start_cleaning_task(
                rules=opts.get("rules"),
                scoring=opts.get("scoring"),
                data_version=opts.get("data_version"),
                tag=opts.get("tag"),
                foreground=bool(body.get("foreground", False)),
                force=bool(body.get("force", False)),
                exec_mode=opts.get("exec_mode"),
                scope=opts.get("scope"))
            # 口径快照改由对话持久化承接：带了 conversation_id 才记（阶段 3a）。
            conversation_id = body.get("conversation_id")
            if (conversation_id and isinstance(env, dict) and env.get("ok")
                    and env.get("task_id")):
                try:
                    _get_runtime().remember_opts(conversation_id, opts)
                except Exception:                            # pragma: no cover
                    pass
            return self._send(200, env)

        if u.path == "/api/chat":
            text = body.get("text", "")

            # 本次显式指定的口径（前端可以给全，也可以一个都不给）
            explicit = _normalize_opts(body)

            cfg = _load_llm_config(body)
            # 自然语言入口只走 LLM 主控运行时：模型不可用/未配置时**结构化报错**，
            # 绝不静默回退到已删除的纯规则解析（既定决策）。
            if cfg is not None and getattr(cfg, "usable", False) \
                    and getattr(cfg, "mode", "on") != "off":
                conversation_id = body.get("conversation_id")
                r = _run_runtime_chat(text, explicit, conversation_id,
                                      conversation_id_created=not conversation_id)
            else:
                code, message = _llm_unavailable(cfg)
                r = _llm_error_envelope(explicit, code, message)
            return self._send(200, r)

        if u.path == "/api/llm/test":
            try:
                from . import llm_client
            except Exception:
                return self._send(200, {"ok": False, "reachable": False, "error": {
                    "code": "LLM_UNSUPPORTED",
                    "message": "当前后端未启用大模型可选层"}})
            # R19：兼容两种请求体 —— {"llm": {...}}（chat 路径）与平铺 cfg
            # （前端 llm-settings.js 的 testLLM 把配置直接放 body 顶层）。
            cfg = _load_llm_config(body if isinstance(body.get("llm"), dict) else {"llm": body})
            if cfg is None:
                return self._send(200, {"ok": False, "reachable": False, "error": {
                    "code": "LLM_UNSUPPORTED", "message": "当前后端未启用大模型可选层"}})
            if not cfg.supported:
                return self._send(200, {"ok": False, "reachable": False,
                                        "config": cfg.safe_dict(),
                                        "error": {
                                            "code": "LLM_DISABLED",
                                            "message": "后端总开关未开：后端启动前需设置 AGENT_LLM_SUPPORTED=1"}})
            if not cfg.usable:
                return self._send(200, {"ok": False, "reachable": False,
                                        "config": cfg.safe_dict(),
                                        "error": {"code": "LLM_NOT_CONFIGURED",
                                                  "message": "缺少 api_base / api_key / model"}})
            t0 = time.time()
            # L2：不要写死 max_tokens=8 —— 推理型模型会把前几个 token 全花在推理上，
            # 8 个被吞完 → content 为空 → 测试永远失败（配置明明是对的）。用配置值。
            got, err = llm_client.chat(cfg, "你只需回复 OK 两个字。", "ping",
                                       max_tokens=cfg.max_tokens)
            return self._send(200, {
                "ok": got is not None,
                "reachable": got is not None,
                "latency_ms": int((time.time() - t0) * 1000),
                "config": cfg.safe_dict(),      # 脱敏：不含 api_key
                "error": None if got else {"code": "LLM_UNREACHABLE",
                                           "message": err or "调用失败"},
            })

        if u.path == "/api/validate":
            return self._send(200, tools.validate_config(
                body.get("rules"), body.get("scoring")))

        return self._send(404, {"ok": False, "error": {
            "code": "USAGE", "message": "未知路径：%s" % u.path}})


class RuntimeServer(ThreadingHTTPServer):
    """带运行时单例的 HTTP 服务。

    契约端点（对话 / 推送流 / 长任务）要求同一个运行时实例跨请求共享；测试与联调可
    注入自建运行时（假模型 / 假工具后端），生产走 `_get_runtime()` 的进程单例。
    daemon_threads=True：SSE 长连接不会拖住进程退出。
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, runtime=None):
        global _RUNTIME
        if runtime is not None:
            _RUNTIME = runtime
        ThreadingHTTPServer.__init__(self, address, Handler)


def create_server(host="127.0.0.1", port=0, runtime=None):
    """建（并绑定）一个 Agent HTTP 服务；port=0 时由系统分配端口。

    返回 `RuntimeServer`，调用方自行 `serve_forever()` / `shutdown()`。
    测试与联调据此注入运行时，无需触碰全局。
    """
    return RuntimeServer((host, port), runtime=runtime)


def main(argv=None):
    p = argparse.ArgumentParser(prog="agent.http_api", description="Agent HTTP 接口")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args(argv)

    srv = create_server(args.host, args.port)
    sys.stderr.write("Agent HTTP API 已启动：http://%s:%d\n" % (args.host, args.port))
    sys.stderr.write("健康检查：/health ｜ 对话：/api/conversations ｜ "
                     "工具表：/api/tools ｜ 方案：/api/schemes\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n已停止\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
