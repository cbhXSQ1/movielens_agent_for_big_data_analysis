#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hadoop/tools/opencode_proxy.py —— 本地 OpenAI 兼容代理 → opencode Zen/Go 网关。

为什么需要它：
  agent 的 llm_client 是标准 OpenAI 兼容客户端（只见 /v1/chat/completions），
  而 opencode go 网关（https://opencode.ai/zen/go/v1）要求每个请求带
  `x-opencode-session` 头（会话路由）。本代理在中间补齐协议差异：

    agent (LLM 层) ──▶ 127.0.0.1:8901/v1/chat/completions
                              │ 附加 x-opencode-session（会话 ID 适配）
                              │        + Authorization: Bearer <key>
                              ▼
                    https://opencode.ai/zen/go/v1/chat/completions

会话 ID 适配（转接头，不渗进 agent 循环）：
  * agent 请求带 `x-agent-session: <会话号/任务号>` → 代理映射到**稳定**的
    网关会话 UUID：同一键的多轮请求打在同一个网关会话上（多轮延续）；
    映射带 TTL 与容量上限，超出淘汰最久未用键。
  * 不带该头（旧调用方 / /api/llm/test 等无状态请求）→ 每次随机 UUID，向后兼容。

用法：
  python3 hadoop/tools/opencode_proxy.py [--port 8901]
      [--api-key <key>]               # 默认读环境变量 OPENCODE_GO_API_KEY
      [--upstream https://opencode.ai/zen/go/v1]
      [--model-id deepseek-v4-flash]  # /v1/models 返回的模型 id（仅展示用）
      [--session-header on|off]       # 默认 on（opencode-go 专属头）；
                                      # upstream 是标准 OpenAI 兼容端点
                                      # （DeepSeek 官方 / icspa 等）时用 off

配合 config/llm.settings.json：
  "api_base": "http://127.0.0.1:8901/v1",
  "api_key":  "anything"        # 代理会替换成真实 key，这里只是占位
  "model":    "deepseek-v4-flash"
"""
import json
import os
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8901
DEFAULT_UPSTREAM = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
SESSION_TTL_SECONDS = 24 * 3600      # 会话键映射的有效期（滚动续期）
MAX_SESSIONS = 1024                  # 上限；超出后淘汰最久未用的键


def _key_from_args(arg):
    return arg or os.environ.get("OPENCODE_GO_API_KEY") or ""


def parse_proxy_args(argv):
    """解析代理命令行参数，返回 dict；未知参数返回 None（调用方报用法错误）。

    独立成函数以便单测（main 会阻塞起服务，不可测）。
    """
    out = {"port": DEFAULT_PORT, "api_key": None,
           "upstream": DEFAULT_UPSTREAM, "model_id": DEFAULT_MODEL,
           "session_header": True}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--port" and i + 1 < len(argv):
            out["port"] = int(argv[i + 1]); i += 2
        elif a == "--api-key" and i + 1 < len(argv):
            out["api_key"] = argv[i + 1]; i += 2
        elif a == "--upstream" and i + 1 < len(argv):
            out["upstream"] = argv[i + 1]; i += 2
        elif a == "--model-id" and i + 1 < len(argv):
            out["model_id"] = argv[i + 1]; i += 2
        elif a == "--session-header" and i + 1 < len(argv):
            # x-opencode-session 是 opencode-go 网关专属头：upstream 是标准
            # OpenAI 兼容端点（DeepSeek 官方 / icspa 等）时用 off 关掉，
            # 不给别人的网关发别的网关的专属头。
            out["session_header"] = argv[i + 1].strip().lower() not in ("0", "off", "false", "no")
            i += 2
        else:
            return None
    return out


def get_or_create_session(session_map, agent_key, ttl=SESSION_TTL_SECONDS,
                          max_sessions=MAX_SESSIONS):
    """转接头核心：agent 会话键 → 网关会话 UUID（一对一稳定映射）。

    agent（v2 循环）每次请求带 `x-agent-session: <自己的会话号/任务号>`，
    同一个键永远映射到**同一个** x-opencode-session —— 多轮延续在网关侧成立。
    不传键（旧调用方 / 测试连接等无状态请求）由调用方用随机 UUID，向后兼容。

    返回值：网关会话 UUID 字符串。
    说明：此映射只解决「会话 ID 适配」，不把网关细节渗进 agent 循环
    （docs/agent/llm-agent-plan.md §2.1 第 2 条：转接头固定在代理侧）。
    """
    now = time.time()
    hit = session_map.get(agent_key)
    if hit and now - hit[1] < ttl:
        session_map[agent_key] = (hit[0], now)        # 滚动续期
        return hit[0]
    session_id = str(uuid.uuid4())
    session_map[agent_key] = (session_id, now)
    if len(session_map) > max_sessions:               # 简单淘汰：最久未用
        oldest = min(session_map, key=lambda k: session_map[k][1])
        session_map.pop(oldest, None)
    return session_id


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
        # 会话 ID 适配：仅当 upstream 是 opencode-go（--session-header 默认 on）
        # 且 agent 带了会话键时才发 x-opencode-session（映射到稳定网关会话）；
        # on + 无键 → 随机 UUID（接受级兼容）；off（标准 OpenAI 兼容 upstream，
        # 如 DeepSeek 官方 / icspa）→ 整头不发，保持纯标准协议。
        agent_key = (self.headers.get("x-agent-session") or "").strip()
        session_id = None
        if self.server.session_header:
            session_id = (get_or_create_session(self.server.sessions, agent_key)
                          if agent_key else str(uuid.uuid4()))
        # 用 curl 而非 urllib 转发：opencode.ai 在 Cloudflare 后面，按 TLS 指纹
        # 封禁 python-urllib（实测 403 error 1010），curl 指纹可正常通过。
        # 超时用 --max-time=60；HTTP 状态码经 -w 写到 stderr 取回。
        cmd = ["curl", "-sS", "--max-time", "60", "-X", "POST",
               self.server.upstream + "/chat/completions",
               "-H", "Authorization: Bearer " + self.server.api_key,
               "-H", "Content-Type: application/json"]
        if session_id is not None:
            cmd += ["-H", "x-opencode-session: " + session_id]
        cmd += ["--data-binary", "@-", "-w", "\n__HTTP__%{http_code}"]
        proc = subprocess.run(cmd, input=body, capture_output=True)
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
    conf = parse_proxy_args(argv)
    if conf is None:
        sys.stderr.write("未知参数（可用：--port / --api-key / --upstream / "
                         "--model-id / --session-header on|off）\n")
        return 2
    key = _key_from_args(conf["api_key"])
    if not key:
        sys.stderr.write("缺少 API key：--api-key 或环境变量 OPENCODE_GO_API_KEY\n")
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", conf["port"]), Handler)
    srv.api_key = key
    srv.upstream = conf["upstream"]
    srv.model_id = conf["model_id"]
    srv.session_header = conf["session_header"]
    srv.sessions = {}                 # agent 会话键 → (网关会话 UUID, 最后使用时间)
    sys.stderr.write("opencode-proxy 已启动：127.0.0.1:%d → %s（model %s，"
                     "x-opencode-session %s）\n"
                     % (conf["port"], conf["upstream"], conf["model_id"],
                        "on" if conf["session_header"] else "off"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:                     # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())