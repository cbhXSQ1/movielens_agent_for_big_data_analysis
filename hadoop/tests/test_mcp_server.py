# -*- coding: utf-8 -*-
"""tests/test_mcp_server.py —— 服务层测试（配置、口令、鉴权、资源清单、协议形状）。

分两类：
  * **不需要第三方包**的部分（配置、口令、鉴权中间件、健康载荷、资源清单、
    说明文字）在任何环境都能跑 —— 仓库统一测试入口用的是系统自带解释器；
  * **需要官方协议包/参数模型**的部分（按工具表合成签名、二十个工具的对外结构、
    资源与模板注册、失败信封不走协议异常）用 `skipUnless` 条件跳过，
    在仓库内独立环境里单独跑一遍（命令见文件末尾说明）。
"""

import asyncio
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (os.path.join(_REPO_ROOT, "hadoop"), _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcpserver import contract, readers, server as srv, tools  # noqa: E402


def _module_available(name):
    try:
        __import__(name)
    except Exception:                       # noqa: BLE001 —— 缺包就是跳过，不是失败
        return False
    return True


PYDANTIC_AVAILABLE = _module_available("pydantic")
SDK_AVAILABLE = _module_available("mcp.server.mcpserver")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mcp-cfg-")
        self.path = os.path.join(self.tmp, "var", "mcp", "server.json")
        self._saved_env = {key: os.environ.get(key)
                           for key in list(srv.ENV_OVERRIDES) + [srv.ENV_CONFIG]}
        for key in self._saved_env:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_are_sane(self):
        cfg = srv.load_config(self.path)
        self.assertEqual(cfg["host"], "0.0.0.0")     # 必须监听所有网卡，否则转发进不来
        self.assertEqual(cfg["port"], 8765)
        self.assertEqual(cfg["streamable_http_path"], "/mcp")
        self.assertEqual(cfg["health_path"], "/health")
        self.assertEqual(cfg["token"], "")

    def test_default_allowed_hosts_cover_the_forwarded_path(self):
        """真实链路是端口转发进来的，Host 头是客户端填的本机回环那几种写法。

        少一个就会表现为"连不上"，而真正原因是防重绑定策略把请求判成非法，
        所以这里把六种写法全部锁死。
        """
        cfg = srv.load_config(self.path)
        self.assertTrue(cfg["enable_dns_rebinding_protection"])
        for host in srv.LOOPBACK_HOSTS:
            self.assertIn(host, cfg["allowed_hosts"], "默认允许列表缺少 %s" % host)
        for host in ("127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*",
                     "[::1]", "[::1]:*"):
            self.assertIn(host, srv.LOOPBACK_HOSTS)

    def test_loopback_hosts_are_always_effective(self):
        """旧配置里只存了两条也要能用：回环那几种写法永远生效。"""
        cfg = srv.load_config(self.path)
        cfg["allowed_hosts"] = ["localhost:*"]
        effective = srv.effective_allowed_hosts(cfg)
        for host in srv.LOOPBACK_HOSTS:
            self.assertIn(host, effective)
        self.assertEqual(len(effective), len(set(effective)), "不应重复")

    def test_extra_hosts_are_additive_for_lan_access(self):
        cfg = srv.load_config(self.path)
        cfg["allowed_hosts"] = ["10.0.2.15", "10.0.2.15:*"]
        effective = srv.effective_allowed_hosts(cfg)
        self.assertIn("10.0.2.15:*", effective)
        self.assertIn("127.0.0.1:*", effective)

    def test_status_reports_allowed_hosts(self):
        """装机排障要看得到当前允许的 Host —— 从命令行实跑一遍取输出。"""
        from mcpserver import __main__ as cli

        cfg = srv.load_config(self.path)
        cfg["token"] = "k" * 32
        cfg["port"] = 18999
        srv.save_config(cfg, self.path)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main(["status", "--config", self.path])
        payload = json.loads(buffer.getvalue())
        self.assertIn("allowed_hosts", payload)
        self.assertIn("download_routes", payload)
        self.assertTrue(payload["download_routes"]["task"].startswith("/download/task/"))
        self.assertIn("127.0.0.1:*", payload["allowed_hosts"])
        self.assertTrue(payload["enable_dns_rebinding_protection"])
        self.assertFalse(payload["listening"])

    def test_file_overrides_defaults_and_round_trips(self):
        cfg = srv.load_config(self.path)
        cfg["port"] = 9999
        cfg["token"] = "a" * 32
        srv.save_config(cfg, self.path)
        again = srv.load_config(self.path)
        self.assertEqual(again["port"], 9999)
        self.assertEqual(again["token"], "a" * 32)
        if os.name == "nt":
            # Windows 的 chmod 只能翻只读位，表达不了 0600；这条断言只在 POSIX 上验
            self.skipTest("Windows 不支持 POSIX 权限位，0600 断言跳过（往返部分已通过）")
        mode = os.stat(self.path).st_mode & 0o777
        self.assertEqual(mode, 0o600, "含口令的配置文件应收紧到仅所有者可读写")

    def test_env_overrides_file(self):
        cfg = srv.load_config(self.path)
        cfg["token"] = "b" * 32
        srv.save_config(cfg, self.path)
        os.environ["MCP_PORT"] = "18080"
        os.environ["MCP_HOST"] = "127.0.0.1"
        os.environ["MCP_TOKEN"] = "c" * 32
        loaded = srv.load_config(self.path)
        self.assertEqual(loaded["port"], 18080)
        self.assertEqual(loaded["host"], "127.0.0.1")
        self.assertEqual(loaded["token"], "c" * 32)

    def test_env_config_chooses_file(self):
        spare = os.path.join(self.tmp, "elsewhere.json")
        with open(spare, "w", encoding="utf-8") as fh:
            json.dump({"port": 17000}, fh)
        os.environ[srv.ENV_CONFIG] = spare
        cfg = srv.load_config()
        self.assertEqual(cfg["port"], 17000)
        self.assertEqual(cfg["config_path"], os.path.abspath(spare))

    def test_invalid_json_is_an_error_not_a_silent_default(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{不是 JSON")
        with self.assertRaises(ValueError):
            srv.load_config(self.path)

    def test_bad_port_rejected(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"port": 70000}, fh)
        with self.assertRaises(ValueError):
            srv.load_config(self.path)
        os.environ["MCP_PORT"] = "abc"
        with self.assertRaises(ValueError):
            srv.load_config(self.path)

    def test_missing_file_is_fine_but_reported(self):
        cfg = srv.load_config(self.path)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(cfg["config_path"], os.path.abspath(self.path))


# ---------------------------------------------------------------------------
# 口令
# ---------------------------------------------------------------------------

class TokenTest(unittest.TestCase):
    def test_generated_token_is_long_and_unique(self):
        a, b = srv.generate_token(), srv.generate_token()
        self.assertGreaterEqual(len(a), srv.MIN_TOKEN_LENGTH)
        self.assertNotEqual(a, b)

    def test_fingerprint_never_contains_the_token(self):
        token = srv.generate_token()
        fingerprint = srv.token_fingerprint(token)
        self.assertNotIn(token, fingerprint)
        for chunk in (token[4:], token[:8]):
            self.assertNotIn(chunk, fingerprint)

    def test_require_token_rejects_blank_and_short(self):
        with self.assertRaises(RuntimeError):
            srv.require_token({"token": ""})
        with self.assertRaises(RuntimeError):
            srv.require_token({"token": "short"})
        token = "x" * srv.MIN_TOKEN_LENGTH
        self.assertEqual(srv.require_token({"token": token}), token)

    def test_ensure_config_creates_once_and_keeps_it(self):
        tmp = tempfile.mkdtemp(prefix="mcp-tok-")
        path = os.path.join(tmp, "var", "mcp", "server.json")
        for key in list(srv.ENV_OVERRIDES):
            os.environ.pop(key, None)
        cfg, created = srv.ensure_config(path, create_token=True)
        self.assertTrue(created)
        first = cfg["token"]
        cfg2, created2 = srv.ensure_config(path, create_token=True)
        self.assertFalse(created2)
        self.assertEqual(cfg2["token"], first, "已有口令不得被悄悄换掉")

    def test_config_file_is_owner_only_from_the_moment_it_exists(self):
        """口令文件不能有"先按默认权限落盘、再改权限"的空窗。

        原先的写法是先 `open()`（默认 0644）再 `chmod(0600)`：从文件出现到改权限
        之间，同机其它用户能读到口令。改成建文件时就 0600，中间没有可读窗口。
        这里锁两件事：最终权限是 0600，且**临时文件**也是 0600（它就是那个空窗）。
        """
        if os.name == "nt":
            # Windows 的 chmod 只能翻只读位，"落盘即 0600"这条性质在这里无法验证
            self.skipTest("Windows 不支持 POSIX 权限位，0600 空窗断言跳过")
        tmp = tempfile.mkdtemp(prefix="mcp-perm-")
        path = os.path.join(tmp, "var", "mcp", "server.json")
        cfg = srv._defaults()
        cfg["token"] = "x" * 40
        srv.save_config(cfg, path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600, "配置文件必须是 0600，实际 %o" % mode)
        # 临时文件只在写入期间存在，这里直接验证"用它落盘时的权限"
        tmp_path = path + ".tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        try:
            tmp_mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
            self.assertEqual(tmp_mode, 0o600, "临时文件也必须是 0600，实际 %o" % tmp_mode)
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

class HealthTest(unittest.TestCase):
    ALLOWED_KEYS = {"ok", "status", "service", "version", "interface_version",
                    "started_at", "uptime_seconds", "endpoints"}
    #: 健康检查里允许出现的信息：**只有存活、版本、时间与"入口开在哪"**。
    #: 任何业务数据（任务编号、计数、分数、文件内容）都不许出现 ——
    #: 这条道路不需要口令，它就是"不用口令能看到什么"的上限。
    ALLOWED_ENDPOINT_KEYS = {"protocol", "health", "download_task",
                             "download_published", "download_model", "auth"}

    def test_payload_is_minimal_and_carries_contract_version(self):
        cfg = dict(srv.DEFAULT_CONFIG)
        ctx = srv.new_context(cfg, started_at="2026-10-17T00:00:00Z")
        payload = srv.health_payload(ctx, cfg, __import__("time").time())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["interface_version"], contract.INTERFACE_VERSION)
        self.assertEqual(payload["started_at"], "2026-10-17T00:00:00Z")
        self.assertEqual(set(payload), self.ALLOWED_KEYS,
                         "健康检查不该带任何数据字段：它是不需要口令就能调到的")
        self.assertNotIn("token", json.dumps(payload).lower())

    def test_endpoints_say_where_things_are_without_any_data(self):
        """两条下载路由与健康检查都不在工具表里，对端只能靠文档猜 —— 这里给路径形状。"""
        cfg = dict(srv.DEFAULT_CONFIG)
        ctx = srv.new_context(cfg, started_at="2026-10-17T00:00:00Z")
        payload = srv.health_payload(ctx, cfg)
        endpoints = payload["endpoints"]
        self.assertEqual(set(endpoints), self.ALLOWED_ENDPOINT_KEYS)
        self.assertEqual(endpoints["protocol"], cfg["streamable_http_path"])
        self.assertEqual(endpoints["health"], cfg["health_path"])
        self.assertEqual(endpoints["download_task"], srv.DOWNLOAD_TASK_ROUTE)
        self.assertEqual(endpoints["download_published"], srv.DOWNLOAD_PUBLISHED_SHAPE)
        self.assertEqual(endpoints["download_model"], srv.DOWNLOAD_MODEL_ROUTE)
        # 只给形状：不带主机、端口、口令，也不带任何业务数据
        text = json.dumps(endpoints)
        self.assertNotIn("token", text.lower())
        self.assertNotIn("8765", text)
        self.assertNotIn("Authorization: Bearer <", text.lower().replace(
            "authorization: bearer <口令>", "x"))

    def test_health_path_must_not_be_root(self):
        """健康检查路径配成 `/` 等于把整个服务免口令 —— 启动前必须拦住。"""
        self.assertEqual(srv.validate_health_path("/health", "/mcp"), "/health")
        self.assertEqual(srv.validate_health_path("/health/", "/mcp"), "/health")
        self.assertEqual(srv.validate_health_path("/probe", "/mcp"), "/probe")
        for bad in ("/", "", "   ", "health", "/health?x=1", "/he alth", "/health#top"):
            with self.assertRaises(ValueError, msg=bad):
                srv.validate_health_path(bad, "/mcp")
        # 与协议入口撞车也会把协议入口顶掉，同样拒绝
        with self.assertRaises(ValueError):
            srv.validate_health_path("/mcp", "/mcp")


# ---------------------------------------------------------------------------
# 鉴权中间件（纯 ASGI，标准库即可测）
# ---------------------------------------------------------------------------

class _Recorder(object):
    """假的 ASGI 应用：记录是否被调用。"""

    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(middleware, scope):
    """跑一次中间件调用，收集发出的消息。"""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


def _scope(path, auth=None):
    headers = []
    if auth is not None:
        headers.append((b"authorization", auth.encode("latin-1")))
    return {"type": "http", "path": path, "headers": headers, "method": "POST"}


def _scope_raw(path, auth_bytes):
    """请求头直接给字节（模拟对端发来 UTF-8 字节这种真实情形）。"""
    return {"type": "http", "path": path,
            "headers": [(b"authorization", auth_bytes)], "method": "POST"}


class AuthMiddlewareTest(unittest.TestCase):
    TOKEN = "t" * 40

    def setUp(self):
        self.app = _Recorder()
        self.mw = srv.BearerAuthMiddleware(self.app, token=self.TOKEN,
                                           exempt_paths=("/health",))

    def _status(self, sent):
        for message in sent:
            if message["type"] == "http.response.start":
                return message["status"]
        return None

    def test_exempt_path_passes_without_token(self):
        sent = _run(self.mw, _scope("/health"))
        self.assertEqual(self._status(sent), 200)
        self.assertEqual(self.app.calls, ["/health"])

    def test_exempt_path_ignores_trailing_slash(self):
        sent = _run(self.mw, _scope("/health/"))
        self.assertEqual(self._status(sent), 200)
        self.assertEqual(self.app.calls, ["/health/"])

    def test_missing_token_is_401_with_json_body(self):
        sent = _run(self.mw, _scope("/mcp"))
        self.assertEqual(self._status(sent), 401)
        self.assertEqual(self.app.calls, [], "没口令时不得进入协议层")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        payload = json.loads(body.decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"],
                         srv.BearerAuthMiddleware.CODE_UNAUTHORIZED)
        self.assertEqual(payload["interface_version"], contract.INTERFACE_VERSION)

    def test_wrong_token_is_401_and_does_not_echo_token(self):
        sent = _run(self.mw, _scope("/mcp", "Bearer wrong-token-value"))
        self.assertEqual(self._status(sent), 401)
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        self.assertNotIn(b"wrong-token-value", body, "回应里不得回显收到的口令")

    def test_valid_token_reaches_protocol_layer(self):
        sent = _run(self.mw, _scope("/mcp", "Bearer " + self.TOKEN))
        self.assertEqual(self._status(sent), 200)
        self.assertEqual(self.app.calls, ["/mcp"])

    def test_token_comparison_is_exact(self):
        self.assertFalse(self.mw.is_valid(self.TOKEN + "x"))
        self.assertFalse(self.mw.is_valid(self.TOKEN[:-1]))
        self.assertFalse(self.mw.is_valid(""))
        self.assertFalse(self.mw.is_valid(None))
        self.assertTrue(self.mw.is_valid(self.TOKEN))

    def test_non_ascii_token_is_401_not_500(self):
        """带非 ASCII 的口令必须得到 401，而不是"服务出错"。

        原先直接用 `hmac.compare_digest` 比较字符串，它在收到非 ASCII 时会抛
        `TypeError` —— 于是"口令不对"变成 500，还顺手给了对方一个刷错误日志的口子。
        """
        for wrong in ("口令中文", "ｆｕｌｌｗｉｄｔｈ", "ключ", "t" * 20 + "汉"):
            self.assertFalse(self.mw.is_valid(wrong), wrong)
        # 走完整中间件：状态码必须是 401，且回应是结构化信封
        sent = _run(self.mw, _scope_raw("/mcp", "Bearer 你好世界".encode("utf-8")))
        self.assertEqual(self._status(sent), 401)
        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["error"]["code"],
                         srv.BearerAuthMiddleware.CODE_UNAUTHORIZED)

    def test_non_ascii_header_bytes_do_not_crash(self):
        """请求头里直接是 UTF-8 字节（非 ASCII）时也不能崩。"""
        sent = _run(self.mw, _scope_raw("/mcp", "Bearer 你好".encode("utf-8")))
        self.assertEqual(self._status(sent), 401)
        self.assertEqual(self.app.calls, [])

    def test_non_ascii_configured_token_still_matches_exactly(self):
        """配置里的口令若是非 ASCII，对端发来同样的字节也必须能通过。"""
        token = "口令-密钥-混合abc"
        mw = srv.BearerAuthMiddleware(_Recorder(), token=token,
                                      exempt_paths=("/health",))
        self.assertTrue(mw.is_valid(token))
        self.assertFalse(mw.is_valid("口令-密钥-混合abd"))

    def test_non_http_scope_passes_through(self):
        sent = _run(self.mw, {"type": "lifespan", "path": "/mcp"})
        self.assertEqual(self.app.calls, ["/mcp"], "生命周期事件不该被鉴权挡住")
        self.assertNotIn(401, [m.get("status") for m in sent])

    def test_401_carries_www_authenticate(self):
        sent = _run(self.mw, _scope("/mcp"))
        headers = dict(sent[0]["headers"])
        self.assertIn(b"www-authenticate", headers)
        self.assertEqual(headers[b"content-type"], b"application/json; charset=utf-8")

    def test_health_path_is_configurable(self):
        mw = srv.BearerAuthMiddleware(_Recorder(), token=self.TOKEN,
                                      exempt_paths=("/ping",))
        self.assertTrue(mw.is_exempt("/ping"))
        self.assertFalse(mw.is_exempt("/health"))


