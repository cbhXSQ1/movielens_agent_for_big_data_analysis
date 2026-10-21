# -*- coding: utf-8 -*-
"""mcpserver/server.py —— 服务层：配置、口令、协议映射、只读资源、应用组装。

服务层很薄，只做四件事：
  1. **把二十项能力按标准协议对外提供**（二十个独立工具，参数结构由工具表合成）；
  2. **口令与健康检查**（协议请求要口令，探活单独开一条不需口令的道路）；
  3. **只读资源**（按 URI 取已发布版本、任务产物、报告、隔离样例、方案文件）；
  4. **常驻启动**（监听地址/端口/路径来自配置，可用环境变量覆盖）。

三条刻意为之的设计，改之前请先读：

**A. 失败信封当正常结果返回，不走协议异常。**
分派层（`dispatch.call_tool`）永远返回 `{"ok": …}` 信封：业务失败（任务不存在、
校验不过、被占用）都会以 `ok=false` 的信封回来。协议层本来有"工具执行失败"
这条异常通道，但那条通道的语义是"工具调用本身出错了"，对端只能看到一句错误文本，
看不到我们的错误码、补救提示与定位字段。所以这里把信封**整体序列化进返回内容**，
让对端永远拿到同一个形状；协议异常只留给真正的传输/实现错误。

**B. 参数校验只有一处（分派层），服务层只负责形状。**
服务层按 `tools.py` 的工具表合成函数签名，仅用于「列工具」时把参数名、类型、
必填、取值范围、默认值展示给对端；调用时仍交给分派层校验与补默认值。
两处口径由测试逐条比对，防止工具表改了、协议形状没跟上。

**C. 官方协议包只在函数里导入。**
仓库统一测试入口用的是系统自带解释器，那里没有装官方包；
`contract/tools/dispatch` 也要保持零依赖。因此本模块顶层只导入标准库与自家模块，
`mcp` / `starlette` / `uvicorn` / `pydantic` 全部在函数内部按需导入，
缺包时只有"起服务"这条路走不通，其余功能（配置、口令、鉴权中间件）照常可用。
"""

import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time

from . import contract, dispatch, readers, tools

# ---------------------------------------------------------------------------
# 标识与默认配置
# ---------------------------------------------------------------------------

#: 服务标识（出现在能力自描述与健康检查里，供对端辨认）。
SERVER_NAME = "movielens-hadoop-mcp"
#: 服务自身版本（与契约版本是两回事：契约版本见 `contract.INTERFACE_VERSION`）。
SERVER_VERSION = "0.1.0"

#: 配置文件相对仓库根的位置。`var/` 已在 .gitignore 里，口令不会入版本库。
CONFIG_RELPATH = os.path.join("var", "mcp", "server.json")
#: 可用环境变量指定配置文件位置（脚本与测试用）。
ENV_CONFIG = "MCP_CONFIG"

#: 默认允许的 Host 头（防重绑定用）。**这条与真实部署强相关，别随手删**：
#:
#: 我们的部署是"Agent 在宿主机、虚拟机的网卡是它的私有地址、连接经虚拟机软件的一条
#: 端口转发进来"。这条链路上服务收到的 Host 头是**客户端填的那个** ——
#: `127.0.0.1:8765` 或 `localhost:8765`，而不是虚拟机自己的地址。所以本机回环的
#: 三种写法（点分地址、主机名、IPv6 回环）各自带端口与不带端口都必须放行，
#: 否则端口转发这条主路径会被我们自己的防重绑定策略挡掉，而现象只是"连不上"。
#:
#: 将来若改成直接从局域网按虚拟机地址访问（例如网卡改桥接），
#: **必须把那个地址加进这份列表**（形如 `"10.0.2.15"` 与 `"10.0.2.15:*"`），
#: 或者按需关掉 `enable_dns_rebinding_protection`。这一点在装机排障时最容易漏。
LOOPBACK_HOSTS = [
    "127.0.0.1", "127.0.0.1:*",
    "localhost", "localhost:*",
    "[::1]", "[::1]:*",
]

#: 默认配置。监听所有网络接口：宿主机经端口转发进来时目标地址是虚拟机的网卡地址，
#: 只监听本机回环会导致转发进来的请求接不上（实现方案 §2）。
DEFAULT_CONFIG = {
    "name": SERVER_NAME,
    "version": SERVER_VERSION,
    "host": "0.0.0.0",
    "port": 8765,
    "streamable_http_path": "/mcp",
    "health_path": "/health",
    "token": "",
    "allowed_hosts": list(LOOPBACK_HOSTS),
    "allowed_origins": [],
    "enable_dns_rebinding_protection": True,
    "json_response": False,
    "stateless_http": False,
}

#: 环境变量 → 配置项（只覆盖这几个，其余只能改配置文件）。
ENV_OVERRIDES = {
    "MCP_HOST": "host",
    "MCP_PORT": "port",
    "MCP_TOKEN": "token",
    "MCP_PATH": "streamable_http_path",
    "MCP_HEALTH_PATH": "health_path",
}

#: 口令最小长度：太短的口令等于没有口令，启动时直接拒绝。
MIN_TOKEN_LENGTH = 16

#: 鉴权失败的错误码。它不属于业务错误码（那套在 `contract` 里），是传输层的事。
CODE_UNAUTHORIZED = "UNAUTHORIZED"
#: Host 不在允许列表里的错误码（同样属于传输层）。
CODE_FORBIDDEN_HOST = "FORBIDDEN_HOST"


# ---------------------------------------------------------------------------
# 时间与路径
# ---------------------------------------------------------------------------

def now_utc():
    """统一时间格式（UTC，秒级，带 Z）。与 driver 的写法一致，便于对端比对。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def effective_allowed_hosts(cfg):
    """实际生效的 Host 白名单 = 本机回环的几种写法 + 配置里额外给的。

    本机回环那几条**永远在内**（写死在这里），原因：旧配置文件里可能只存了两条，
    升级后不会自动补全，而缺的那几条会让端口转发这条主路径直接连不上；
    而"只允许回环"本身是安全的 —— 重绑定攻击要的是"外部域名解析到本机"，
    那种请求的 Host 是外部域名，照样会被拒。
    """
    hosts = list(LOOPBACK_HOSTS)
    for host in (cfg or {}).get("allowed_hosts") or []:
        text = str(host).strip()
        if text and text not in hosts:
            hosts.append(text)
    return hosts


def repo_root():
    """仓库根（本文件在 `<repo>/hadoop/mcpserver/` 下，上溯三层）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_config_path(config_path=None, repo=None):
    """配置文件位置：显式参数 > 环境变量 > 仓库内默认位置。"""
    if config_path:
        return os.path.abspath(config_path)
    from_env = os.environ.get(ENV_CONFIG)
    if from_env:
        return os.path.abspath(from_env)
    return os.path.join(repo or repo_root(), CONFIG_RELPATH)


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------

