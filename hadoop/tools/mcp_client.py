# -*- coding: utf-8 -*-
"""tools/mcp_client.py —— 给对端（Agent 侧）的最小连接示例。

这份脚本的目的不是"功能齐全的客户端"，而是把**连接、列工具、调能力、等任务**
这四件事用最短的代码写清楚，让对端照着就能接上。它也顺便当排障工具用：
口令不对、连不上、能力报错三种情形分别给出能看懂的话。

用法（在仓库根执行，用独立运行环境里的解释器）：

    # 1) 探活与鉴权自检（不需要口令的两条路 + 不带口令被拒的确认）
    .venv/bin/python hadoop/tools/mcp_client.py authcheck

    # 2) 列工具（最常看的一步：确认对端看到的能力与参数）
    .venv/bin/python hadoop/tools/mcp_client.py list

    # 3) 调能力（参数按 键=值 传；不传就是缺省值）
    .venv/bin/python hadoop/tools/mcp_client.py call list_tasks
    .venv/bin/python hadoop/tools/mcp_client.py call get_task_status task_id=20260930-001124-172e01
    .venv/bin/python hadoop/tools/mcp_client.py call get_samples task_id=... limit=3

    # 4) 等一个长任务（阻塞到终态或超时；超时会如实告诉你"还在跑"）
    .venv/bin/python hadoop/tools/mcp_client.py wait 20260930-001124-172e01 --timeout 600

    # 5) 只读资源
    .venv/bin/python hadoop/tools/mcp_client.py resources
    .venv/bin/python hadoop/tools/mcp_client.py read ml://schemes

    # 6) 下载（普通 GET，流式落盘；不经 JSON、不读进内存）
    .venv/bin/python hadoop/tools/mcp_client.py download-task \
        20261017-201144-2e9153 report.md --out /tmp/report.md
    .venv/bin/python hadoop/tools/mcp_client.py download-task \
        20261017-201144-2e9153 cleaned/ml1m-clean-v1/movies.dat --out /tmp/movies.dat
    .venv/bin/python hadoop/tools/mcp_client.py download-published \
        ml1m-clean-v1 ratings --out /tmp/ratings.dat

口令来源：**推荐 `--token-file` 从文件读**（文件权限建议 600）或环境变量 MCP_TOKEN；
`--token` 仍然支持，但它会把口令留在 shell 历史与进程表里，只适合临时排障 —— 用它时会
打印一次提醒。`--token-file` 指错位置时会直接给出"该怎么做"的一句话，不会甩堆栈。

地址来源：`--url`，或环境变量 MCP_URL（缺省 http://127.0.0.1:8765/mcp）。
**健康检查地址默认由 `--url` 推导**（同一协议、同一主机端口、路径 /health）：真实部署里
宿主机侧端口由使用方自定（联调时是宿主机 8900 → 虚拟机 8765），写死 8765 只在"就在本机
默认端口上"时才成立。要单独指定时用 `--health-url` 或环境变量 MCP_HEALTH_URL。

三条实现约定（对端照抄即可）：
  * 所有协议请求都带 `Authorization: Bearer <口令>` 头；
  * 能力返回永远是我们的信封：**先看 `ok`，再决定读业务字段还是读 `error`**；
  * 长任务受理后不要自己算进度 —— 调"等任务结束"或"查任务状态"拿真实值。
"""

import argparse
import asyncio
import builtins
import json
import os
import sys
import urllib.error
import urllib.request

#: 连接失败时客户端常把真正的错误包进"异常组"，而异常组并不继承 Exception —— 
#: 只写 `except Exception` 会漏掉它，结果给用户甩一屏堆栈。这里统一成一个元组。
_GROUP = getattr(builtins, "BaseExceptionGroup", None) or ()
CLIENT_ERRORS = (Exception,) if not _GROUP else (Exception, _GROUP)

DEFAULT_URL = os.environ.get("MCP_URL") or "http://127.0.0.1:8765/mcp"
#: 健康检查的**路径**（地址本身从协议地址推导，见 `health_url_for`）。
DEFAULT_HEALTH_PATH = "/health"
#: 环境变量显式指定健康检查地址（给了就用它，不再推导）。
ENV_HEALTH_URL = os.environ.get("MCP_HEALTH_URL") or ""