# ---------------------------------------------------------------------------
# 资源清单与说明文字（标准库即可断言）
# ---------------------------------------------------------------------------

class ResourceSpecTest(unittest.TestCase):
    def setUp(self):
        cfg = dict(srv.DEFAULT_CONFIG)
        self.ctx = srv.new_context(cfg, started_at="2026-10-17T00:00:00Z")

    def test_expected_resources_present(self):
        specs = srv.resource_specs(self.ctx)
        uris = [spec[0] for spec in specs]
        for expected in ("ml://schemes", "ml://published", "ml://tasks",
                         "ml://scheme/{name}", "ml://task/{task_id}/status",
                         "ml://task/{task_id}/files", "ml://task/{task_id}/report",
                         "ml://task/{task_id}/quarantine"):
            self.assertIn(expected, uris)

    def test_specs_are_well_formed(self):
        specs = srv.resource_specs(self.ctx)
        self.assertEqual(len({spec[0] for spec in specs}), len(specs), "URI 不得重复")
        for uri, name, description, mime_type, fn in specs:
            self.assertTrue(uri.startswith("ml://"))
            self.assertTrue(name and description and mime_type)
            self.assertTrue(callable(fn))

    def test_template_resources_take_argument(self):
        specs = dict((spec[0], spec[4]) for spec in srv.resource_specs(self.ctx))
        self.assertEqual(len(specs["ml://scheme/{name}"].__code__.co_varnames[:1]), 1)

    def test_long_running_tools_say_so_in_description(self):
        for tool in tools.TOOLS:
            text = srv.tool_description(tool)
            if tool["long_running"]:
                self.assertIn("长任务", text)
                self.assertIn(str(tool["estimated_seconds"]), text)
            else:
                self.assertNotIn("长任务", text)