def _defaults():
    """默认配置的深拷贝（列表字段要复制，避免调用方改到全局）。"""
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _coerce_port(value):
    """端口：整数并做范围校验；非法取值直接报错，不静默回落。"""
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("端口必须是整数，收到 %r" % (value,))
    if not (1 <= port <= 65535):
        raise ValueError("端口必须在 1~65535 之间，收到 %r" % (value,))
    return port


def validate_health_path(value, protocol_path=None):
    """健康检查路径的形状校验：**必须是以 `/` 开头、且不等于 `/` 的具体路径**。

    这条校验为什么是安全项而不是洁癖：鉴权中间件的豁免规则是"请求路径等于配置里的
    健康路径"。一旦有人把它配成 `/`，**整个服务就免口令了** —— 包括协议入口与两条
    下载路由。这类事故不会报错、不会告警，只会安静地敞开，所以启动前必须拦住。

    另外两条一并拦下（都属于"配了但不会按预期工作"）：
      * 含空白、查询串、片段：路由匹配不上，表现为"健康检查 404"，很难查；
      * 与协议入口相同：两条路由抢同一个路径，健康检查会把协议入口顶掉。

    合法时返回规范化后的路径（去掉末尾多余的 `/`）。
    """
    text = value if isinstance(value, str) else ""
    text = text.strip()
    if not text:
        raise ValueError(
            "健康检查路径不能为空。请配一个以 / 开头、不等于 / 的具体路径，"
            "例如 /health")
    if not text.startswith("/"):
        raise ValueError(
            "健康检查路径必须以 / 开头，收到 %r（否则路由挂不上）" % (value,))
    if text == "/":
        raise ValueError(
            "健康检查路径不能是 / —— 豁免规则按路径匹配，配成 / 等于把整个服务"
            "免口令（协议入口与下载路由都会敞开）。请换成 /health 这类具体路径。")
    if any(ch in text for ch in (" ", "\t", "?", "#")):
        raise ValueError(
            "健康检查路径不能含空白、查询串或片段，收到 %r" % (value,))
    normalized = text.rstrip("/") or "/"
    if normalized == "/":
        raise ValueError("健康检查路径不能是 / —— 原因同上（整个服务会免口令）。")
    protocol = (protocol_path or "").strip().rstrip("/")
    if protocol and normalized == protocol:
        raise ValueError(
            "健康检查路径不能与协议入口相同（都是 %r）：两条路由会抢同一个路径，"
            "健康检查会把协议入口顶掉。" % normalized)
    return normalized


def load_config(config_path=None, repo=None):
    """读配置：默认值打底 → 合并文件 → 环境变量覆盖。

    文件不存在不算错（首次启动前会先 `init` 生成）；文件存在但内容不合法则**报错**，
    绝不带着半份配置起服务。
    """
    cfg = _defaults()
    path = resolve_config_path(config_path, repo)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except ValueError as exc:
            raise ValueError("配置文件不是合法 JSON：%s（%s）" % (path, exc))
        if not isinstance(stored, dict):
            raise ValueError("配置文件内容必须是对象：%s" % path)
        cfg.update(stored)
    cfg["port"] = _coerce_port(cfg.get("port"))

    for env_name, key in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        cfg[key] = _coerce_port(value) if key == "port" else value
    cfg["config_path"] = path
    return cfg


def save_config(cfg, config_path=None, repo=None):
    """写配置（含口令）。权限从**文件建立的那一刻**就是仅所有者可读写。

    为什么不用"先写临时文件、改名、再改权限"那套：`open()` 建出来的文件按默认权限
    （通常是 0644）落地，从它存在到 `chmod` 之间有一小段时间同机其它用户可以读到，
    而这份文件里就是口令。改成 `os.open(..., 0o600)` 建文件，权限在创建时确定，
    中间没有可读窗口。`os.replace` 是原子改名，读的一方要么看到旧文件、要么看到新文件。
    """
    path = config_path or cfg.get("config_path") or resolve_config_path(None, repo)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    stored = {key: cfg[key] for key in DEFAULT_CONFIG if key in cfg}
    # 调优项（`driver_timeouts` 这类）不在默认配置里，但可能被手工写进配置文件；
    # 重写配置时**必须原样保留**，否则一次 init 就会把它们悄悄抹掉。
    for key, value in cfg.items():
        if key in DEFAULT_CONFIG or key == "config_path":
            continue
        stored[key] = value
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(stored, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass          # 某些文件系统不支持改权限；不影响功能
    return path


def generate_token():
    """生成访问口令：够长的随机串，只出现二进制/大写小写数字与短横线。"""
    return secrets.token_urlsafe(36)


def token_fingerprint(token):
    """口令的**可打印指纹**：日志里只能出现这个，绝不出现口令本身。

    给出四个字符与长度，足够运维核对"是不是同一枚口令"，又不至于泄露。
    """
    token = token or ""
    if not token:
        return "（未设置）"
    return "前四位 %s…，共 %d 字符" % (token[:4], len(token))


def require_token(cfg):
    """启动前校验口令：缺失或太短一律拒绝启动（失败要关闭，不能敞开跑）。"""
    token = (cfg.get("token") or "").strip()
    if not token:
        raise RuntimeError(
            "未配置访问口令。请先执行 `hadoop/scripts/mcp_server.sh init` 生成，"
            "或设置环境变量 MCP_TOKEN")
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            "访问口令太短（%d 字符，至少 %d 字符）。请重新生成一个。"
            % (len(token), MIN_TOKEN_LENGTH))
    return token


def ensure_config(config_path=None, repo=None, create_token=False, rotate_token=False):
    """读配置；`create_token=True` 时缺口令就生成一枚并落盘；`rotate_token=True` 时**换一枚**。

    为什么要有"换口令"这条路：初始化默认是幂等的（保留旧口令，避免一次误执行把正在
    用的客户端全踢掉），可口令一旦怀疑泄露就必须能换。运行手册里曾写着"重来加 --force"，
    而入口并没有这个开关 —— 两边对不上。现在的约定是：`init --force` 换口令，
    手册按这个写。
    """
    cfg = load_config(config_path, repo)
    created = False
    has_token = bool((cfg.get("token") or "").strip())
    if rotate_token or (create_token and not has_token):
        cfg["token"] = generate_token()
        save_config(cfg, config_path, repo)
        created = True
    return cfg, created


# ---------------------------------------------------------------------------
# 调用上下文
# ---------------------------------------------------------------------------

def new_context(cfg, repo=None, started_at=None):
    """一次服务进程共享的调用上下文（仓库根、服务信息、启动时间、调优旋钮）。

    见 `context_config`：配置里的调优项必须**原样透传**进来，否则"配置文件里写了"
    与"处理器实际拿到的"会是两个值。
    """
    return dispatch.ToolContext(
        repo_root=repo or repo_root(),
        env=None,
        config=context_config(cfg),
        started_at=started_at or now_utc(),
    )


