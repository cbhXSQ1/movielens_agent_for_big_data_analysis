#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hadoop/tools/opencode_proxy.py —— 本地 OpenAI 兼容代理 → opencode Zen/Go 网关。

为什么需要它：
  agent 的 llm_client 是标准 OpenAI 兼容客户端（只见 /v1/chat/completions），
  而 opencode go 网关（https://opencode.ai/zen/go/v1）要求每个请求带
  `x-opencode-session` 头（会话路由）。本代理在中间补齐协议差异：

    agent (LLM 层) ──▶ 127.0.0.1:8901/v1/chat/completions
                              │ 自动附加 x-opencode-session（随机 UUID）
                              │        + Authorization: Bearer <key>
                              ▼
                    https://opencode.ai/zen/go/v1/chat/completions

用法：
  python3 hadoop/tools/opencode_proxy.py [--port 8901]
      [--api-key <key>]               # 默认读环境变量 OPENCODE_GO_API_KEY
      [--upstream https://opencode.ai/zen/go/v1]
      [--model-id deepseek-v4-flash]  # /v1/models 返回的模型 id（仅展示用）

配合 config/llm.settings.json：
  "api_base": "http://127.0.0.1:8901/v1",
  "api_key":  "anything"        # 代理会替换成真实 key，这里只是占位
  "model":    "deepseek-v4-flash"
"""
import json
import os
import subprocess
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8901
DEFAULT_UPSTREAM = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"


def _key_from_args(arg):
    return arg or os.environ.get("OPENCODE_GO_API_KEY") or ""


class Handler(BaseHTTPRequestHandler):
    server_version = "opencode-proxy/1.0"
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):                       # CORS 预检
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/health"):
            return self._send(200, json.dumps({
                "ok": True, "service": "opencode-proxy/1.0",
                "upstream": self.server.upstream,
                "model": self.server.model_id,
            }))
        if self.path == "/v1/models":
            return self._send(200, json.dumps({
                "object": "list",
                "data": [{"id": self.server.model_id, "object": "model"}],
            }))
        return self._send(404, json.dumps({"error": {"message": "未知路径：%s" % self.path}}))

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            return self._send(404, json.dumps({"error": {"message": "未知路径：%s" % self.path}}))
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        # 用 curl 而非 urllib 转发：opencode.ai 在 Cloudflare 后面，按 TLS 指纹
        # 封禁 python-urllib（实测 403 error 1010），curl 指纹可正常通过。
        # 超时用 --max-time=60；HTTP 状态码经 -w 写到 stderr 取回。
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "60", "-X", "POST",
             self.server.upstream + "/chat/completions",
             "-H", "Authorization: Bearer " + self.server.api_key,
             "-H", "x-opencode-session: " + str(uuid.uuid4()),
             "-H", "Content-Type: application/json",
             "--data-binary", "@-",
             "-w", "\n__HTTP__%{http_code}"],
            input=body, capture_output=True)
        out = proc.stdout
        marker = b"\n__HTTP__"
        code = 502
        if marker in out:
            out, tail = out.rsplit(marker, 1)
            try:
                code = int(tail.strip())
            except ValueError:
                pass
        if not out.strip():
            out = json.dumps({"error": {"message": (proc.stderr or b"").decode("utf-8", "replace")}}).encode("utf-8")
            if code == 0:
                code = 502
        self._send(code, out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    port, key, upstream, model = DEFAULT_PORT, None, DEFAULT_UPSTREAM, DEFAULT_MODEL
    i = 0
    while i < len(argv):
        if argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2
        elif argv[i] == "--api-key" and i + 1 < len(argv):
            key = argv[i + 1]; i += 2
        elif argv[i] == "--upstream" and i + 1 < len(argv):
            upstream = argv[i + 1]; i += 2
        elif argv[i] == "--model-id" and i + 1 < len(argv):
            model = argv[i + 1]; i += 2
        else:
            sys.stderr.write("未知参数：%s\n" % argv[i]); return 2
    key = _key_from_args(key)
    if not key:
        sys.stderr.write("缺少 API key：--api-key 或环境变量 OPENCODE_GO_API_KEY\n")
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.api_key, srv.upstream, srv.model_id = key, upstream, model
    sys.stderr.write("opencode-proxy 已启动：127.0.0.1:%d → %s（model %s）\n"
                     % (port, upstream, model))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:                     # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())