class SchemeResourceTest(unittest.TestCase):
    """方案内容资源的路径白名单：越界必须被拒（复用读取层，不另写一套）。"""

    def setUp(self):
        cfg = dict(srv.DEFAULT_CONFIG)
        self.ctx = srv.new_context(cfg, started_at="2026-10-17T00:00:00Z")

    def _read(self, name):
        return json.loads(srv._scheme_content_json(name, self.ctx))

    def test_known_scheme_returns_content(self):
        payload = self._read("scoring_scheme.v1.json")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "scoring_scheme.v1.json")
        self.assertIn("content", payload)

    def test_traversal_is_rejected(self):
        for name in ("../../etc/passwd", "..\\..\\etc\\passwd"):
            payload = self._read(name)
            self.assertFalse(payload["ok"], name)
            self.assertEqual(payload["error"]["code"], contract.CODE_FORBIDDEN_PATH, name)

    def test_missing_scheme_is_not_found(self):
        payload = self._read("no_such_scheme.json")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_FILE_NOT_FOUND)



# ---------------------------------------------------------------------------
# 只读下载路由（判定部分只需标准库；协议层形状放在需要协议包的类里）
# ---------------------------------------------------------------------------

class DownloadHostTest(unittest.TestCase):
    def test_exact_and_wildcard_port_match(self):
        allowed = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
        for host in ("127.0.0.1", "127.0.0.1:8765", "localhost", "localhost:8765"):
            self.assertTrue(srv.host_is_allowed(host, allowed), host)

    def test_unknown_host_rejected(self):
        allowed = list(srv.LOOPBACK_HOSTS)
        for host in ("evil.example.com", "evil.example.com:8765", "", None,
                     "127.0.0.1.evil.com:8765"):
            self.assertFalse(srv.host_is_allowed(host, allowed), host)

    def test_empty_allowlist_rejects_everything(self):
        self.assertFalse(srv.host_is_allowed("127.0.0.1:8765", []))

    def test_download_paths_are_not_auth_exempt(self):
        """下载能读到数据内容，鉴权不能比协议入口松。"""
        from mcpserver.server import BearerAuthMiddleware
        mw = BearerAuthMiddleware(None, token="t" * 32, exempt_paths=("/health",))
        self.assertFalse(mw.is_exempt("/download/task/abc/report.md"))
        self.assertFalse(mw.is_exempt("/download/published/ml1m-clean-v1/ratings"))