#: 允许从配置透传进调用上下文的键（**白名单**，其余一律不进上下文）。
#:
#: 为什么必须透传：统一调用通道（`driver_bridge`）与长任务层（`tasks`）都从
#: 上下文里读这些旋钮 —— 超时、驱动脚本与解释器位置、等待轮询间隔、取消宽限、
#: 占用状态里附带几条最近任务、同时允许几个等待。上下文是服务层构造的，服务层不
#: 透传，这些键在真实服务里就**永远不生效**（审查实测：配置写 0.05 秒实得 0.5 秒、
#: 写 0.001 秒超时实得 180 秒）。这类偏差最坏的地方在于：测试直接构造上下文注入
#: 这些值时全绿，而线上是另一条路。
CONTEXT_CONFIG_KEYS = (
    # 统一调用通道
    "driver_script", "driver_python", "driver_timeouts",
    # 长任务层
    "wait_poll_seconds", "cancel_grace_seconds", "cancel_kill_wait_seconds",
    "scheduler_recent_limit", "max_concurrent_waits",
)


def context_config(cfg):
    """从服务配置里挑出要透传给处理器的键；不在白名单里的一律不传。

    只透传**确实写了**的键（值为 None 视同没写），这样模块里的默认值仍然是唯一
    的默认来源，服务层不复制一份默认值（否则两处默认会各自漂移）。
    """
    cfg = cfg or {}
    out = {}
    for key in ("name", "version") + CONTEXT_CONFIG_KEYS:
        if key in cfg and cfg[key] is not None:
            out[key] = cfg[key]
    return out


def health_payload(ctx, cfg=None, started_ts=None):
    """健康检查的返回：**只回答存活、契约版本、启动时间与"入口开在哪"**，不回任何数据。

    这条道路不校验口令（用于探活），因此内容必须最小化 —— 它不该成为
    "不用口令就能看到系统内部"的口子。为什么还要给"入口"这一段：两条下载路由
    与健康检查本身都不在工具表里（`describe_capabilities` 不列它们），对端只能靠
    翻文档猜地址。这里给出**路径形状**（不含数据、不含口令、不含主机），
    让对端与运维一眼确认"服务把东西开在哪"，也顺便暴露"路径被改过"这件事。
    """
    cfg = cfg or {}
    payload = {
        "ok": True,
        "status": "ok",
        "service": cfg.get("name") or SERVER_NAME,
        "version": cfg.get("version") or SERVER_VERSION,
        "interface_version": contract.INTERFACE_VERSION,
        "started_at": ctx.started_at if ctx else None,
        "endpoints": {
            "protocol": cfg.get("streamable_http_path") or "/mcp",
            "health": cfg.get("health_path") or "/health",
            "download_task": DOWNLOAD_TASK_ROUTE,
            "download_published": DOWNLOAD_PUBLISHED_SHAPE,
            "download_model": DOWNLOAD_MODEL_ROUTE,
            "auth": "除健康检查外，一律需要请求头 Authorization: Bearer <口令>",
        },
    }
    if started_ts:
        payload["uptime_seconds"] = int(max(0, time.time() - started_ts))
    return payload


# ---------------------------------------------------------------------------
# 工具注册：按工具表合成函数签名（只为了"列工具"能看见参数形状）
# ---------------------------------------------------------------------------

#: 参数类型 → 对外展示的 JSON Schema 类型名。
SCHEMA_TYPES = {"string": "string", "int": "integer", "bool": "boolean", "enum": "string",
                # 对象参数（机器学习超参）：对外展示成 JSON 对象；运行时校验在分派层
                "object": "object"}


class ArgumentModel(object):
    """参数模型的基类占位（真正的父类在 `_argument_base()` 里按需取）。

    这里只声明"要什么"，不导入第三方包 —— 服务层顶层必须能在裸解释器下被导入，
    参数模型只在真正起服务时才需要。
    """


def _argument_base():
    """参数模型的基类：**收下所有键**，包括工具表里没声明的。

    为什么要单独造一个基类：官方包默认的参数模型会静默丢弃"没声明的键"。
    后果很具体 —— 对端把任务编号写成驼峰式（`taskId`）时，服务什么错都不报，
    直接按"缺少必填参数"或按默认值跑，用户看到的结果与意图不符，还查不出原因。
    所以：
      * 参数模型允许额外字段（`extra="allow"`）；
      * 并把额外字段一并交给函数（默认实现只导出声明字段，必须覆盖掉）。

    这样多余键会一路到达分派层的 `validate_args`，由它回"不认识这个参数，
    可用参数是……"的 `USAGE` 信封。代价是"列工具"返回的参数结构里会多出
    `additionalProperties: true` —— 我们宁可把"允许额外字段"写在明面上，
    也不要"看着不允许、实际上悄悄丢掉"。
    """
    from pydantic import ConfigDict
    from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase

    class _ArgumentModel(ArgModelBase):
        #: 额外字段保留下来，不丢弃
        model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

        def model_dump_one_level(self):
            """声明字段 + 额外字段一起交出去（默认实现只给声明字段）。"""
            out = super().model_dump_one_level()
            out.update(self.model_extra or {})
            return out

    return _ArgumentModel


def build_arguments_model(tool):
    """按工具表建一个"只负责收参数"的模型。

    字段类型一律宽松（`Any`）：类型、枚举、上下界这些判断**只在分派层做**，
    这样任何参数问题都会变成我们的 `USAGE` 信封，而不是协议层的一句异常。
    展示用的严格结构不从这里取（见 `build_input_schema`），两者分开更清楚。
    """
    from typing import Any

    from pydantic import create_model

    fields = {}
    for param in tool["params"]:
        default = None if param.get("required") else param.get("default")
        fields[param["name"]] = (Any, default)
    return create_model("%sArguments" % tool["name"],
                        __base__=_argument_base(), **fields)


def build_input_schema(tool):
    """按工具表生成"给对端看的"参数结构（类型、说明、枚举、上下界、默认值、必填）。

    这份结构是**展示**用的权威形状，与工具表逐条对应（测试会比对）。
    运行时校验并不用它，理由见 `_argument_base` 与 `build_arguments_model`。
    """
    properties = {}
    required = []
    for param in tool["params"]:
        prop = {"type": SCHEMA_TYPES.get(param["type"], "string"),
                "description": param.get("description") or ""}
        if param["type"] == "enum":
            prop["enum"] = list(param.get("values") or ())
        if param["type"] == "int":
            if param.get("minimum") is not None:
                prop["minimum"] = param["minimum"]
            if param.get("maximum") is not None:
                prop["maximum"] = param["maximum"]
        if param.get("default") is not None:
            prop["default"] = param["default"]
        if param.get("required"):
            required.append(param["name"])
        properties[param["name"]] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        # 服务端会收下未声明的键，再由分派层拒绝 —— 写在明面上，不让对端猜
        "additionalProperties": True,
    }


