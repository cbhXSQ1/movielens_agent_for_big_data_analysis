# -*- coding: utf-8 -*-
"""agent/gateway/opencode_proxy.py —— opencode-go 网关转接头（独立进程）。

职责只有一件：把 Agent 的 OpenAI 兼容**流式**请求转发到 opencode-go 网关，
补齐网关专属的 `x-opencode-session` 会话头，并把上游响应**边收边转**（不缓冲）。

为什么需要一个独立进程、且转发用 curl：
  * opencode-go 网关按 `x-opencode-session` 做会话路由，标准 OpenAI 客户端不发；
  * opencode.ai 在 Cloudflare 后按 TLS 指纹封禁 python-urllib（实测 403 error 1010），
    而 curl 的 TLS 指纹可正常通过 —— 故转发用 curl。

会话键（转接头核心）：
  Agent 每轮请求带 `x-agent-session: <对话键>`，本适配器把它映射成**稳定**的网关
  会话 UUID —— 同一个键永远映射到同一个网关会话，多轮延续在网关侧才成立。不带键
  则退化为随机 UUID（请求仍被接受，只是不保证网关侧多轮连续）。

启动：
    python -m agent.gateway.opencode_proxy --port 8901 --api-key <key>
key 也可从环境变量 OPENCODE_GO_API_KEY 读取；**不落盘、不回显**。
"""
import json
import os
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8901
#: 固定上游：本适配器只服务 opencode-go，不做通用代理（上游可在测试中注入）
GATEWAY_BASE = "https://opencode.ai/zen/go/v1"
UPSTREAM_TIMEOUT_SECONDS = 60
SESSION_TTL_SECONDS = 24 * 3600      # 会话键映射有效期（滚动续期）
MAX_SESSIONS = 1024                  # 上限；超出后淘汰最久未用的键
AGENT_SESSION_HEADER = "x-agent-session"
GATEWAY_SESSION_HEADER = "x-opencode-session"
CHAT_PATH = "/v1/chat/completions"


def get_or_create_session(session_map, agent_key, ttl=SESSION_TTL_SECONDS,
                          max_sessions=MAX_SESSIONS):
    """转接头核心：agent 会话键 → 网关会话 UUID（一对一稳定映射）。"""
    now = time.time()
    hit = session_map.get(agent_key)
    if hit and now - hit[1] < ttl:
        session_map[agent_key] = (hit[0], now)         # 滚动续期
        return hit[0]
    session_id = str(uuid.uuid4())
    session_map[agent_key] = (session_id, now)
    if len(session_map) > max_sessions:                # 简单淘汰：最久未用
        oldest = min(session_map, key=lambda k: session_map[k][1])
        session_map.pop(oldest, None)
    return session_id


def parse_args(argv):
    """只认 --port 与 --api-key；未知参数返回 None（调用方报用法错误）。"""
    out = {"port": DEFAULT_PORT, "api_key": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--port" and i + 1 < len(argv):
            try:
                out["port"] = int(argv[i + 1])
            except ValueError:
                return None
            i += 2
        elif a == "--api-key" and i + 1 < len(argv):
            out["api_key"] = argv[i + 1]
            i += 2
        else:
            return None
    return out


class Handler(BaseHTTPRequestHandler):
    """只服务 POST /v1/chat/completions：补会话头 → curl 转发 → 边收边转。"""

    server_version = "opencode-adapter/2.0"
    protocol_version = "HTTP/1.1"

    def _send_error(self, code, message):
        body = json.dumps({"error": {"code": "ADAPTER_ERROR", "message": message}},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_POST(self):
        if self.path not in (CHAT_PATH, "/chat/completions"):
            return self._send_error(404, "未知路径：%s（本适配器只服务 %s）"
                                    % (self.path, CHAT_PATH))
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"

        agent_key = (self.headers.get(AGENT_SESSION_HEADER) or "").strip()
        session_id = (get_or_create_session(self.server.sessions, agent_key)
                      if agent_key else str(uuid.uuid4()))

        cmd = ["curl", "-sS", "-N", "--no-buffer",
               "--max-time", str(UPSTREAM_TIMEOUT_SECONDS),
               "-X", "POST", self.server.upstream + "/chat/completions",
               "-H", "Authorization: Bearer " + self.server.api_key,
               "-H", "Content-Type: application/json",
               "-H", GATEWAY_SESSION_HEADER + ": " + session_id,
               "--data-binary", "@-"]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            return self._send_error(502, "无法启动转发（curl 不可用？）：%s" % exc)

        try:
            proc.stdin.write(body)
            proc.stdin.close()
        except Exception:
            pass

        # 先读第一行：判断是 SSE 还是错误体；也避免「发完 200 头才发现上游无响应」。
        try:
            first = proc.stdout.readline()
        except Exception:
            first = b""
        if not first:
            rc = proc.wait()
            err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            return self._send_error(502, "上游无响应（curl rc=%s）：%s" % (rc, err[:300]))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        head = first.decode("utf-8", "replace")
        if not head.lstrip().startswith("data:"):
            # 上游不是 SSE（错误 JSON / 非流式响应）：整体包成一帧 data:，让 Agent 侧可见
            rest = b""
            try:
                rest = proc.stdout.read() or b""
            except Exception:
                pass
            blob = (first + rest).decode("utf-8", "replace").strip()
            try:
                self.wfile.write(("data: %s\n\n" % blob).encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            proc.wait()
            return

        # SSE：逐行转发、逐行 flush（不缓冲）
        try:
            self.wfile.write(first)
            self.wfile.flush()
            for line in proc.stdout:
                self.wfile.write(line)
                self.wfile.flush()
        except Exception:
            pass                                     # 客户端断开：收尾即可
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def log_message(self, fmt, *args):
        sys.stderr.write("[opencode-adapter] %s\n" % (fmt % args))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    conf = parse_args(argv)
    if conf is None:
        sys.stderr.write("未知参数（可用：--port / --api-key）\n")
        return 2
    key = conf["api_key"] or os.environ.get("OPENCODE_GO_API_KEY") or ""
    if not key:
        sys.stderr.write("缺少 API key：--api-key 或环境变量 OPENCODE_GO_API_KEY\n")
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", conf["port"]), Handler)
    srv.api_key = key
    srv.upstream = GATEWAY_BASE
    srv.sessions = {}
    sys.stderr.write("opencode 适配器已启动：127.0.0.1:%d → %s\n"
                     % (conf["port"], GATEWAY_BASE))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:                        # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