class DownloadTargetTest(unittest.TestCase):
    """任务产物下载的判定：越界、非产物、目录、不存在，各回各的错误码。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mcp-dl-")
        self.var = os.path.join(self.tmp, "var")
        self.task = os.path.join(self.var, "tasks", "t-0001")
        os.makedirs(os.path.join(self.task, "cleaned", "v1"))
        os.makedirs(os.path.join(self.task, "metrics"))
        with open(os.path.join(self.task, "report.md"), "wb") as fh:
            fh.write(b"# report\n")
        with open(os.path.join(self.task, "cleaned", "v1", "movies.dat"), "wb") as fh:
            fh.write(b"1::a\n2::b\n")
        with open(os.path.join(self.task, "metrics", ".after.json.raw.crc"), "wb") as fh:
            fh.write(b"crc")
        with open(os.path.join(self.task, ".hidden.json"), "wb") as fh:
            fh.write(b"h")
        with open(os.path.join(self.task, "cancel_request.json"), "wb") as fh:
            fh.write(b"{}")
        cfg = dict(srv.DEFAULT_CONFIG)
        self.ctx = srv.new_context(cfg, started_at="2026-10-17T00:00:00Z")
        self.ctx.env["ML_VAR_DIR"] = self.var
        self.task_id = "t-0001"

    def _code(self, relpath, task_id=None):
        try:
            srv.downloadable_task_file(self.ctx, task_id or self.task_id, relpath)
            return None
        except contract.StructuredError as exc:
            return exc.code

    def test_normal_artifact_ok(self):
        path, size = srv.downloadable_task_file(self.ctx, self.task_id, "report.md")
        self.assertEqual(size, os.path.getsize(os.path.join(self.task, "report.md")))
        path, size = srv.downloadable_task_file(self.ctx, self.task_id,
                                                "cleaned/v1/movies.dat")
        self.assertGreater(size, 0)

    def test_traversal_and_absolute_rejected(self):
        for relpath in ("../../../etc/passwd", "..\\..\\etc\\passwd",
                        "/etc/passwd", "cleaned/../../outside.txt"):
            self.assertEqual(self._code(relpath), contract.CODE_FORBIDDEN_PATH, relpath)

    def test_non_artifact_files_rejected(self):
        """不该对外提供的文件一律 `FORBIDDEN_PATH` —— 与"读产物"同一套码，两端不各说一套。"""
        # 细类取值与读取层共用一套（`reason_kind` 只有一个词汇表）：
        # control / sidecar / residue / directory / not_regular_file
        cases = {
            "metrics/.after.json.raw.crc": readers.EXCLUSION_KIND_SIDECAR,
            ".hidden.json": readers.EXCLUSION_KIND_SIDECAR,
            "cancel_request.json": readers.EXCLUSION_KIND_CONTROL,
        }
        for relpath, expected_kind in cases.items():
            try:
                srv.downloadable_task_file(self.ctx, self.task_id, relpath)
                self.fail("本该被拒：%s" % relpath)
            except contract.StructuredError as exc:
                self.assertEqual(exc.code, contract.CODE_FORBIDDEN_PATH, relpath)
                self.assertEqual(exc.extra.get("reason_kind"), expected_kind, relpath)

    def test_directory_and_missing(self):
        # 目录里没有可下载的文件：说"不存在"比说"越界"更贴近事实
        self.assertEqual(self._code("cleaned"), contract.CODE_FILE_NOT_FOUND)
        self.assertEqual(self._code("nope.md"), contract.CODE_FILE_NOT_FOUND)

    def test_symlink_to_control_file_rejected(self):
        """改过名的符号链接指向控制文件：读取会拒，下载也必须拒。

        只按**请求里的名字**判定时，这条会被放行 —— 两条路给出不同结论，
        等于绕过读取层的白名单。所以判定要同时看请求路径与解析后的真实路径。
        """
        link = os.path.join(self.task, "renamed-report.md")
        try:
            os.symlink(os.path.join(self.task, "cancel_request.json"), link)
        except OSError:
            self.skipTest("这个环境不允许创建符号链接（Windows 非开发者模式即如此）")
        self.assertEqual(self._code("renamed-report.md"), contract.CODE_FORBIDDEN_PATH)

    def test_special_file_rejected(self):
        """管道/设备这类特殊文件必须先拦住：对它们调用读取会挂住，把服务拖死。"""
        fifo = os.path.join(self.task, "pipe.dat")
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError):
            self.skipTest("这个环境不支持创建管道文件（mkfifo）")
        try:
            srv.downloadable_task_file(self.ctx, self.task_id, "pipe.dat")
            self.fail("特殊文件本该被拒")
        except contract.StructuredError as exc:
            self.assertEqual(exc.code, contract.CODE_FORBIDDEN_PATH)
            self.assertEqual(exc.extra.get("reason_kind"), "not_regular_file")

    def test_symlink_inside_root_to_artifact_is_allowed(self):
        """根目录之内、指向正当产物的链接不算越界（不必过度收紧）。"""
        link = os.path.join(self.task, "alias.md")
        try:
            os.symlink(os.path.join(self.task, "report.md"), link)
        except OSError:
            self.skipTest("这个环境不允许创建符号链接（Windows 非开发者模式即如此）")
        path, size = srv.downloadable_task_file(self.ctx, self.task_id, "alias.md")
        self.assertEqual(size, os.path.getsize(os.path.join(self.task, "report.md")))

    def test_unknown_task_rejected(self):
        self.assertEqual(self._code("report.md", task_id="t-nope"),
                         contract.CODE_TASK_NOT_FOUND)
        self.assertEqual(self._code("report.md", task_id="../t-0001"),
                         contract.CODE_FORBIDDEN_PATH)


class PublishedTargetTest(unittest.TestCase):
    ALLOWED_TABLES = ("movies", "ratings", "users")

    def test_valid_target(self):
        version, filename = srv.parse_published_target("ml1m-clean-v1", "movies")
        self.assertEqual(version, "ml1m-clean-v1")
        self.assertEqual(filename, "movies.dat")

    def test_all_three_tables_accepted(self):
        for table in self.ALLOWED_TABLES:
            self.assertTrue(srv.parse_published_target("v1", table)[1].endswith(".dat"))

    def test_bad_table_lists_allowed_values(self):
        with self.assertRaises(contract.StructuredError) as cm:
            srv.parse_published_target("ml1m-clean-v1", "comments")
        self.assertEqual(cm.exception.code, contract.CODE_USAGE)
        message = str(cm.exception)
        for table in self.ALLOWED_TABLES:
            self.assertIn(table, message)

    def test_unsafe_version_rejected(self):
        for version in ("../etc", "a/b", "..", "", "a b", "v1;rm -rf /", "x" * 65):
            with self.assertRaises(contract.StructuredError) as cm:
                srv.parse_published_target(version, "movies")
            self.assertEqual(cm.exception.code, contract.CODE_USAGE, version)

    def test_split_path(self):
        self.assertEqual(srv.split_published_path("v1/movies"), ("v1", "movies"))
        self.assertEqual(srv.split_published_path("/v1/movies/"), ("v1", "movies"))
        with self.assertRaises(contract.StructuredError):
            srv.split_published_path("v1")

    def test_download_routes_shapes(self):
        routes = srv.download_routes()
        self.assertIn("task", routes)
        self.assertIn("published", routes)
        self.assertTrue(routes["task"].startswith("/download/task/"))
        self.assertTrue(routes["published"].startswith("/download/published/"))


class PublishedReachabilityTest(unittest.TestCase):
    """"找不到文件"与"连不上集群"必须分开说 —— 这两种局面的处置完全不同。"""

    def setUp(self):
        cfg = dict(srv.DEFAULT_CONFIG)
        self.ctx = srv.new_context(cfg, started_at="2026-10-17T00:00:00Z")
        self._original = readers.run_hdfs

    def tearDown(self):
        readers.run_hdfs = self._original

    def _patch(self, result):
        readers.run_hdfs = lambda argv, ctx, timeout=None: result

    def test_size_returned(self):
        self._patch((0, "171131\n", ""))
        self.assertEqual(srv.published_file_size(self.ctx, "/data/published/v1/movies.dat"),
                         171131)

    def test_missing_file_is_not_found(self):
        self._patch((1, "", "stat: `/data/published/v1/movies.dat': "
                            "No such file or directory\n"))
        with self.assertRaises(contract.StructuredError) as cm:
            srv.published_file_size(self.ctx, "/data/published/v1/movies.dat")
        self.assertEqual(cm.exception.code, contract.CODE_FILE_NOT_FOUND)

    def test_cluster_down_is_unreachable(self):
        self._patch((1, "", "stat: Call From host/127.0.0.1 to host:9000 failed on "
                            "connection exception: Connection refused"))
        with self.assertRaises(contract.StructuredError) as cm:
            srv.published_file_size(self.ctx, "/data/published/v1/movies.dat")
        self.assertEqual(cm.exception.code, contract.CODE_DRIVER_UNREACHABLE)
        self.assertIn("连不上集群", str(cm.exception))

    def test_unknown_size_is_not_invented(self):
        self._patch((0, "not-a-number\n", ""))
        self.assertIsNone(srv.published_file_size(self.ctx, "/data/published/v1/x.dat"))


# ---------------------------------------------------------------------------
# 需要官方协议包 / 参数模型的部分（在独立环境里跑）
# ---------------------------------------------------------------------------

@unittest.skipUnless(SDK_AVAILABLE, "需要官方协议实现包（在仓库内独立环境里跑）")
class ServerShapeTest(unittest.TestCase):
    """协议形状：二十个工具、参数结构与工具表逐条一致、资源与模板可列。"""

    @classmethod
    def setUpClass(cls):
        cls.cfg = dict(srv.DEFAULT_CONFIG)
        cls.cfg["token"] = "z" * 40
        cls.ctx = srv.new_context(cls.cfg, started_at="2026-10-17T00:00:00Z")
        cls.server = srv.create_server(cls.cfg, cls.ctx)

    @staticmethod
    def _props(input_schema):
        return input_schema.get("properties") or {}

    @staticmethod
    def _unwrap(prop):
        """把"可空"包装拆掉，取出真实类型那一支（枚举/上下界都在里面）。"""
        if isinstance(prop, dict) and "anyOf" in prop:
            for branch in prop["anyOf"]:
                if branch.get("type") != "null":
                    merged = dict(branch)
                    merged.setdefault("default", prop.get("default"))
                    return merged
            return {"type": "null"}
        return prop

    def test_twenty_tools_and_names_match_table(self):
        listed = asyncio.run(self.server.list_tools())
        self.assertEqual([tool.name for tool in listed], list(tools.tool_names()))
        for tool, spec in zip(listed, tools.TOOLS):
            self.assertEqual(tool.title, spec["title"])
            self.assertTrue(tool.description.startswith(spec["description"][:10]))

    def test_schema_declares_additional_properties(self):
        """多余键是"接下来由分派层拒绝"，不是"悄悄丢掉" —— 写在结构里说清楚。"""
        listed = {tool.name: tool for tool in asyncio.run(self.server.list_tools())}
        for spec in tools.TOOLS:
            schema = listed[spec["name"]].input_schema
            self.assertIs(schema.get("additionalProperties"), True, spec["name"])

    def test_unknown_parameter_is_rejected_by_dispatch(self):
        """拼错的参数名必须报错，而不是被静默忽略。

        这条专门防"代理把 task_id 写成 taskId、服务按缺参或默认值跑完"的情形：
        用户看到的结果与意图不符，还查不出原因。
        """
        result = asyncio.run(self.server.call_tool("get_samples",
                                                   {"taskId": "abc"}))
        self.assertFalse(getattr(result, "is_error", getattr(result, "isError", False)))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_USAGE)
        self.assertIn("taskId", payload["error"]["message"])
        self.assertIn("task_id", payload["error"]["message"])

    def test_missing_required_parameter_is_rejected_by_dispatch(self):
        result = asyncio.run(self.server.call_tool("get_task_status", {}))
        self.assertFalse(getattr(result, "is_error", getattr(result, "isError", False)))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_USAGE)

    def test_normal_call_still_reaches_the_handler(self):
        """上一轮改动不能伤到正常调用：参数齐全时照常进业务判定。"""
        result = asyncio.run(self.server.call_tool("get_samples",
                                                   {"task_id": "no-such-task"}))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        # 进到了业务层：报的是任务不存在，而不是参数问题
        self.assertEqual(payload["error"]["code"], contract.CODE_TASK_NOT_FOUND)

    def test_defaults_filled_before_handler(self):
        result = asyncio.run(self.server.call_tool("get_samples",
                                                   {"task_id": "no-such-task",
                                                    "limit": 3}))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        self.assertIn("TASK_NOT_FOUND", text)

    def test_parameter_shape_matches_tool_table(self):
        listed = {tool.name: tool for tool in asyncio.run(self.server.list_tools())}
        for spec in tools.TOOLS:
            schema = listed[spec["name"]].input_schema
            props = self._props(schema)
            self.assertEqual(list(props), [p["name"] for p in spec["params"]],
                             "%s 的参数名/顺序应与工具表一致" % spec["name"])
            required = schema.get("required") or []
            self.assertEqual(set(required),
                             {p["name"] for p in spec["params"] if p.get("required")},
                             "%s 的必填项应与工具表一致" % spec["name"])
            for param in spec["params"]:
                prop = self._unwrap(props[param["name"]])
                expected_type = {"string": "string", "int": "integer", "bool": "boolean",
                                 "enum": "string", "object": "object"}[param["type"]]
                self.assertEqual(prop.get("type"), expected_type,
                                 "%s.%s 的类型不对" % (spec["name"], param["name"]))
                if param["type"] == "enum":
                    self.assertEqual(prop.get("enum"), list(param["values"]),
                                     "%s.%s 的取值范围不对" % (spec["name"], param["name"]))
                if param["type"] == "int":
                    if param.get("minimum") is not None:
                        self.assertEqual(prop.get("minimum"), param["minimum"])
                    if param.get("maximum") is not None:
                        self.assertEqual(prop.get("maximum"), param["maximum"])
                if not param.get("required") and param.get("default") is not None:
                    self.assertEqual(prop.get("default"), param["default"],
                                     "%s.%s 的默认值不对" % (spec["name"], param["name"]))

    def test_download_routes_registered(self):
        app = self.server.streamable_http_app(host="127.0.0.1")
        paths = [(getattr(route, "path", "") or "") for route in app.routes]
        self.assertTrue(any(path.startswith("/download/task") for path in paths), paths)
        self.assertTrue(any(path.startswith("/download/published") for path in paths), paths)

    def test_resources_and_templates_are_listed(self):
        resources = asyncio.run(self.server.list_resources())
        templates = asyncio.run(self.server.list_resource_templates())
        self.assertIn("ml://schemes", [item.uri for item in resources])
        uris = [getattr(t, "uri_template", None) or getattr(t, "uriTemplate", "")
                for t in templates]
        self.assertIn("ml://task/{task_id}/report", uris)
        for item in resources:
            self.assertTrue(item.name and item.description)

    def test_missing_token_refuses_to_start(self):
        """没有口令就拒绝启动：宁可起不来，也不敞开跑。"""
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["token"] = ""
        with self.assertRaises(RuntimeError):
            srv.build_app(config=cfg, repo=_REPO_ROOT)

    def test_root_health_path_refuses_to_start(self):
        """健康路径配成 / 等于整个服务免口令 —— 启动阶段就必须拒绝，而不是安静地敞开。"""
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["token"] = "z" * 40
        cfg["health_path"] = "/"
        with self.assertRaises(ValueError) as cm:
            srv.build_app(config=cfg, repo=_REPO_ROOT)
        self.assertIn("免口令", str(cm.exception))

    def test_health_path_equal_to_protocol_path_refuses_to_start(self):
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["token"] = "z" * 40
        cfg["health_path"] = cfg["streamable_http_path"]
        with self.assertRaises(ValueError):
            srv.build_app(config=cfg, repo=_REPO_ROOT)

    # ---- 调优配置必须**经完整组装**仍然生效（审查实测过它不生效）----

    def test_tuning_config_reaches_context_after_full_assembly(self):
        """配置里的调优项要真的传到处理器，而不是只传到 `new_context`。

        审查实测的偏差是：服务层构造上下文时只放了服务名与版本，于是统一调用通道的
        超时、长任务层的轮询间隔在**真实服务里**永远取默认值（配置写 0.05 秒实得 0.5 秒、
        写 0.001 秒超时实得 180 秒）。这里走完整组装：`build_app` → 服务对象 → 调能力，
        断言返回值里的轮询间隔就是配置里那个数。

        被等的任务用**自建夹具**（终态、存在）：不能拿某台机器上跑过的真实任务编号 ——
        那种编号只存在于那台机器的 `var/` 里，换台机器就是莫名其妙的 TASK_NOT_FOUND。
        """
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["token"] = "z" * 40
        cfg["wait_poll_seconds"] = 0.25
        cfg["driver_timeouts"] = {"validate": 7}
        cfg["scheduler_recent_limit"] = 3
        _app, cfg2, ctx = srv.build_app(config=cfg, repo=_REPO_ROOT)
        # ① 上下文里确实带上了这三个键（白名单透传）
        self.assertEqual(ctx.config.get("wait_poll_seconds"), 0.25)
        self.assertEqual(ctx.config.get("driver_timeouts"), {"validate": 7})
        self.assertEqual(ctx.config.get("scheduler_recent_limit"), 3)
        # ② 统一调用通道按它算超时
        from mcpserver import driver_bridge as bridge
        self.assertEqual(bridge.timeout_for("validate", ctx), 7.0)
        # ③ 长任务层按它算轮询间隔 —— 经服务对象调一次真实能力看返回值
        var_dir = tempfile.mkdtemp(prefix="mcp-tuning-")
        self.addCleanup(shutil.rmtree, var_dir, True)
        tid = "tuning-verify-01"
        task_dir = os.path.join(var_dir, "tasks", tid)
        os.makedirs(task_dir)
        stamp = "2026-10-20T00:00:00Z"
        with io.open(os.path.join(task_dir, "status.json"), "w",
                     encoding="utf-8") as fh:
            fh.write(json.dumps({"task_id": tid, "status": "succeeded",
                                 "stage": "done", "started_at": stamp,
                                 "updated_at": stamp, "message": "",
                                 "errors": []}, ensure_ascii=False, indent=2))
        # ML_VAR_DIR 同时决定"服务层本进程"与"driver 子进程"看哪个目录，两边必须同一个
        with mock.patch.dict(os.environ, {"ML_VAR_DIR": var_dir}):
            server = srv.create_server(cfg2, ctx)
            result = asyncio.run(server.call_tool(
                "wait_for_task", {"task_id": tid, "timeout_seconds": 0}))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertTrue(payload.get("ok"), text[:300])
        self.assertEqual(payload.get("poll_interval_seconds"), 0.25,
                         "配置里的轮询间隔没有传到长任务层")

    def test_health_route_rejects_unknown_host(self):
        """健康检查免口令，但**Host 校验不能省**：陌生 Host 应拿到 403。"""
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["token"] = "z" * 40
        app, _cfg, _ctx = srv.build_app(config=cfg, repo=_REPO_ROOT)
        ok = self._get(app, "/health", "127.0.0.1:8765")
        self.assertEqual(ok[0], 200, ok)
        bad = self._get(app, "/health", "evil.example.com:8765")
        self.assertEqual(bad[0], 403, bad)
        self.assertEqual(json.loads(bad[1]).get("error", {}).get("code"),
                         contract.CODE_FORBIDDEN_HOST)

    @staticmethod
    def _get(app, path, host):
        """直接驱动 ASGI 应用（不依赖 httpx 之类的测试客户端）。"""
        sent = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "root_path": "", "scheme": "http",
                 "headers": [(b"host", host.encode("latin-1")),
                             (b"accept", b"application/json")],
                 "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 12345)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(app(scope, receive, send))
        status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        return status, body.decode("utf-8", "replace")

    def test_tool_failure_returns_envelope_not_protocol_error(self):
        """失败信封必须当正常结果回来：对端要看得见错误码，而不是一句协议异常。"""
        result = asyncio.run(self.server.call_tool("get_task_status",
                                                  {"task_id": "no-such-task"}))
        self.assertFalse(getattr(result, "is_error", getattr(result, "isError", False)))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertIn("code", payload["error"])

    def test_out_of_range_value_returns_usage_envelope(self):
        """取值越界要走我们的信封（USAGE），而不是被协议层拦成一句异常。

        取值与上下界只用于"列工具"时展示，真正的拒绝在分派层 —— 这样对端
        永远能拿到错误码与补救说明。
        """
        result = asyncio.run(self.server.call_tool("get_samples",
                                                   {"task_id": "t1", "table": "nope"}))
        self.assertFalse(getattr(result, "is_error", getattr(result, "isError", False)))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], contract.CODE_USAGE)

    def test_health_tool_returns_ok_envelope(self):
        result = asyncio.run(self.server.call_tool("health_check", {}))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["interface_version"], contract.INTERFACE_VERSION)

    def test_unknown_tool_is_reported_through_envelope(self):
        # 协议层允许的未知工具会走协议错误；这里确认已注册工具的失败语义统一
        result = asyncio.run(self.server.call_tool("get_report",
                                                  {"task_id": "no-such-task",
                                                   "format": "md"}))
        self.assertFalse(getattr(result, "is_error", getattr(result, "isError", False)))
        text = "".join(getattr(item, "text", "") or ""
                       for item in getattr(result, "content", []) or [])
        self.assertFalse(json.loads(text)["ok"])


@unittest.skipUnless(_module_available("mcp.server.transport_security"),
                     "需要官方协议实现包（在仓库内独立环境里跑）")
class HostAllowListTest(unittest.TestCase):
    """Host 头的真实放行行为：端口转发这条主路径必须通得过。"""

    def setUp(self):
        from mcp.server.transport_security import (TransportSecurityMiddleware,
                                                  TransportSecuritySettings)
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["token"] = "z" * 40
        self.mw = TransportSecurityMiddleware(TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=srv.effective_allowed_hosts(cfg),
            allowed_origins=[]))

    def test_loopback_forms_pass(self):
        for host in srv.LOOPBACK_HOSTS:
            # "*" 是通配写法，本身不是真实 Host 头，用具体端口替换后判断
            probe = host.replace("*", "8765")
            self.assertTrue(self.mw._validate_host(probe), "被自己的策略挡掉了：%s" % probe)

    def test_unknown_host_is_rejected(self):
        self.assertFalse(self.mw._validate_host("evil.example.com:8765"))

    def test_lan_address_rejected_until_configured(self):
        """按虚拟机地址直连时会被拒 —— 这是刻意的，需要使用者显式加进配置。"""
        self.assertFalse(self.mw._validate_host("10.0.2.15:8765"))
        cfg = dict(srv.DEFAULT_CONFIG)
        cfg["allowed_hosts"] = ["10.0.2.15", "10.0.2.15:*"]
        from mcp.server.transport_security import (TransportSecurityMiddleware,
                                                  TransportSecuritySettings)
        mw = TransportSecurityMiddleware(TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=srv.effective_allowed_hosts(cfg),
            allowed_origins=[]))
        self.assertTrue(mw._validate_host("10.0.2.15:8765"))


class ReadinessTest(unittest.TestCase):
    """就绪情况必须**如实自洽**：就绪数 + 缺失数 = 总数，且缺失的都是真实存在的工具名。

    为什么不断言"全部就绪"：工具表与处理器由不同段落维护，工具先加、处理器后落地
    是常态（本次迭代就发生过）。此时正确行为是**点名缺哪些**，而不是让服务假装齐全。
    断言自洽性既锁住了这一点，又不会因为别人正在落地某个处理器而误报。
    """

    def test_missing_handlers_are_named(self):
        from mcpserver import dispatch
        saved = dict(dispatch.HANDLERS)
        try:
            dispatch.reset_handlers()
            report = srv.readiness_report()
            self.assertEqual(report["ready"], 0)
            self.assertEqual(set(report["missing"]), set(tools.tool_names()))
            self.assertEqual(report["total"], len(tools.tool_names()))

            dispatch.load_handlers()
            report = srv.readiness_report()
            self.assertEqual(report["ready"] + len(report["missing"]), report["total"],
                             "就绪数与缺失数必须加起来等于总数")
            self.assertGreater(report["ready"], 0)
            unknown = [name for name in report["missing"] if name not in tools.tool_names()]
            self.assertEqual(unknown, [], "点名缺失的能力必须是工具表里的名字")
            self.assertNotIn(None, report["missing"])
        finally:
            dispatch.reset_handlers()
            dispatch.HANDLERS.update(saved)


if __name__ == "__main__":
    unittest.main()

# ---------------------------------------------------------------------------
# 在仓库内独立环境里跑协议形状那几条：
#   PYTHONPATH=hadoop .venv/bin/python -m unittest hadoop.tests.test_mcp_server -v
# 只用系统解释器时它们会自动跳过（跳过原因会写在输出里）。
# ---------------------------------------------------------------------------