def tool_description(tool):
    """工具说明：长耗时工具要点明"这是长任务、大约多久"（要求文档 §3.3）。"""
    text = tool["description"]
    if tool.get("long_running"):
        text = "%s（长任务：预计约 %d 秒；受理后请用「查任务状态」或「等任务结束」跟进）" % (
            text, tool.get("estimated_seconds") or 0)
    return text


def make_tool_callable(tool, ctx):
    """把一条能力包成可调用的函数：执行只转一次分派层。

    这里**不做参数校验、不包信封** —— 那两件事都在分派层，只有一处实现。
    收到的键（含工具表里没声明的）原样转过去，让分派层统一判定并说明。
    函数永远返回字典（成功信封或失败信封），协议层会把它序列化进返回内容；
    绝不在这里抛异常，否则对端只能看到一句协议错误，看不到我们的错误码。
    """
    name = tool["name"]

    def _call(**kwargs):
        return dispatch.call_tool(name, kwargs, ctx)

    _call.__name__ = name
    _call.__qualname__ = "tool_%s" % name
    _call.__doc__ = tool["description"]
    return _call


def build_tool(tool, ctx):
    """组装一条协议层工具定义：展示结构 + 宽松参数模型 + 执行函数。"""
    from mcp.server.mcpserver.tools import Tool
    from mcp.server.mcpserver.utilities.func_metadata import FuncMetadata

    return Tool(
        fn=make_tool_callable(tool, ctx),
        name=tool["name"],
        title=tool["title"],
        description=tool_description(tool),
        parameters=build_input_schema(tool),
        fn_metadata=FuncMetadata(arg_model=build_arguments_model(tool)),
        is_async=False,
    )


def build_tools(ctx):
    """按工具表组装全部工具定义（交给服务对象一次性登记）。"""
    return [build_tool(tool, ctx) for tool in tools.TOOLS]


# ---------------------------------------------------------------------------
# 只读资源
# ---------------------------------------------------------------------------

def _envelope_json(tool_name, args, ctx):
    """调一条只读能力，把信封序列化成资源内容（资源与工具共用同一套判定）。"""
    return json.dumps(dispatch.call_tool(tool_name, args, ctx),
                      ensure_ascii=False, indent=2)


def _scheme_content_json(name, ctx):
    """读一份方案文件的完整内容（路径白名单复用读取层，不另写一套）。"""
    from . import readers

    relpath = "config/%s" % name
    try:
        path = readers.resolve_allowed_path(ctx, relpath)
        with open(path, encoding="utf-8") as fh:
            content = json.load(fh)
    except contract.StructuredError as exc:
        return json.dumps(exc.to_envelope(), ensure_ascii=False, indent=2)
    except ValueError as exc:
        payload = contract.envelope_error(
            contract.CODE_PRODUCT_INVALID, "方案文件不是合法 JSON：%s（%s）" % (name, exc),
            path=relpath)
        return json.dumps(payload, ensure_ascii=False, indent=2)
    payload = contract.envelope_ok(name=name, path=relpath, content=content)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def resource_specs(ctx):
    """只读资源清单：`(URI, 名称, 说明, 媒体类型, 取值函数)`。

    取值函数一律返回文本；内容统一是我们的信封 JSON，与工具返回同形 ——
    对端只需要写一套解析，越界与大小上限由读取层负责（这里不重复实现）。
    """
    return [
        ("ml://schemes", "schemes", "已登记方案列表（清洗规则与评分方案）",
         "application/json", lambda: _envelope_json("list_schemes", {}, ctx)),
        ("ml://scheme/{name}", "scheme_content", "单个方案的完整内容（供评分依据展示）",
         "application/json", lambda name: _scheme_content_json(name, ctx)),
        ("ml://published", "published_versions", "已发布的数据版本及其来源任务与文件大小",
         "application/json", lambda: _envelope_json("list_published_versions", {}, ctx)),
        ("ml://tasks", "tasks", "最近的任务列表（状态、开始时间、数据版本）",
         "application/json", lambda: _envelope_json("list_tasks", {}, ctx)),
        ("ml://task/{task_id}/status", "task_status", "某个任务的状态与进度",
         "application/json", lambda task_id: _envelope_json(
             "get_task_status", {"task_id": task_id}, ctx)),
        ("ml://task/{task_id}/files", "task_files", "某个任务目录下的产物文件清单",
         "application/json", lambda task_id: _envelope_json(
             "list_task_files", {"task_id": task_id}, ctx)),
        ("ml://task/{task_id}/report", "task_report",
         "某个任务的评估报告（全文，包在我们的信封里返回）",
         # 媒体类型必须与**实际返回的内容**一致：资源内容一律是信封 JSON
         # （报告正文放在 `report` 字段里），若声明成 Markdown，客户端会按 Markdown
         # 渲染这段 JSON，用户看到的就是一坨花括号。要拿纯 Markdown 正文走下载路由
         # （`report.md`），那里返回的是文件原文。
         "application/json", lambda task_id: _envelope_json(
             "get_report", {"task_id": task_id, "format": "md"}, ctx)),
        ("ml://models", "ml_models", "已登记的机器学习模型（编号、类型、算法、关键指标摘要）",
         "application/json", lambda: _envelope_json("list_ml_models", {}, ctx)),
        ("ml://model/{model_id}", "ml_model", "某个模型的元数据与指标（含产物清单）",
         "application/json", lambda model_id: _envelope_json(
             "get_ml_model", {"model_id": model_id}, ctx)),
        ("ml://task/{task_id}/quarantine", "task_quarantine",
         "某个任务隔离区的代表性样本（带命中规则与原因）",
         "application/json", lambda task_id: _envelope_json(
             "get_samples", {"task_id": task_id, "sample_type": "quarantine",
                             "table": "ratings", "limit": 10}, ctx)),
    ]


def register_resources(server, ctx):
    """注册只读资源（含按任务编号/方案文件名的模板资源）。"""
    for uri, name, description, mime_type, fn in resource_specs(ctx):
        server.resource(uri, name=name, description=description,
                        mime_type=mime_type)(fn)
    return tuple(spec[0] for spec in resource_specs(ctx))



# ---------------------------------------------------------------------------
# 只读下载路由（普通 HTTP GET，流式返回；不读进内存、不塞进 JSON）
# ---------------------------------------------------------------------------

#: 任务产物下载：任务编号 + 任务目录内的相对路径。
DOWNLOAD_TASK_ROUTE = "/download/task/{task_id}/{path:path}"
#: 已发布数据下载：数据版本 + 表名（只允许三张表）。
#: 注册用的实际形状是"兜底一段"（`{rest:path}`），自己再拆成两半 ——
#: 这样像 `../..` 这种畸形版本也会落到我们的判定里，回结构化错误而不是框架的 404。
DOWNLOAD_PUBLISHED_ROUTE = "/download/published/{rest:path}"
#: 对外文档里给对端看的形状（与上面注册的形状不同，这里给的是"该怎么写"）。
DOWNLOAD_PUBLISHED_SHAPE = "/download/published/{data_version}/{table}"
#: 模型产物下载：模型编号 + 模型目录内的相对路径（可多级）。
#: 形状与数据契约 §4.1 一致，对端按它接线。
DOWNLOAD_MODEL_ROUTE = "/download/model/{model_id}/{path:path}"