# ---------------------------------------------------------------------------
# 地址与口令
# ---------------------------------------------------------------------------

def health_url_for(args):
    """健康检查地址：**优先从协议地址推导**，只有显式给出时才用显式的。

    为什么必须推导：宿主机侧端口是使用方定的（联调时是宿主机 8900 → 虚拟机 8765），
    协议地址会跟着变；而健康检查地址写死 8765 只在"就在本机默认端口上"这一种情况下成立。
    同一份配置里，下载基地址早就从协议地址推导了，健康检查却写死 ——
    两种行为并存最容易让人误判成"服务没起"。
    """
    explicit = getattr(args, "health_url", None) or ENV_HEALTH_URL
    if explicit:
        return explicit
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(args.url)
    return urlunsplit((parts.scheme, parts.netloc, DEFAULT_HEALTH_PATH, "", ""))


def resolve_token(args):
    """口令：文件 > 环境变量；`--token` 仍然支持，但它是排障手段而不是日常用法。

    为什么不推荐 `--token`：命令行参数会留在 shell 历史里，也会出现在进程表
    （同机其它用户 `ps` 就能看到）—— 等于把口令写在了公告栏上。
    日常请用 `--token-file`（文件权限 600）或环境变量 `MCP_TOKEN`。
    这里对 `--token` 只提示一次，不阻断使用（排障时它最方便）。
    """
    if args.token:
        _warn_token_on_command_line()
        return args.token
    if args.token_file:
        try:
            with open(args.token_file, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            # 不做未处理异常：把"文件不存在/读不动"直接说清楚，并给出下一步。
            print("读不到口令文件：%s（%s）\n"
                  "  用 hadoop/scripts/mcp_server.sh token > %s 生成一份，或改用环境变量 MCP_TOKEN。"
                  % (args.token_file, exc, args.token_file), file=sys.stderr)
            raise SystemExit(2)
    return (os.environ.get("MCP_TOKEN") or "").strip()


#: `--token` 的提示只打印一次（resolve_token 在一次运行里会被调用多次）。
_TOKEN_WARNED = []


def _warn_token_on_command_line():
    """提示一次"命令行给口令会留下痕迹"（不阻断，排障时它最快）。"""
    if _TOKEN_WARNED:
        return
    _TOKEN_WARNED.append(True)
    print("提示：口令写在命令行里会留在 shell 历史与进程表中。"
          "日常请改用 --token-file（权限 600）或环境变量 MCP_TOKEN。", file=sys.stderr)


def preflight_token_source(args):
    """开跑之前先确认口令来源可用：返回"要打印的问题"或 None。

    单独抽出来的理由：`--token-file` 指错位置是**最常见的对接失误**之一，
    而它如果在深层（异步会话里）才炸，用户看到的是满屏堆栈。
    这里提前判一次，给一句能照着做的事。
    """
    if args.token_file:
        if not os.path.exists(args.token_file):
            return ("口令文件不存在：%s\n"
                    "  先用 hadoop/scripts/mcp_server.sh token > %s 生成一份。"
                    % (args.token_file, args.token_file))
        if not os.access(args.token_file, os.R_OK):
            return "口令文件读不动（权限不足）：%s" % args.token_file
    if args.token:
        _warn_token_on_command_line()
    return None


def flatten_exception(exc, depth=0):
    """把异常组摊平。

    连接失败时，客户端那边常常包成"异常组"（内部的并发结构会把真正的错误
    藏一层），直接打印只能看到"unhandled errors in a TaskGroup"这种无用信息。
    这里递归取出真正的原因，让对端看到"401"或"连接被拒"这样的关键字。
    """
    children = getattr(exc, "exceptions", None)
    if children and depth < 5:
        flat = []
        for child in children:
            flat.extend(flatten_exception(child, depth + 1))
        return flat
    return [exc]


def probe_http_status(url, token=None, timeout=5):
    """在错误路径上补一次"到底是不是鉴权问题"的判断。

    协议客户端把 401 包成了一句笼统的 "Server returned an error response"，
    光看它分不清是口令问题还是别的问题。这里直接发一个最小的握手请求，
    看服务端到底回了什么状态码 —— 只在出错时才走这一步，正常路径不受影响。
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "mcp-client-example", "version": "0.1"}}}
    headers = {"Authorization": "Bearer %s" % token} if token else None
    try:
        return _post_json(url, payload, headers=headers, timeout=timeout)
    except Exception:                                # noqa: BLE001 —— 探不到就交给上层说别的
        return None, ""


def explain_failure(exc, url, args=None):
    """把底层异常翻译成能照着做的话（对端最常撞的三种情况）。"""
    flat = flatten_exception(exc)
    detail = "；".join("%s: %s" % (type(item).__name__, item) for item in flat[:3])
    text = detail or ("%s: %s" % (type(exc).__name__, exc))
    lowered = " ".join(str(item).lower() for item in flat) or str(exc).lower()
    if "401" in lowered or "unauthorized" in lowered:
        return ("口令不对或没带：确认请求头是 Authorization: Bearer <口令>，"
                "口令可用 hadoop/scripts/mcp_server.sh token 取。原始错误：%s" % text)
    if "refused" in lowered or "connect" in lowered or "unreachable" in lowered:
        return ("连不上 %s：服务可能没启动（hadoop/scripts/mcp_server.sh start），"
                "或宿主机侧没配端口转发（宿主机侧端口由你定，虚拟机侧固定 8765）。原始错误：%s"
                % (url, text))
    if args is not None and "error response" in lowered:
        status, body = probe_http_status(url, resolve_token(args))
        if status == 401:
            return ("口令不对或没带：确认请求头是 Authorization: Bearer <口令>，"
                    "口令可用 hadoop/scripts/mcp_server.sh token 取。"
                    "（服务端回了 HTTP 401）")
        if status is not None:
            return ("服务端回了 HTTP %s，不是鉴权问题。原始回应：%s"
                    % (status, body.strip()[:300]))
    return "调用失败：%s" % text


# ---------------------------------------------------------------------------
# 动作：探活与鉴权自检（用标准库，不依赖协议包）
# ---------------------------------------------------------------------------

def _post_json(url, payload, headers=None, timeout=5):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream"})
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def action_authcheck(args):
    """三条确认：健康检查免口令、协议入口不带口令被拒、带口令能通过握手。"""
    health_url = health_url_for(args)
    ok = True

    try:
        with urllib.request.urlopen(health_url, timeout=args.timeout) as response:
            body = response.read().decode("utf-8", "replace")
        print("[1] 健康检查（不带口令）：HTTP %s" % response.status)
        print("    %s" % body.strip()[:400])
    except CLIENT_ERRORS as exc:                   # noqa: BLE001 —— 这里就是要报清楚
        ok = False
        print("[1] 健康检查失败：%s" % explain_failure(exc, health_url))

    init_payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18",
                               "capabilities": {},
                               "clientInfo": {"name": "mcp-client-example", "version": "0.1"}}}
    status, body = _post_json(args.url, init_payload)
    if status == 401:
        print("[2] 不带口令调协议入口：HTTP 401（符合预期，说明鉴权生效）")
        print("    %s" % body.strip()[:300])
    else:
        ok = False
        print("[2] 不带口令调协议入口返回了 HTTP %s —— 鉴权可能没生效，请检查：" % status)
        print("    %s" % body.strip()[:300])

    token = resolve_token(args)
    if not token:
        print("[3] 跳过：没有提供口令（--token / --token-file / MCP_TOKEN）")
        return 0 if ok else 1
    status, body = _post_json(args.url, init_payload,
                              headers={"Authorization": "Bearer %s" % token})
    if status == 200:
        print("[3] 带口令调协议入口：HTTP %s（已通过鉴权）" % status)
    else:
        ok = False
        print("[3] 带口令调协议入口失败：HTTP %s —— %s" % (status, body.strip()[:300]))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 动作：协议调用
# ---------------------------------------------------------------------------

async def _with_session(args, worker):
    """建连接、握手、执行动作。

    连接方式就是标准做法：一个带鉴权头的 HTTP 客户端 + 传输层 + 会话。
    各版本之间的差别只在传输函数的返回值形状上，这里按"取前两个"处理即可。
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    token = resolve_token(args)
    if not token:
        raise SystemExit("缺少口令：用 --token / --token-file 给，或设置环境变量 MCP_TOKEN")
    headers = {"Authorization": "Bearer %s" % token}

    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(args.url, http_client=http_client) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await worker(session)


def run_with_explanations(args, coro):
    """跑一次协议动作；失败时把底层原因翻成人话，绝不把堆栈甩给对端。

    单独抽出来是因为：连接失败时底层抛的是"异常组"，不包住的话用户只看到一屏堆栈，
    而真正的原因（口令不对 / 端口没转发 / 服务没起）全被埋在里层。
    """
    try:
        return asyncio.run(coro)
    except CLIENT_ERRORS as exc:                    # noqa: BLE001 —— 这里就是要报清楚
        print(explain_failure(exc, args.url, args), file=sys.stderr)
        return 2


def _parse_value(text):
    """参数值：能当 JSON 解析就当（数字、布尔、null），否则按字符串。"""
    try:
        return json.loads(text)
    except ValueError:
        return text


#: 打印信封时的最大字符数（排障工具不该把一屏刷满）。
PRINT_LIMIT = 8000


def _print_envelope(text, title="返回", full=False):
    """打印信封：先判断成没成，再挑重点打印，原文附在后面。

    超过上限时**明确标注"已截断"**并给出总长度 —— 否则用户会以为服务就返回了这么点
    内容（例如产物清单被截到一半，看起来像是服务少给了几项；审查时就有人被它骗过）。
    需要完整内容时加 `--full`，或者改用下载 / 对该能力传更小的条数。
    """
    try:
        payload = json.loads(text)
    except ValueError:
        print("%s（非 JSON）：%s" % (title, text))
        return 1
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    print("%s：" % title)
    if full or len(body) <= PRINT_LIMIT:
        print(body)
    else:
        print(body[:PRINT_LIMIT])
        print("……（已截断：完整返回共 %d 字符，上面只显示了前 %d 字符；"
              "要看全的加 --full，或改用下载 / 对该能力传更小的条数）"
              % (len(body), PRINT_LIMIT))
    if isinstance(payload, dict) and payload.get("ok") is False:
        error = payload.get("error") or {}
        print("—— 失败：%s %s" % (error.get("code"), error.get("message")))
        return 1
    return 0


def action_list(args):
    async def worker(session):
        result = await session.list_tools()
        tools = list(getattr(result, "tools", []) or [])
        print("可用能力 %d 项：" % len(tools))
        for tool in tools:
            schema = getattr(tool, "input_schema", None) or {}
            required = schema.get("required") or []
            params = list((schema.get("properties") or {}).keys())
            mark = "（必填：%s）" % "、".join(required) if required else ""
            print("  - %-24s %s %s" % (tool.name, tool.title or "", mark))
            if params:
                print("      参数：%s" % "、".join(params))
        return 0

    result = run_with_explanations(args, _with_session(args, worker))
    return 2 if isinstance(result, int) and result == 2 else result


def action_describe(args):
    return action_call(args, tool="describe_capabilities", pairs=[])


def action_call(args, tool=None, pairs=None):
    name = tool or args.target
    arguments = {}
    for pair in (pairs if pairs is not None else args.kv):
        if "=" not in pair:
            raise SystemExit("参数要写成 键=值，收到：%s" % pair)
        key, value = pair.split("=", 1)
        arguments[key.strip()] = _parse_value(value)

    async def worker(session):
        result = await session.call_tool(name, arguments)
        texts = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
        return "\n".join(texts)

    try:
        text = asyncio.run(_with_session(args, worker))
    except CLIENT_ERRORS as exc:                   # noqa: BLE001 —— 打印能看懂的话
        print(explain_failure(exc, args.url, args))
        return 2
    return _print_envelope(text, "能力 %s" % name, full=getattr(args, "full", False))


def action_wait(args):
    task_id = args.target
    timeout = args.timeout
    interval = args.interval

    async def worker(session):
        last = None
        waited = 0.0
        while True:
            result = await session.call_tool("wait_for_task",
                                             {"task_id": task_id,
                                              "timeout_seconds": int(interval)})
            text = "".join(getattr(item, "text", "") or ""
                           for item in getattr(result, "content", []) or [])
            try:
                payload = json.loads(text)
            except ValueError:
                return text
            status = payload.get("status")
            if status != last:
                print("  状态：%s（阶段 %s，进度 %s%%，已等 %.0f 秒）"
                      % (status,
                         payload.get("stage") or "-",
                         payload.get("progress_percent"),
                         waited))
                last = status
            if payload.get("ok") is False:
                return text
            if status in ("succeeded", "failed", "aborted") and not payload.get("timed_out"):
                return text
            waited += interval
            if waited >= timeout:
                return text

    try:
        text = asyncio.run(_with_session(args, worker))
    except CLIENT_ERRORS as exc:                   # noqa: BLE001
        print(explain_failure(exc, args.url, args))
        return 2
    return _print_envelope(text, "任务 %s" % task_id, full=getattr(args, "full", False))


def action_resources(args):
    async def worker(session):
        resources = list(getattr(await session.list_resources(), "resources", []) or [])
        templates = list(getattr(await session.list_resource_templates(),
                                 "resourceTemplates", []) or [])
        print("只读资源 %d 项：" % len(resources))
        for item in resources:
            print("  - %-28s %s" % (item.uri, item.name or ""))
            if item.description:
                print("      %s" % item.description)
        if templates:
            print("按编号/名称取用的资源模板 %d 项：" % len(templates))
            for item in templates:
                uri = getattr(item, "uri_template", None) or getattr(item, "uriTemplate", "")
                print("  - %-40s %s" % (uri, item.name or ""))
        return 0

    result = run_with_explanations(args, _with_session(args, worker))
    return 2 if isinstance(result, int) and result == 2 else result


def action_read(args):
    uri = args.target

    async def worker(session):
        result = await session.read_resource(uri)
        parts = []
        for item in getattr(result, "contents", []) or []:
            text = getattr(item, "text", None)
            if text is None:
                continue
            parts.append(text)
        return "\n".join(parts)

    try:
        text = asyncio.run(_with_session(args, worker))
    except CLIENT_ERRORS as exc:                   # noqa: BLE001
        print(explain_failure(exc, args.url, args))
        return 2
    return _print_envelope(text, "资源 %s" % uri, full=getattr(args, "full", False))


# ---------------------------------------------------------------------------
# 动作：下载（普通 HTTP GET，流式落盘）
# ---------------------------------------------------------------------------

def base_url(args):
    """下载用的基地址：没显式给就从协议入口地址里取协议 + 主机 + 端口。"""
    if args.base:
        return args.base.rstrip("/")
    from urllib.parse import urlsplit
    parts = urlsplit(args.url)
    return "%s://%s" % (parts.scheme, parts.netloc)


def _explain_download_error(exc):
    """下载失败翻成人话：先看是不是我们自己的结构化错误，再看连接问题。"""
    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
            error = payload.get("error") or {}
            return "下载被拒：HTTP %s，%s —— %s" % (
                exc.code, error.get("code"), error.get("message"))
        except ValueError:
            return "下载被拒：HTTP %s —— %s" % (exc.code, body.strip()[:200])
    return "下载失败：%s" % exc


def download_to_file(url, token, out_path, timeout):
    """把 URL 的内容流式写到文件；返回 (写入字节数, 响应声明的长度, 服务端给的文件名)。"""
    request = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        disposition = response.headers.get("Content-Disposition") or ""
        written = 0
        with open(out_path, "wb") as handle:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
    return written, declared, disposition


def _download(args, url, out_path):
    token = resolve_token(args)
    if not token:
        print("缺少口令：用 --token / --token-file 给，或设置环境变量 MCP_TOKEN", file=sys.stderr)
        return 2
    try:
        written, declared, disposition = download_to_file(url, token, out_path,
                                                          args.timeout)
    except urllib.error.HTTPError as exc:
        print(_explain_download_error(exc), file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(explain_failure(exc, url), file=sys.stderr)
        return 2
    print("已下载：%s" % out_path)
    print("  写入字节：%d" % written)
    if declared is not None:
        match = "一致" if str(written) == str(declared) else "不一致（需核对）"
        print("  服务端声明长度：%s（与实际写入 %s）" % (declared, match))
    if disposition:
        print("  服务端给的文件名：%s" % disposition)
    return 0


def action_download_task(args):
    """按任务编号 + 相对路径下载产物。"""
    relpath = args.target2
    if not relpath:
        print("用法：download-task <任务编号> <相对路径> --out 本地文件", file=sys.stderr)
        return 2
    from urllib.parse import quote
    url = "%s/download/task/%s/%s" % (base_url(args), quote(str(args.target), safe=""),
                                      quote(relpath.replace("\\", "/"), safe="/"))
    return _download(args, url, args.out or "download.bin")


def action_download_published(args):
    """按数据版本 + 表名下载已发布数据。"""
    table = args.target2
    if not table:
        print("用法：download-published <数据版本> <表名> --out 本地文件", file=sys.stderr)
        return 2
    from urllib.parse import quote
    url = "%s/download/published/%s/%s" % (base_url(args), quote(str(args.target), safe=""),
                                           quote(str(table), safe=""))
    return _download(args, url, args.out or ("%s.dat" % table))


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mcp_client.py",
        description="连到 Hadoop 侧能力服务的最小示例 / 排障工具")
    parser.add_argument("action", nargs="?", default="list",
                        choices=("list", "call", "describe", "wait", "resources",
                                 "read", "authcheck", "download-task",
                                 "download-published"),
                        help="要做什么，缺省 list")
    parser.add_argument("target", nargs="?",
                        help="能力名 / 任务编号 / 资源 URI / 数据版本")
    # 为什么只留一个"剩余位置参数"：下载动作要两个位置参数（目标 + 路径/表名），
    # 调用动作要若干个"键=值"。若把它们拆成两个各自可选的位置参数，argparse 会把
    # 调用动作的第一对"键=值"当成第二个位置参数吃掉，参数被**静默丢弃** ——
    # 表现为"调用成功但服务端说缺参数"，比直接报错更难查（实测踩到过）。
    # 因此统一收在这里，按动作再分派，见 main() 里的归一化。
    parser.add_argument("rest", nargs="*",
                        help="调用参数（键=值）或下载的第二个位置参数")
    parser.add_argument("--url", default=DEFAULT_URL, help="协议入口地址")
    parser.add_argument("--health-url", default=None,
                        help="健康检查地址（缺省从 --url 推导：同一主机端口 + /health）")
    parser.add_argument("--base", help="下载用的基地址（缺省从 --url 里取协议与主机）")
    parser.add_argument("--out", help="下载写到哪个本地文件")
    parser.add_argument("--full", action="store_true",
                        help="打印信封时不截断（默认超过 %d 字符会标注已截断）" % PRINT_LIMIT)
    parser.add_argument("--token", help="访问口令（**仅排障用**：会留在 shell 历史与进程表里，"
                                        "日常请用 --token-file 或环境变量 MCP_TOKEN）")
    parser.add_argument("--token-file", help="从文件读访问口令（推荐；文件权限建议 600）")
    parser.add_argument("--timeout", type=float, default=600.0, help="整体等待上限（秒）")
    parser.add_argument("--interval", type=float, default=5.0, help="等待时的轮询间隔（秒）")
    return parser


#: 需要两个位置参数（目标 + 路径/表名）的动作。
_TWO_POSITIONAL_ACTIONS = ("download-task", "download-published")


def _normalize_positionals(args):
    """把"剩余位置参数"按动作还原成 `target2` 与 `kv`（见 build_parser 的说明）。"""
    rest = list(getattr(args, "rest", None) or [])
    if args.action in _TWO_POSITIONAL_ACTIONS:
        args.target2 = rest[0] if rest else None
        args.kv = rest[1:]
    else:
        args.target2 = None
        args.kv = rest
    return args


def main(argv=None):
    args = _normalize_positionals(build_parser().parse_args(argv))
    # 口令来源先确认一遍：指错文件是最常见的对接失误，提前给人话，不要等深了才炸。
    problem = preflight_token_source(args)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if args.action in ("call", "wait", "read") and not args.target:
        print("这个动作需要给出名字/编号/URI", file=sys.stderr)
        return 2
    actions = {
        "list": action_list,
        "call": action_call,
        "describe": action_describe,
        "wait": action_wait,
        "resources": action_resources,
        "read": action_read,
        "authcheck": action_authcheck,
        "download-task": action_download_task,
        "download-published": action_download_published,
    }
    return actions[args.action](args)


if __name__ == "__main__":
    sys.exit(main())
