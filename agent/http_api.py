# -*- coding: utf-8 -*-
"""第 5 节 Agent：零依赖 HTTP 接口（给第 6 节前端联调用）。

只用 Python 标准库，不需要 pip 装任何东西。

启动：
    python3 -m agent.http_api --port 8765

已加 CORS 头，本地前端可直接跨域调。
所有响应都是 driver 的原始 JSON 信封（外加 `_http` 说明），
失败时如实返回错误码与原因，绝不造数。
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import agent as agent_mod
from . import explain, session, tools

SERVER_VERSION = "agent-http-api/1.1"

#: /api/chat 允许透传的运行口径参数（其余字段一律丢弃，防脏参数透传给 driver）
OPTION_FIELDS = ("rules", "scoring", "data_version", "tag", "exec_mode", "scope")


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
    """加载大模型配置。阶段 2 才有 llm_config 模块，没有就返回 None（= 纯规则）。"""
    try:
        from . import llm_config
    except Exception:
        return None
    raw = body.get("llm") if isinstance(body.get("llm"), dict) else None
    return llm_config.load(raw)


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION

    # ---------------------------------------------------------------- 基础
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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

    # ---------------------------------------------------------------- 路由
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]

        if u.path == "/health":
            cfg = _load_llm_config({})
            return self._send(200, {
                "ok": True,
                "service": SERVER_VERSION,
                "scope_default": tools.DEFAULT_SCOPE,
                # supported：后端总开关（AGENT_LLM_SUPPORTED）有没有开 —— **默认关**。
                #   关着 ⇒ 整层不生效（请求一律走规则解析，不会发给大模型）。
                #   前端入口是**常显**的，false 时点开只显示提示（见 docs/agent/前端对接接口.md §9.2）。
                # configured：当前有没有真的配齐（enabled + base + key + model）。
                "llm": {"supported": bool(cfg is not None and cfg.supported),
                        "configured": bool(cfg is not None and cfg.usable)},
            })

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
        raw = _decode_body(self.rfile.read(length)) if length else "{}"
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return self._send(400, {"ok": False, "error": {
                "code": "USAGE", "message": "请求体不是合法 JSON"}})

        if u.path == "/api/tasks":
            opts = session.normalize(
                dict((k, body.get(k)) for k in OPTION_FIELDS))
            env = tools.start_cleaning_task(
                rules=opts.get("rules"),
                scoring=opts.get("scoring"),
                data_version=opts.get("data_version"),
                tag=opts.get("tag"),
                foreground=bool(body.get("foreground", False)),
                force=bool(body.get("force", False)),
                exec_mode=opts.get("exec_mode"),
                scope=opts.get("scope"))
            if isinstance(env, dict) and env.get("ok") and env.get("task_id"):
                session.remember(env["task_id"], opts)
            return self._send(200, env)

        if u.path == "/api/chat":
            text = body.get("text", "")

            # 1) 本次显式指定的口径（前端可以给全，也可以一个都不给）
            explicit = session.normalize(
                dict((k, body.get(k)) for k in OPTION_FIELDS))

            # 2) 会话级配置快照：本次带了 task_id 就按它查，否则用最近一次。
            #    这样「追问」即使一个参数都不传，也能沿用第一轮选定的口径。
            tid_hint = body.get("task_id") or session.last_task_id()
            opts = session.merge(session.recall(tid_hint), explicit)

            ctx = {"task_id": body.get("task_id")} if body.get("task_id") else None

            r = agent_mod.respond(text, context=ctx,
                                  auto_start=bool(body.get("auto_start", True)),
                                  task_opts=opts,
                                  llm_cfg=_load_llm_config(body))

            # 3) 只有「真的发起了任务」才把这次的口径记进快照
            if isinstance(r, dict) and r.get("task_started") and r.get("task_id"):
                session.remember(r["task_id"], r.get("opts") or opts)
            return self._send(200, r)

        if u.path == "/api/llm/test":
            try:
                from . import llm_client
            except Exception:
                return self._send(200, {"ok": False, "reachable": False, "error": {
                    "code": "LLM_UNSUPPORTED",
                    "message": "当前后端未启用大模型可选层"}})
            cfg = _load_llm_config(body)
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
            got, err = llm_client.chat(cfg, "你只需回复 OK 两个字。", "ping", max_tokens=8)
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="agent.http_api", description="Agent HTTP 接口")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args(argv)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write("Agent HTTP API 已启动：http://%s:%d\n" % (args.host, args.port))
    sys.stderr.write("健康检查：/health ｜ 任务：/api/tasks ｜ 方案：/api/schemes\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\n已停止\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