#: 数据版本允许的字符形状：字母或数字开头，其后字母数字与 `.` `_` `-`，最长 64。
#: 这一条是"不把用户字符串拼进命令"的第一道闸：路径分隔符、上级跳转、空白、
#: 引号、通配符全部进不来。第二道闸是命令一律以参数列表传入（见 `_hdfs_command`）。
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: 流式返回的分块大小（1 MB）。数据表三十多兆，分块读就不占内存。
STREAM_CHUNK_BYTES = 1 << 20

#: 下载失败时的 HTTP 状态：错误码仍是我们的那一套，状态码只是给 HTTP 层看的。
DOWNLOAD_STATUS_BY_CODE = {
    CODE_FORBIDDEN_HOST: 403,
    CODE_UNAUTHORIZED: 401,
    contract.CODE_FORBIDDEN_PATH: 400,
    contract.CODE_USAGE: 400,
    contract.CODE_FILE_NOT_FOUND: 404,
    contract.CODE_PRODUCT_INVALID: 404,
    contract.CODE_TASK_NOT_FOUND: 404,
    contract.CODE_DRIVER_UNREACHABLE: 503,
    contract.CODE_DRIVER_TIMEOUT: 504,
}


def download_routes():
    """下载路由的三条路径（`status` 自检里打印，装机时确认开在哪）。"""
    return {"task": DOWNLOAD_TASK_ROUTE, "published": DOWNLOAD_PUBLISHED_SHAPE,
            "model": DOWNLOAD_MODEL_ROUTE}


def host_is_allowed(host, allowed_hosts):
    """Host 头是否放行 —— 与协议入口用的那份允许列表**同一套判断**。

    规则与协议层一致：整串精确匹配，或匹配「`基础地址:*`」这种带端口的通配写法。
    为什么下载路由要自己判一遍：协议层那份判断挂在协议入口上，普通 GET 路由不走它；
    而下载是另一条能读到数据内容的路，Host 校验不能比协议入口松。
    """
    host = (host or "").strip()
    if not host:
        return False
    for allowed in allowed_hosts or ():
        text = str(allowed).strip()
        if not text:
            continue
        if host == text:
            return True
        if text.endswith(":*") and host.startswith(text[:-2] + ":"):
            return True
    return False


def split_published_path(rest):
    """把兜底那一段拆成"数据版本 + 表名"（拆不出就报用法错误）。"""
    text = str(rest or "").replace("\\", "/").strip("/")
    if "/" not in text:
        raise contract.StructuredError(
            contract.CODE_USAGE,
            "下载地址需要数据版本与表名，形如 %s" % DOWNLOAD_PUBLISHED_SHAPE)
    return tuple(text.rsplit("/", 1))


def parse_published_target(data_version, table):
    """校验"数据版本 + 表名"，返回 `(版本, 落盘文件名)`。

    两边都做白名单：版本必须形如安全标识（见 `_VERSION_PATTERN`），表名只能是三张表之一。
    不合法一律 `USAGE`，并把可选值写进说明。
    """
    from . import readers

    version = str(data_version or "").strip()
    if not _VERSION_PATTERN.match(version):
        raise contract.StructuredError(
            contract.CODE_USAGE,
            "数据版本必须是安全的标识（字母或数字开头，可含 . _ -，最长 64 字符），"
            "收到 %r" % (data_version,),
            param="data_version")
    tables = sorted(readers.TABLE_FILES)
    name = str(table or "").strip()
    if name not in tables:
        raise contract.StructuredError(
            contract.CODE_USAGE,
            "表名只能是 %s，收到 %r" % ("、".join(tables), table),
            param="table")
    return version, readers.TABLE_FILES[name]


def _exclusion_kind(reason):
    """把读取层给的排除原因翻成稳定标签；没登记就原样带出去，不丢信息。

    **映射只有一份**：直接用读取层的 `EXCLUSION_KINDS`。此前这里自己维护过一份，
    取值写成 `control_file` / `filesystem_sidecar` / `temp_residue`，与读取层的
    `control` / `sidecar` / `residue` 形成"同一个字段、两套值"——对端看同一个
    `reason_kind` 会拿到两套词汇，且没有任何提示能发现这件事。收敛成一份之后，
    改名也只会改一处。
    """
    return readers.EXCLUSION_KINDS.get(reason, reason)


def downloadable_task_file(ctx, task_id, relpath):
    """任务产物下载的判定：返回 `(真实路径, 字节数)`，不合法就抛结构化错误。

    判定顺序是刻意的，四步各管一件事：

    1. **越界**（`..`、绝对路径、指向根外的符号链接）→ `FORBIDDEN_PATH`。
       复用读取层的 `resolve_under`，与"读产物"完全同一条判定。
    2. **是不是可以对外提供的产物** → `FORBIDDEN_PATH`（与读取层同一套码，不另造）。
       控制文件、隐藏文件、校验和附属物、流程残留即使真的存在也不对外，所以它们拿到
       的是"不允许"，而不是"不存在"（后者会让人去查文件是不是被删了）。
       **请求里的路径与解析后的真实路径都要判**：只判名字时，一个改过名的符号链接
       指向控制文件，读取会拒、下载却放行 —— 两条路必须给出同一个结论。
    3. **在不在** → `FILE_NOT_FOUND`。
    4. **是不是普通文件** → 目录报"没有可下载的文件"，管道/设备这类特殊文件报
       `FORBIDDEN_PATH`：对它们调用读取会**挂住**（没有数据也没有结束），
       把服务拖死，所以必须在打开之前就拦住。
    """
    from . import readers

    text = str(relpath or "").replace("\\", "/").strip()
    base = readers.task_dir(ctx, task_id)
    # 第 1 步：越界（顺序上必须在"排除"之前：先回答"能不能碰这个位置"）
    target = readers.resolve_under(base, text, must_exist=False)

    # 第 2 步：排除判定 —— 请求路径与真实路径各判一次
    real_rel = os.path.relpath(target, os.path.realpath(base)).replace(os.sep, "/")
    checked = [text]
    if real_rel not in ("", ".", "..") and not real_rel.startswith("../"):
        if real_rel != text:
            checked.append(real_rel)
    for candidate in checked:
        reason = readers.exclusion_reason(candidate)
        if reason:
            raise contract.StructuredError(
                contract.CODE_FORBIDDEN_PATH,
                "该文件不对外提供下载：%s（%s）" % (text, reason),
                path=text, reason_kind=_exclusion_kind(reason),
                matched_path=candidate)

    # 第 3 步：在不在
    if not os.path.exists(target):
        raise contract.StructuredError(
            contract.CODE_FILE_NOT_FOUND, "文件不存在：%s" % text, path=text)

    # 第 4 步：是不是普通文件（先判再打开，避免管道/设备把服务挂住）
    info = os.stat(target)
    if stat.S_ISDIR(info.st_mode):
        raise contract.StructuredError(
            contract.CODE_FILE_NOT_FOUND,
            "这个路径是一个目录，没有可下载的文件：%s（先用「列任务产物文件」看看里面有什么）"
            % text, path=text, reason_kind="directory")
    if not stat.S_ISREG(info.st_mode):
        raise contract.StructuredError(
            contract.CODE_FORBIDDEN_PATH,
            "该路径不是普通文件，不对外提供下载：%s" % text,
            path=text, reason_kind="not_regular_file")
    return target, info.st_size


