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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import agent as agent_mod
from . import explain, tools

SERVER_VERSION = "agent-http-api/1.0"


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
            return self._send(200, {"ok": True, "service": SERVER_VERSION})

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
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return self._send(400, {"ok": False, "error": {
                "code": "USAGE", "message": "请求体不是合法 JSON"}})

        if u.path == "/api/tasks":
            env = tools.start_cleaning_task(
                rules=body.get("rules"),
                scoring=body.get("scoring"),
                data_version=body.get("data_version"),
                tag=body.get("tag"),
                foreground=bool(body.get("foreground", False)),
                force=bool(body.get("force", False)),
                exec_mode=body.get("exec_mode"),
            )
            return self._send(200, env)

        if u.path == "/api/chat":
            text = body.get("text", "")
            ctx = {"task_id": body.get("task_id")} if body.get("task_id") else None
            r = agent_mod.respond(text, context=ctx,
                                  auto_start=bool(body.get("auto_start", True)),
                                  exec_mode=body.get("exec_mode"))
            return self._send(200, r)

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