def downloadable_model_file(ctx, model_id, relpath):
    """模型产物下载的判定：返回 `(真实路径, 字节数)`，不合法就抛结构化错误。

    判定顺序与任务产物下载**完全一致**（越界 → 不该外发 → 在不在 → 是不是普通文件），
    只把根目录从"任务目录"换成"该模型的目录"：同一类问题必须给出同一个结论，
    否则会出现"读产物拒绝、下载却放行"这种自相矛盾。

    模型目录来自 `mcpserver.ml.model_directory`：编号形状与路径逃逸都在那里判过
    （单层编号 + 真实路径必须落在模型根之内），这里不另立规则。
    """
    from . import ml, readers

    text = str(relpath or "").replace("\\", "/").strip()
    base = ml.model_directory(ctx, model_id)
    target = readers.resolve_under(base, text, must_exist=False)

    real_rel = os.path.relpath(target, os.path.realpath(base)).replace(os.sep, "/")
    checked = [text]
    if real_rel not in ("", ".", "..") and not real_rel.startswith("../"):
        if real_rel != text:
            checked.append(real_rel)
    for candidate in checked:
        reason = readers.exclusion_reason(candidate)
        if reason:
            raise contract.StructuredError(
                contract.CODE_FORBIDDEN_PATH,
                "该文件不对外提供下载：%s（%s）" % (text, reason),
                path=text, reason_kind=_exclusion_kind(reason), matched_path=candidate)

    if not os.path.exists(target):
        raise contract.StructuredError(
            contract.CODE_FILE_NOT_FOUND, "文件不存在：%s" % text, path=text,
            model_id=model_id)
    info = os.stat(target)
    if stat.S_ISDIR(info.st_mode):
        raise contract.StructuredError(
            contract.CODE_FILE_NOT_FOUND,
            "这个路径是一个目录，没有可下载的文件：%s（先用「取模型元数据」看产物清单）"
            % text, path=text, reason_kind="directory")
    if not stat.S_ISREG(info.st_mode):
        raise contract.StructuredError(
            contract.CODE_FORBIDDEN_PATH,
            "该路径不是普通文件，不对外提供下载：%s" % text,
            path=text, reason_kind="not_regular_file")
    return target, info.st_size


#: 按扩展名给的媒体类型（数据契约 §4.1 写死了这份对应关系）。
MODEL_MEDIA_TYPES = {
    ".json": "application/json",
    ".csv": "text/csv",
    ".jsonl": "application/x-ndjson",
    ".md": "text/markdown",
}


def model_media_type(filename):
    """模型产物的媒体类型；未登记的一律给二进制流（不猜内容）。"""
    ext = os.path.splitext(str(filename or ""))[1].lower()
    return MODEL_MEDIA_TYPES.get(ext, "application/octet-stream")


def published_file_path(ctx, version, filename):
    """发布区里某个文件的完整路径（版本与文件名都已通过白名单校验）。"""
    from . import readers

    base = "%s/%s" % (readers.hdfs_base(ctx).rstrip("/"), readers.PUBLISHED_SUBDIR)
    return "%s/%s/%s" % (base, version, filename)


def published_file_size(ctx, hdfs_path):
    """发布区文件的字节数；连"在不在"的判断也在这里做。

    为什么用 `-stat` 而不是 `-test -e`：`-test` **成功失败都不输出任何东西**，
    于是"文件不存在"和"连不上集群"在它这里长得一模一样 —— 而这两件事的处理
    完全不同（一个该让人去核对版本，一个该让人去启动集群）。`-stat` 会把
    原因写在错误输出里，正好用来分辨。
    """
    from . import readers

    code, out, err = readers.run_hdfs(["dfs", "-stat", "%b", hdfs_path], ctx)
    if code == 0:
        text = (out or "").strip()
        if text.isdigit():
            return int(text)
        # 命令成功但没给出字节数：不编数字，让 HTTP 层退回分块传输
        return None
    if readers.looks_like_missing(err):
        raise contract.StructuredError(
            contract.CODE_FILE_NOT_FOUND,
            "发布区里没有这个文件：%s（这个数据版本可能还没发布过，或表名不对）"
            % hdfs_path, path=hdfs_path)
    raise contract.StructuredError(
        contract.CODE_DRIVER_UNREACHABLE,
        "连不上集群，无法读取发布区：%s。集群可能没启动；这不是「文件不存在」"
        % hdfs_path, path=hdfs_path, stderr=(err or "").strip()[-500:] or None)


def stream_local_file(path, chunk_size=STREAM_CHUNK_BYTES):
    """把本地文件按块读出来（生成器：一次只在内存里放一块）。"""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk


def hdfs_command(ctx, argv):
    """拼出一次集群命令的调用方式：返回 `(command, env, cwd)`。

    下载是流式的（数据表三十多兆，不能整份读进内存再吐），必须自己起子进程按块读；
    但"命令怎么拼、环境怎么来"**只有一份实现** —— 直接转调读取层的 `hdfs_command`。
    此前这里自己复制了一份（连固定英文报错都抄了），属于"同一件事两处实现"：
    两条路会各自漂移，拿不同的 `PATH` 与默认值，排障时分不清是集群的问题还是我们
    带错了环境。收敛之后，改命令形态只需改一处。
    """
    return readers.hdfs_command(ctx, argv)


def stream_published_file(ctx, hdfs_path, chunk_size=STREAM_CHUNK_BYTES):
    """把集群里的发布文件流式吐出来；对端断开时把子进程收掉，不留孤儿。

    错误输出**不能接管道**：管道写满（内核缓冲约 64KB）之后子进程会阻塞在写日志上，
    而我们在读它的标准输出 —— 两边互相等，下载就挂住了。作业告警多的时候很容易写满。
    这里把它重定向到一个临时文件：既不会堵，故障时又拿得到原因（比丢进 `/dev/null` 好，
    因为"为什么读不到"正是排障最需要的）。临时文件随用完关闭，不落在任何目录里。
    """
    command, env, cwd = hdfs_command(ctx, ["dfs", "-cat", hdfs_path])
    with tempfile.TemporaryFile() as errlog:
        proc = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=errlog)
        try:
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.stdout.close()
            except Exception:                      # noqa: BLE001 —— 收尾不掩盖主流程
                pass
            if proc.poll() is None:
                proc.kill()
            code = proc.wait()

        if code != 0:
            errlog.seek(0)
            detail = errlog.read().decode("utf-8", "replace").strip()
            # 走到这里说明响应已经开始发了（长度头也发过），没法再改状态码；
            # 如实抛出，让连接中断、对端按"长度对不上"发现这次下载不完整。
            raise RuntimeError(
                "读取发布区文件失败（退出码 %s）：%s"
                % (code, (detail[-500:] if detail else "没有错误输出")))


def download_error_response(exc):
    """把结构化错误转成下载失败响应（JSON，带我们的错误码）。"""
    from starlette.responses import JSONResponse

    status = DOWNLOAD_STATUS_BY_CODE.get(exc.code, 400)
    return JSONResponse(exc.to_envelope(), status_code=status)


def attachment_headers(filename, size=None):
    """下载响应头：文件名 + （已知时）长度。

    文件名同时给普通写法与 UTF-8 写法：中文名的产物在部分客户端上只认后者。
    """
    from urllib.parse import quote

    safe = os.path.basename(str(filename or "download")).replace("\"", "_")
    ascii_name = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
    headers = {
        "Content-Disposition": 'attachment; filename="%s"; filename*=UTF-8\'\'%s'
                               % (ascii_name, quote(safe)),
    }
    if size is not None:
        headers["Content-Length"] = str(int(size))
    return headers


def _download_host_guard(request, cfg):
    """下载路由的 Host 校验（与协议入口一致）；不通过就抛结构化错误。"""
    allowed = effective_allowed_hosts(cfg)
    host = request.headers.get("host")
    if not host_is_allowed(host, allowed):
        raise contract.StructuredError(
            CODE_FORBIDDEN_HOST,
            "这个 Host 不在允许列表里：%r。允许的是：%s" % (host, "、".join(allowed)),
            host=host)
    return host


def register_download_routes(server, cfg, ctx):
    """注册三条只读下载路由（任务产物 / 已发布数据 / 模型产物）。"""
    from starlette.responses import StreamingResponse

    def task_file(request):
        try:
            _download_host_guard(request, cfg)
            task_id = request.path_params.get("task_id")
            relpath = request.path_params.get("path")
            target, size = downloadable_task_file(ctx, task_id, relpath)
        except contract.StructuredError as exc:
            return download_error_response(exc)
        return StreamingResponse(
            stream_local_file(target),
            media_type="application/octet-stream",
            headers=attachment_headers(os.path.basename(target), size))

    def model_file(request):
        try:
            _download_host_guard(request, cfg)
            model_id = request.path_params.get("model_id")
            relpath = request.path_params.get("path")
            target, size = downloadable_model_file(ctx, model_id, relpath)
        except contract.StructuredError as exc:
            return download_error_response(exc)
        return StreamingResponse(
            stream_local_file(target),
            media_type=model_media_type(target),
            headers=attachment_headers(os.path.basename(target), size))

    def published_file(request):
        try:
            _download_host_guard(request, cfg)
            version, table = split_published_path(request.path_params.get("rest"))
            version, filename = parse_published_target(version, table)
            hdfs_path = published_file_path(ctx, version, filename)
            size = published_file_size(ctx, hdfs_path)
        except contract.StructuredError as exc:
            return download_error_response(exc)
        return StreamingResponse(
            stream_published_file(ctx, hdfs_path),
            media_type="application/octet-stream",
            headers=attachment_headers(filename, size))

    server.custom_route(DOWNLOAD_TASK_ROUTE, ["GET"], name="download_task")(task_file)
    server.custom_route(DOWNLOAD_MODEL_ROUTE, ["GET"], name="download_model")(model_file)
    server.custom_route(DOWNLOAD_PUBLISHED_ROUTE, ["GET"], name="download_published")(published_file)
    return download_routes()


# ---------------------------------------------------------------------------
# 鉴权（纯 ASGI，标准库实现：不依赖 starlette，便于单测）
# ---------------------------------------------------------------------------

class BearerAuthMiddleware(object):
    """口令中间件：协议请求必须带 `Authorization: Bearer <口令>`。

    为什么自己写而不是用框架的：这一层要在**没有任何第三方包**的环境里也能被单测
    （仓库统一测试入口用系统解释器）。纯 ASGI 实现只需要读请求头、写一段 JSON。

    豁免：健康检查那一条道路（探活不该需要口令）。除它之外一律校验，
    缺失或不符返回 401 与明确的 JSON，**不静默放行、不返回 500**。
    """

    #: 401 返回的错误码（与 `DOWNLOAD_STATUS_BY_CODE` 共用顶层那个常量）。
    CODE_UNAUTHORIZED = CODE_UNAUTHORIZED

    def __init__(self, app, token, exempt_paths=(), realm="ml-mcp"):
        self.app = app
        self.token = token or ""
        self.exempt_paths = tuple(self._normalize(path) for path in exempt_paths)
        self.realm = realm

    @staticmethod
    def _normalize(path):
        path = path or "/"
        return path.rstrip("/") or "/"

    def is_exempt(self, path):
        """该路径是否免口令（只认健康检查那一条）。"""
        return self._normalize(path) in self.exempt_paths

    @staticmethod
    def extract_bearer(scope):
        """从 ASGI 请求头里取 Bearer 口令；没有返回 None。"""
        for key, value in scope.get("headers") or []:
            if key.decode("latin-1").lower() != "authorization":
                continue
            text = value.decode("latin-1").strip()
            head, _, rest = text.partition(" ")
            if head.lower() != "bearer":
                return None
            return rest.strip() or None
        return None

    def is_valid(self, provided):
        """口令比对：**先归一到字节**，再做定长比较（避免按字节提前返回的时间差异）。

        为什么必须归一：`hmac.compare_digest` 只接受纯 ASCII 的字符串或字节串。
        收到带非 ASCII 的口令（对端复制时混进了中文标点、全角字符，或故意构造）
        而直接比较字符串，它会抛 `TypeError` —— 于是"口令不对"变成了"服务出错"，
        回的是 500，还顺手给了对方一个刷错误日志的口子。

        归一方式（两种都可能出现，先试保真、再试文本）：
          * 从请求头来的字符串是 latin-1 解码的结果，**逐字节保真** —— 用 latin-1 编回去
            就能还原对端发来的原始字节；
          * 直接以 Python 文本传进来（测试、或将来别的调用方）时，latin-1 编不了非 ASCII，
            退回 UTF-8。
        配置里的口令一律按 UTF-8 编码。两者相等才算通过；任何编码失败视为"口令不对"。
        """
        if not provided or not self.token:
            return False
        try:
            if isinstance(provided, bytes):
                provided_bytes = provided
            else:
                try:
                    provided_bytes = str(provided).encode("latin-1")
                except UnicodeError:
                    provided_bytes = str(provided).encode("utf-8")
        except (UnicodeError, TypeError):
            return False
        try:
            token_bytes = str(self.token).encode("utf-8")
        except (UnicodeError, TypeError):
            return False
        try:
            return hmac.compare_digest(provided_bytes, token_bytes)
        except TypeError:                          # 理论上到不了；防御性兜底
            return False

    def reject_body(self):
        """401 的 JSON 内容：说清"缺什么、怎么带"，不回显收到的口令。"""
        payload = {
            "ok": False,
            "interface_version": contract.INTERFACE_VERSION,
            "error": {
                "code": self.CODE_UNAUTHORIZED,
                "message": "缺少或错误的访问口令：请在请求头带上 Authorization: Bearer <口令>",
                "realm": self.realm,
            },
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def _reject(self, send, body):
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", ('Bearer realm="%s"' % self.realm).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or "/"
        if self.is_exempt(path):
            return await self.app(scope, receive, send)
        if self.is_valid(self.extract_bearer(scope)):
            return await self.app(scope, receive, send)
        return await self._reject(send, self.reject_body())


# ---------------------------------------------------------------------------
# 组装与启动（这里才开始依赖官方包）
# ---------------------------------------------------------------------------

def create_server(cfg, ctx):
    """组装协议服务对象：注册工具、资源与健康检查道路。"""
    from mcp.server.mcpserver import MCPServer

    # 处理器清单由分派层统一给出（含机器学习那一段），这里不再自己拼接。
    dispatch.load_handlers()
    server = MCPServer(
        name=cfg.get("name") or SERVER_NAME,
        version=cfg.get("version") or SERVER_VERSION,
        description="数据治理能力（校验、清洗、评分、发布与产物取证），由 Hadoop 侧执行。",
        tools=build_tools(ctx),
    )
    register_resources(server, ctx)
    _register_health_route(server, cfg, ctx)
    register_download_routes(server, cfg, ctx)
    return server


def _register_health_route(server, cfg, ctx):
    """健康检查：独立、免口令、内容最小化，但**Host 校验不能省**。

    为什么免口令的道路也要判 Host：协议入口与两段下载都做了 Host 白名单，只有这里
    不做的话，一个陌生 Host 的请求就能拿到 200。它能读到的只是"存活、版本、运行时长、
    入口形状"，但那已经是一条"能读到东西的路"——原则是**能读到东西的路都不比协议入口松**。
    顺带还有个实际作用：端口转发配错时，健康检查会直接给出 403 而不是 200，
    一眼就能看出是 Host 被挡了，而不是服务没起。
    """
    from starlette.responses import JSONResponse

    started_ts = time.time()

    def health(request):
        allowed = effective_allowed_hosts(cfg)
        host = request.headers.get("host")
        if not host_is_allowed(host, allowed):
            return download_error_response(contract.StructuredError(
                CODE_FORBIDDEN_HOST,
                "这个 Host 不在允许列表里：%r。允许的是：%s"
                % (host, "、".join(allowed)), host=host))
        return JSONResponse(health_payload(ctx, cfg, started_ts))

    server.custom_route(cfg.get("health_path") or "/health", ["GET"], name="health")(health)


def build_app(config=None, config_path=None, repo=None):
    """组装可用于启动的应用：协议应用 + 口令中间件。

    返回 `(app, cfg, ctx)`。口令缺失、或健康检查路径不合法时**直接报错**，
    绝不启动一个敞开的服务（后者详见 `validate_health_path` 的说明）。
    """
    from mcp.server.transport_security import TransportSecuritySettings

    cfg = config or load_config(config_path, repo)
    require_token(cfg)
    # 健康检查免口令，它的路径必须是一个具体的、不等于 / 的路径 —— 否则等于免掉全部。
    cfg["health_path"] = validate_health_path(
        cfg.get("health_path") or "/health", cfg.get("streamable_http_path") or "/mcp")
    ctx = new_context(cfg, repo)
    server = create_server(cfg, ctx)

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(cfg.get("enable_dns_rebinding_protection", True)),
        allowed_hosts=effective_allowed_hosts(cfg),
        allowed_origins=list(cfg.get("allowed_origins") or []),
    )
    app = server.streamable_http_app(
        streamable_http_path=cfg.get("streamable_http_path") or "/mcp",
        host=cfg.get("host") or "0.0.0.0",
        transport_security=security,
        json_response=bool(cfg.get("json_response", False)),
        stateless_http=bool(cfg.get("stateless_http", False)),
    )
    app.add_middleware(BearerAuthMiddleware, token=cfg["token"],
                       exempt_paths=(cfg.get("health_path") or "/health",))
    return app, cfg, ctx


def readiness_report():
    """就绪情况：已接上的能力数、总数、以及还没接上的能力名。

    有未就绪能力时**点名**，不隐瞒 —— 那通常意味着另一段开发还没落地。
    """
    loaded = dispatch.available_tools()
    missing = [name for name in tools.tool_names() if name not in loaded]
    return {"ready": len(loaded), "total": len(tools.TOOLS), "missing": missing}


def serve(config_path=None, repo=None, log_level="info"):
    """常驻启动：按配置监听，等信号退出。

    三条运维须知（写在代码里，免得只活在文档里被忽略）：

    1. **访问日志与会话会持续堆积。** 每次协议请求都有一条访问日志；对端若每次调用都
       新建会话，服务端会累积会话状态。对端的正确做法是**复用同一个会话**；运维侧要
       按需轮转 `var/mcp/server.log`（脚本已把日志集中在那一处）。
    2. **明文传输。** 这里没有 TLS：口令与内容都是明文。它的使用范围只能是
       "宿主机 → 虚拟机的那条本机端口转发"，**不要**把它暴露到局域网或公网。
       需要跨网络使用时，务必套一层加密隧道（或改桥接后加反向代理做 TLS 终止）。
    3. **会话上限取官方实现的默认值**（同时存在的会话数、空闲超时都由它决定，
       当前没有改）。并发很高时可调，但调之前先确认为什么会有那么多并发会话 ——
       绝大多数情况是对端没复用会话。
    """
    import logging

    import uvicorn

    logging.basicConfig(
        level=getattr(logging, str(log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, cfg, _ctx = build_app(config_path=config_path, repo=repo)
    report = readiness_report()
    logger = logging.getLogger("mcpserver")
    logger.info("能力已就绪 %d/%d%s", report["ready"], report["total"],
                "" if not report["missing"] else "，未就绪：%s" % "、".join(report["missing"]))
    logger.info("监听 %s:%s，协议路径 %s，健康检查 %s", cfg["host"], cfg["port"],
                cfg["streamable_http_path"], cfg["health_path"])
    logger.info("下载路由 %s 与 %s", DOWNLOAD_TASK_ROUTE, DOWNLOAD_PUBLISHED_SHAPE)
    logger.info("访问口令%s（口令本身不写日志）", token_fingerprint(cfg["token"]))
    logger.warning("本服务是明文传输（无 TLS）：只应在宿主机到虚拟机的本机端口转发范围内使用")
    uvicorn.run(app, host=cfg["host"], port=int(cfg["port"]), log_level=log_level)
    return 0
