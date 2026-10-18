# Agent 侧接入远端 Hadoop 能力服务 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agent 的能力调用从「本机 driver 子进程」切到「远端 Hadoop 能力服务」（标准协议、HTTP 入口 `/mcp`、Bearer 口令），只保留「快速演示清洗」在本机；同步消除两端参数名/错误码/状态/编号/下载的语义差异。

**Architecture:** 新增一个真实的 `McpClient` 实现（官方 mcp SDK + httpx，连 `/mcp`），由现有的 `RoutingBackend` 按工具「来源」字段路由；远端能力全部标 `source=mcp`，本机只剩 `quick_clean_demo`。二进制产物下载走能力服务的两段只读路由，由 Agent 带口令取回后**流式**转给浏览器。长任务沿用既有后台轮询线程，只把探针的查状态/取结果改成远端调用。

**Tech Stack:** Python 3、标准库、现有依赖 httpx（已装）、新增 `mcp`（官方客户端库，随服务端版本）。

## Global Constraints

- **契约版本两端同为 `1.0`**；只做加性变更（新增字段/工具/错误码不升版本）。
- **口令**用请求头 `Authorization: Bearer <口令>` 传递；**绝不写入日志、信封或任何用户可见输出**。
- **双编号纪律**：所有后续远端调用（查状态、取结果、取样例、取报告、取消、产物清单、下载）一律用 **`hadoop_task_id`**；`agent_task_id` 只用于界面展示与排障对照。
- **如实转述失败**：直接使用信封里的 `error.message`，不改写成别的结论；保留 `error.code` 与 `driver_code` 供排障。
- **长任务一律异步受理**：发起后立即返回任务编号，不在调用里等它跑完；进度**只来自查状态**，不用时间估算。
- **只有「快速演示清洗」留本机**；其余共享能力走远端。
- **用户可见文案**（含中文错误说明、状态名）用自然语言描述场景，**不得**出现「冒烟」「对账」「口径」等词。
- 远端地址：虚拟机固定 `8765`，宿主机侧本轮用 `8900`（端口转发 宿主 8900 → 虚拟机 8765）。

---

## 文件结构

**新增**
- `agent/mcp_config.py` — 远端连接配置（地址、口令、超时、接口版本），仿 `agent/llm_config.py`。
- `agent/mcp_http_client.py` — 真实 `McpClient` 实现（列工具/调工具/查状态/健康/版本/关闭）。
- `agent/mcp_tools.py` — 远端工具表（22 项，`source=mcp`）与来源改写。
- `agent/mcp_download.py` — 两段下载路由的带口令流式取回。

**修改**
- `agent/mcp_contract.py` — 扩充结构化错误码集合与 `reason_kind`。
- `agent/tool_specs.py` — 工具来源改写（除快速演示外全部 `mcp`）、参数名对齐。
- `agent/tools.py` — 参数名与默认值对齐、等待终态含中止、原始数据上传相关下线。
- `agent/resources.py` — 原始数据上传下线、列表/预览/下载改为远端来源。
- `agent/runtime/tool_exec.py` — MCP 后端注入真实客户端。
- `agent/runtime/long_task.py` — 探针改为远端查状态/取结果；完成判定含中止。
- `agent/runtime/runtime.py` — 注入客户端。
- `agent/http_api.py` — 路由（上传下线、下载中转、health 暴露远端状态）。
- `agent/explain.py` — 状态中文映射补「已中止」。
- `requirements.txt` / `start_backend.bat` — 新增依赖与远端环境变量。

**测试**
- `agent/tests/test_mcp_config.py`、`test_mcp_client.py`、`test_mcp_tools.py`、`test_mcp_download.py`，并改造既有 `test_stage8_mcp_seam.py`、`test_tool_specs.py`、`test_resources_api.py`。

---

### Task 1: 远端连接配置

**Files:**
- Create: `agent/mcp_config.py`
- Test: `agent/tests/test_mcp_config.py`

**Interfaces:**
- Produces: `McpConfig`（属性 `url:str`、`token:str`、`timeout_sec:float`、`interface_version:str`、`usable:bool`、`health_url()`、`protocol_url()`、`safe_dict()`）、`load(overrides=None) -> McpConfig`。

- [ ] **Step 1: 写失败测试**

```python
# agent/tests/test_mcp_config.py
from agent import mcp_config


def test_defaults_unusable_without_url_and_token(monkeypatch):
    for k in ("AGENT_MCP_URL", "AGENT_MCP_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    cfg = mcp_config.load()
    assert cfg.usable is False
    assert cfg.interface_version == "1.0"


def test_env_overrides_and_usable(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_URL", "http://127.0.0.1:8900/mcp")
    monkeypatch.setenv("AGENT_MCP_TOKEN", "x" * 20)
    cfg = mcp_config.load()
    assert cfg.usable is True
    assert cfg.protocol_url() == "http://127.0.0.1:8900/mcp"
    assert cfg.health_url() == "http://127.0.0.1:8900/health"


def test_safe_dict_hides_token(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_URL", "http://127.0.0.1:8900/mcp")
    monkeypatch.setenv("AGENT_MCP_TOKEN", "secret-token-value")
    d = mcp_config.load().safe_dict()
    assert "secret-token-value" not in str(d)
    assert d["token_set"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest agent/tests/test_mcp_config.py -v`
Expected: FAIL（`ModuleNotFoundError: agent.mcp_config`）

- [ ] **Step 3: 实现**

```python
# agent/mcp_config.py
# -*- coding: utf-8 -*-
"""远端 Hadoop 能力服务的连接配置（标准协议入口 /mcp + Bearer 口令）。

配置来源（后者覆盖前者）：
  1) config/mcp.settings.json（可选，不建议入库）
  2) 环境变量 AGENT_MCP_*

红线：口令不落日志；默认不可用（地址或口令缺一即视为未接入）。
"""

import json
import os

from .mcp_contract import MCP_INTERFACE_VERSION

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(os.path.dirname(_HERE), "config", "mcp.settings.json")

ENV = {
    "url": "AGENT_MCP_URL",
    "token": "AGENT_MCP_TOKEN",
    "timeout_sec": "AGENT_MCP_TIMEOUT",
    "interface_version": "AGENT_MCP_INTERFACE_VERSION",
}

DEFAULT_TIMEOUT = 15.0
MAX_TIMEOUT = 120.0


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class McpConfig(object):
    def __init__(self, raw=None):
        d = dict(raw or {})
        self.url = (d.get("url") or "").strip()
        self.token = (d.get("token") or "").strip()
        self.timeout_sec = min(_as_float(d.get("timeout_sec"), DEFAULT_TIMEOUT), MAX_TIMEOUT)
        self.interface_version = (d.get("interface_version") or MCP_INTERFACE_VERSION).strip()

    @property
    def usable(self):
        return bool(self.url and self.token)

    def _base(self):
        base = self.url
        for suffix in ("/mcp", "/"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        return base

    def protocol_url(self):
        return self._base() + "/mcp"

    def health_url(self):
        return self._base() + "/health"

    def safe_dict(self):
        """脱敏视图：不含口令。"""
        return {"url": self.url or None, "token_set": bool(self.token),
                "timeout_sec": self.timeout_sec,
                "interface_version": self.interface_version, "usable": self.usable}


def _read_file():
    if not os.path.isfile(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def load(overrides=None):
    raw = {}
    raw.update(_read_file())
    for key, name in ENV.items():
        val = os.environ.get(name)
        if val not in (None, ""):
            raw[key] = val
    if isinstance(overrides, dict):
        raw.update(overrides)
    return McpConfig(raw)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest agent/tests/test_mcp_config.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add agent/mcp_config.py agent/tests/test_mcp_config.py
git commit -m "feat(agent): 远端能力服务连接配置（地址/口令/超时，口令不落日志）"
```

---

### Task 2: 真实 MCP 客户端

**Files:**
- Create: `agent/mcp_http_client.py`
- Modify: `requirements.txt`（新增 `mcp`）
- Test: `agent/tests/test_mcp_client.py`

**Interfaces:**
- Consumes: `McpConfig`（Task 1）、`McpClient` 基类（`agent/mcp_contract.py`）。
- Produces: `McpHttpClient(config)`，实现 `list_tools()`、`call_tool(name, args)`、`get_task_status(task_id)`、`health()`、`describe_capabilities()`、`negotiate_version(peer=None)`、`close()`；`interface_version` 来自配置。

**实现要点（照 `hadoop/tools/mcp_client.py:269-288` 的连接方式）：** 用 `mcp` 官方库的
`create_mcp_http_client(headers=...)` + `streamable_http_client(url, http_client=...)` + `ClientSession`；
每次动作开一次会话（短连接），动作完成即关。工具返回值是文本项拼接，`json.loads` 后即信封。
连接失败**不抛堆栈**，翻成可读说明（区分：口令不对 / 端口没转发或服务没起 / 其它）。

- [ ] **Step 1: 写失败测试（用注入的假会话，不真连网）**

```python
# agent/tests/test_mcp_client.py
import pytest

from agent import mcp_http_client as mc


class FakeSession:
    def __init__(self, tools=None, call_result=None):
        self._tools = tools or []
        self._call = call_result

    async def initialize(self):
        return None

    async def list_tools(self):
        class _T:
            def __init__(self, name, schema):
                self.name = name
                self.title = name
                self.input_schema = schema
        return type("R", (), {"tools": [_T(n, s) for n, s in self._tools]})()

    async def call_tool(self, name, args):
        class _C:
            def __init__(self, text):
                self.text = text
        import json
        return type("R", (), {"content": [_C(json.dumps(self._call))]})()


def test_list_tools_maps_to_rows(monkeypatch):
    cfg = mc.McpConfig({"url": "http://127.0.0.1:8900/mcp", "token": "t" * 20})
    client = mc.McpHttpClient(cfg, session_factory=lambda: FakeSession(
        tools=[("list_schemes", {"type": "object", "properties": {}, "required": []})]))
    rows = client.list_tools()
    assert rows[0]["name"] == "list_schemes"
    assert rows[0]["source"] == "mcp"


def test_call_tool_returns_envelope(monkeypatch):
    cfg = mc.McpConfig({"url": "http://127.0.0.1:8900/mcp", "token": "t" * 20})
    client = mc.McpHttpClient(cfg, session_factory=lambda: FakeSession(
        call_result={"ok": True, "interface_version": "1.0", "task_id": "T1"}))
    env = client.call_tool("get_task_status", {"task_id": "T1"})
    assert env["ok"] is True and env["task_id"] == "T1"


def test_unusable_config_reports_unavailable():
    client = mc.McpHttpClient(mc.McpConfig({}))
    env = client.call_tool("list_schemes", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "MCP_UNAVAILABLE"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest agent/tests/test_mcp_client.py -v`
Expected: FAIL（`ModuleNotFoundError: agent.mcp_http_client`）

- [ ] **Step 3: 实现**

先加依赖并安装：

```bash
python -m pip install mcp
python -m pip show mcp | findstr Version
```

把 `mcp` 追加到 `requirements.txt`（与 Hadoop 侧同版本；未固定时写 `mcp` 并在提交信息里记下实测版本）。

```python
# agent/mcp_http_client.py
# -*- coding: utf-8 -*-
"""真实 MCP 客户端：按标准协议连远端能力服务的 /mcp 入口。

连接方式与 hadoop/tools/mcp_client.py 一致：带鉴权头的 HTTP 客户端 + 传输层 + 会话，
每次动作开一次短会话。任何连接/协议异常都翻成可读说明，不把堆栈甩给上层。
口令只放进请求头，绝不进日志或信封。
"""

import asyncio
import json

from . import mcp_contract
from .mcp_config import McpConfig


def _flatten(exc):
    out = []
    stack = [exc]
    seen = set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        out.append(item)
        for sub in getattr(item, "exceptions", ()) or ():
            stack.append(sub)
    return out


def explain(exc, url):
    flat = _flatten(exc)
    text = "；".join("%s: %s" % (type(x).__name__, x) for x in flat[:3]) or str(exc)
    low = " ".join(str(x).lower() for x in flat) or str(exc).lower()
    if "401" in low or "unauthorized" in low:
        return "连接凭据不对：确认口令与请求头 Authorization: Bearer <口令>。原始错误：%s" % text
    if "refused" in low or "connect" in low or "unreachable" in low:
        return ("连不上 %s：服务可能没启动，或宿主机侧没配端口转发（虚拟机侧固定 8765）。"
                "原始错误：%s" % (url, text))
    return "调用失败：%s" % text


class McpHttpClient(mcp_contract.McpClient):
    def __init__(self, config=None, session_factory=None):
        self._config = config or McpConfig({})
        self._session_factory = session_factory     # 仅测试注入
        self.interface_version = self._config.interface_version

    @property
    def config(self):
        return self._config

    def _unavailable(self, action):
        return {"ok": False, "interface_version": self.interface_version,
                "error": {"code": mcp_contract.CODE_MCP_UNAVAILABLE,
                          "message": "远端能力服务未接入或未配置：%s" % action}}

    def _run(self, worker):
        """开一次会话跑 worker(session)，异常翻成可读错误 dict。"""
        if self._session_factory is not None:
            async def _fake():
                session = self._session_factory()
                await session.initialize()
                return await worker(session)
            try:
                return asyncio.run(_fake())
            except Exception as exc:                    # noqa: BLE001
                return {"_error": explain(exc, self._config.protocol_url())}

        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        headers = {"Authorization": "Bearer %s" % self._config.token}

        async def _real():
            async with create_mcp_http_client(headers=headers) as http_client:
                async with streamable_http_client(
                        self._config.protocol_url(), http_client=http_client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        return await worker(session)

        try:
            return asyncio.run(_real())
        except Exception as exc:                        # noqa: BLE001
            return {"_error": explain(exc, self._config.protocol_url())}

    def list_tools(self):
        if not self._config.usable:
            return []

        def worker(session):
            async def go():
                result = await session.list_tools()
                out = []
                for tool in list(getattr(result, "tools", []) or []):
                    schema = getattr(tool, "input_schema", None) or {}
                    out.append({"name": tool.name,
                                "title": getattr(tool, "title", None) or tool.name,
                                "description": getattr(tool, "description", "") or "",
                                "source": "mcp",
                                "parameters": schema})
                return out
            return go()
        # list_tools 需要 async worker；用 asyncio 直接跑
        return self._run_async_list()

    def _run_async_list(self):
        if self._session_factory is not None:
            async def _fake():
                session = self._session_factory()
                await session.initialize()
                result = await session.list_tools()
                out = []
                for tool in list(getattr(result, "tools", []) or []):
                    schema = getattr(tool, "input_schema", None) or {}
                    out.append({"name": tool.name, "title": getattr(tool, "title", None) or tool.name,
                                "description": getattr(tool, "description", "") or "",
                                "source": "mcp", "parameters": schema})
                return out
            try:
                return asyncio.run(_fake())
            except Exception:                           # noqa: BLE001
                return []
        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
        headers = {"Authorization": "Bearer %s" % self._config.token}

        async def _real():
            async with create_mcp_http_client(headers=headers) as http_client:
                async with streamable_http_client(
                        self._config.protocol_url(), http_client=http_client) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        out = []
                        for tool in list(getattr(result, "tools", []) or []):
                            schema = getattr(tool, "input_schema", None) or {}
                            out.append({"name": tool.name,
                                        "title": getattr(tool, "title", None) or tool.name,
                                        "description": getattr(tool, "description", "") or "",
                                        "source": "mcp", "parameters": schema})
                        return out
        try:
            return asyncio.run(_real())
        except Exception:                               # noqa: BLE001
            return []

    def call_tool(self, name, args):
        if not self._config.usable:
            return self._unavailable("call_tool(%s)" % name)

        def worker(session):
            async def go():
                result = await session.call_tool(name, args or {})
                texts = []
                for item in getattr(result, "content", []) or []:
                    text = getattr(item, "text", None)
                    if text is not None:
                        texts.append(text)
                return "\n".join(texts)
            return go()

        text = self._run(worker)
        if isinstance(text, dict) and text.get("_error"):
            return {"ok": False, "error": {"code": mcp_contract.CODE_DRIVER_UNREACHABLE,
                                           "message": text["_error"]}}
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return {"ok": False, "error": {"code": mcp_contract.CODE_DRIVER_BAD_OUTPUT,
                                           "message": "远端返回非信封：%s" % str(text)[:300]}}
        if not isinstance(payload, dict):
            return {"ok": False, "error": {"code": mcp_contract.CODE_DRIVER_BAD_OUTPUT,
                                           "message": "远端返回非对象信封"}}
        return payload

    def get_task_status(self, task_id):
        return self.call_tool("get_task_status", {"task_id": task_id})

    def describe_capabilities(self):
        return self.call_tool("describe_capabilities", {})

    def health(self):
        """健康检查：不进协议入口，直接 GET /health（免口令）。"""
        import httpx
        try:
            resp = httpx.get(self._config.health_url(), timeout=self._config.timeout_sec)
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True, "raw": data}
        except Exception as exc:                        # noqa: BLE001
            return {"ok": False, "error": {"code": mcp_contract.CODE_DRIVER_UNREACHABLE,
                                           "message": explain(exc, self._config.health_url())}}
```

> 注：`list_tools` 里保留的 `worker` 局部函数可删；实现时只保留 `_run_async_list` 路径，去掉死代码。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest agent/tests/test_mcp_client.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/mcp_http_client.py agent/tests/test_mcp_client.py requirements.txt
git commit -m "feat(agent): 真实 MCP 客户端（标准协议 /mcp，Bearer 口令，连接失败给可读原因）"
```

---

### Task 3: 远端工具表与来源改写

**Files:**
- Create: `agent/mcp_tools.py`
- Modify: `agent/tool_specs.py`
- Test: `agent/tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: 远端工具清单（Task 2 的 `list_tools`/`describe_capabilities`）。
- Produces:
  - `agent/mcp_tools.py`：`REMOTE_TOOL_SPECS`（22 项工具定义，`source="mcp"`，参数名/默认值以远端自描述为准）、`LOCAL_ONLY_TOOLS`、`remote_tool_names()`、`spec_for(name)`。
  - `tool_specs.TOOL_SPECS` 改为「远端 22 项 + 本机 `quick_clean_demo`」；`source_for` 对远端项返回 `mcp`。

**说明：** `quick_clean_demo` 保留 `source=local` 且仍在 `SPEC_TO_FUNCTION`；其余 10 项原本地工具（`validate_config/list_schemes/start_cleaning_task/get_task_status/get_task_result/get_samples/get_report/list_tasks/start_clean_task/score_task`）在表中改为 `mcp`，本地实现函数**保留在 `tools.py` 但不再注册**（供快速演示与旧测试复用）。

- [ ] **Step 1: 写失败测试**

```python
# agent/tests/test_mcp_tools.py
from agent import tool_specs, mcp_tools


def test_remote_tool_count_and_source():
    assert len(mcp_tools.REMOTE_TOOL_SPECS) == 22
    assert all(s["source"] == "mcp" for s in mcp_tools.REMOTE_TOOL_SPECS.values())


def test_local_only_is_quick_demo():
    assert mcp_tools.LOCAL_ONLY_TOOLS == ("quick_clean_demo",)


def test_tool_specs_routes_shared_tools_to_mcp():
    assert tool_specs.source_for("get_samples") == "mcp"
    assert tool_specs.source_for("start_cleaning_task") == "mcp"
    assert tool_specs.source_for("quick_clean_demo") == "local"


def test_samples_uses_limit_not_n_and_report_uses_format():
    samples = mcp_tools.REMOTE_TOOL_SPECS["get_samples"]["parameters"]["properties"]
    assert "limit" in samples and "n" not in samples
    report = mcp_tools.REMOTE_TOOL_SPECS["get_report"]["parameters"]["properties"]
    assert "format" in report and "fmt" not in report


def test_no_foreground_or_exec_mode_on_start_tools():
    for name in ("start_cleaning_task", "start_clean_task", "score_task"):
        props = mcp_tools.REMOTE_TOOL_SPECS[name]["parameters"]["properties"]
        assert "foreground" not in props
        assert "exec_mode" not in props
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest agent/tests/test_mcp_tools.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `agent/mcp_tools.py`**

按远端能力自描述（`hadoop/mcpserver/tools.py:92-341`）逐项写入 22 个工具，参数名与默认值以远端为准。示例骨架（其余 11 个同法补齐）：

```python
# agent/mcp_tools.py
# -*- coding: utf-8 -*-
"""远端能力服务的工具表（source=mcp）。

参数名/默认值/枚举以远端能力自描述为准（hadoop/mcpserver/tools.py）。
本表是本机侧的权威形状；运行时可用 describe_capabilities 校验一致性（见 Task 4 说明）。
"""

SOURCE_MCP = "mcp"

_LOCAL_ONLY = ("quick_clean_demo",)


def _p(type_, description, **extra):
    d = {"type": type_, "description": description}
    d.update(extra)
    return d


REMOTE_TOOL_SPECS = {
    "validate_config": {
        "name": "validate_config", "source": SOURCE_MCP, "long_running": False, "estimated_seconds": 3,
        "description": "校验清洗方案/评分方案是否合法。",
        "parameters": {"type": "object", "properties": {
            "rules": _p("string", "清洗方案 JSON 路径；缺省用默认方案"),
            "scoring": _p("string", "评分方案 JSON 路径；缺省用默认方案")}, "required": []},
    },
    "get_samples": {
        "name": "get_samples", "source": SOURCE_MCP, "long_running": False, "estimated_seconds": 1,
        "description": "取样例：cleaned=清洗后（默认）/ quarantine=隔离区（带规则与原因）。",
        "parameters": {"type": "object", "properties": {
            "task_id": _p("string", "任务标识"),
            "sample_type": _p("string", "cleaned=清洗后；quarantine=隔离区", enum=["cleaned", "quarantine"], default="cleaned"),
            "table": _p("string", "movies / ratings / users", enum=["movies", "ratings", "users"], default="movies"),
            "limit": _p("integer", "取几条（默认 5，上限 2000）", minimum=0, maximum=2000, default=5)},
            "required": ["task_id"]},
    },
    "get_report": {
        "name": "get_report", "source": SOURCE_MCP, "long_running": False, "estimated_seconds": 1,
        "description": "取报告：json=结构化（默认）/ md=Markdown 全文。",
        "parameters": {"type": "object", "properties": {
            "task_id": _p("string", "任务标识"),
            "format": _p("string", "json=结构化（默认）；md=全文", enum=["md", "json"], default="json")},
            "required": ["task_id"]},
    },
    # ……其余 19 项照 hadoop/mcpserver/tools.py 补齐（start_cleaning_task / start_clean_task /
    #    score_task / get_task_status / get_task_result / list_tasks / list_schemes /
    #    list_published_versions / list_task_files / read_task_file / get_job_counters /
    #    get_job_logs / list_raw_datasets / get_scheme_content / describe_capabilities /
    #    wait_for_task / cancel_task / get_scheduler_state / health_check）……
}

#: 本机专属能力（不来自远端）。
LOCAL_ONLY_TOOLS = _LOCAL_ONLY


def remote_tool_names():
    return tuple(REMOTE_TOOL_SPECS.keys())


def spec_for(name):
    return REMOTE_TOOL_SPECS.get(name)
```

- [ ] **Step 4: 改 `agent/tool_specs.py`**

在文件末尾追加合并逻辑（放在 `TOOL_SPECS` 定义之后、各辅助函数之前）：

```python
from . import mcp_tools

# 远端能力覆盖同名本地项（来源改为 mcp），本机仅保留快速演示。
for _name, _spec in mcp_tools.REMOTE_TOOL_SPECS.items():
    TOOL_SPECS[_name] = _spec
# 未在远端表内的本机工具（quick_clean_demo）保持 local
```

并确认 `SPEC_TO_FUNCTION` 仍含 `quick_clean_demo`；远端项不需要本地实现函数。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest agent/tests/test_mcp_tools.py agent/tests/test_tool_specs.py -v`
Expected: 新增用例 PASS；`test_tool_specs.py` 里对旧参数名/旧来源的断言按本计划改成 mcp 语义（见 Task 6）。

- [ ] **Step 6: 提交**

```bash
git add agent/mcp_tools.py agent/tool_specs.py agent/tests/test_mcp_tools.py
git commit -m "feat(agent): 远端工具表（22 项，source=mcp），本机仅保留快速演示"
```

---

### Task 4: 启动时用能力自描述校验工具表

**Files:**
- Modify: `agent/mcp_http_client.py`（新增 `verify_tools()`）
- Modify: `agent/runtime/tool_exec.py`
- Test: `agent/tests/test_mcp_tools.py`

**Interfaces:**
- Produces: `McpHttpClient.verify_tools(declared)` → `{"ok","missing","extra","version_ok","peer_version"}`；`tool_exec` 装配时调用并记录不一致（不阻断启动，只在 `/health` 暴露）。

- [ ] **Step 1: 写失败测试**

```python
def test_verify_tools_detects_missing():
    from agent import mcp_http_client as mc, mcp_tools
    cfg = mc.McpConfig({"url": "http://127.0.0.1:8900/mcp", "token": "t" * 20})
    client = mc.McpHttpClient(cfg, session_factory=lambda: _FakeCaps())
    out = client.verify_tools(declared=set(mcp_tools.remote_tool_names()) - {"cancel_task"})
    assert "cancel_task" in out["extra"] or "cancel_task" in out["missing"] or out["ok"] is False
```

（`_FakeCaps` 返回一个 `describe_capabilities` 信封，`tools` 含 22 项；测试文件内实现。）

- [ ] **Step 2-4:** 跑失败 → 实现 `verify_tools`（调 `describe_capabilities`，比对名字集合与 `interface_version`）→ 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/mcp_http_client.py agent/runtime/tool_exec.py agent/tests/test_mcp_tools.py
git commit -m "feat(agent): 启动时用能力自描述校验工具表一致性"
```

---

### Task 5: 扩充结构化错误码与细类

**Files:**
- Modify: `agent/mcp_contract.py`
- Test: `agent/tests/test_mcp_errors.py`

**Interfaces:**
- Produces: 在 `STRUCTURED_ERROR_CODES` 增补远端可能出现的码；新增 `REASON_KINDS` 常量与 `describe_error(error) -> str`（生成可读中文，供出口文案统一）。

- [ ] **Step 1: 写失败测试**

```python
from agent import mcp_contract as c


def test_new_codes_present():
    for code in ("ABORTED", "FILE_NOT_FOUND", "FORBIDDEN_PATH", "CONTENT_TOO_LARGE",
                 "PRODUCT_INVALID", "CANCEL_FAILED", "UNAUTHORIZED", "FORBIDDEN_HOST",
                 "WAIT_LIMIT_REACHED", "INTERNAL_ERROR", "NOT_PREVIEWABLE"):
        assert code in c.STRUCTURED_ERROR_CODES


def test_reason_kinds():
    assert set(c.REASON_KINDS) == {"control", "sidecar", "residue", "directory", "not_regular_file"}
```

- [ ] **Step 2-4:** 跑失败 → 在 `mcp_contract.py` 增加常量并并入 `STRUCTURED_ERROR_CODES`，新增 `REASON_KINDS` → 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/mcp_contract.py agent/tests/test_mcp_errors.py
git commit -m "feat(agent): 扩充结构化错误码与 reason_kind 细类"
```

---

### Task 6: 参数名与默认值对齐

**Files:**
- Modify: `agent/tools.py`、`agent/tool_policy.py`、`agent/runtime/stream.py`、`agent/tests/test_tool_specs.py`
- Test: `agent/tests/test_tool_param_align.py`

**要改的点（对齐远端）：**
- `get_samples`：形参与 schema 的 `n` → `limit`（默认 5，上限 2000；本机实现调 driver 时再映射回 `n`）。
- `get_report`：形参与 schema 的 `fmt` → `format`（默认 `json`）。
- `start_cleaning_task`：去掉 `foreground`、`exec_mode` 形参；保留 `force`。
- `start_clean_task`：去掉 `exec_mode`。
- `score_task`：去掉 `foreground`（本机不提供强制开关，与远端一致）。
- `agent/runtime/stream.py:70-74` 的 `LONG_RUNNING_TOOLS` 预估时长改为远端值：`start_cleaning_task:1500, start_clean_task:900, score_task:300`。
- `agent/tool_policy.py` 的策略表按改名后的参数名同步（如用到参数名）。

- [ ] **Step 1: 写失败测试**

```python
from agent import tools, tool_specs


def test_get_samples_limit_param():
    import inspect
    assert "limit" in inspect.signature(tools.get_samples).parameters
    assert "n" not in inspect.signature(tools.get_samples).parameters
    props = tool_specs.schema_for("get_samples")["parameters"]["properties"]
    assert "limit" in props and props["limit"]["maximum"] == 2000


def test_get_report_format_param():
    import inspect
    assert "format" in inspect.signature(tools.get_report).parameters


def test_start_tools_drop_foreground_exec_mode():
    import inspect
    for fn in (tools.start_cleaning_task, tools.start_clean_task, tools.score_task):
        params = inspect.signature(fn).parameters
        assert "foreground" not in params
        assert "exec_mode" not in params
```

- [ ] **Step 2-4:** 跑失败 → 改 `tools.py`（形参名、内部映射、`get_samples` 上限到 2000）、`stream.py`、`tool_policy.py`、`test_tool_specs.py` → 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/tools.py agent/tool_policy.py agent/runtime/stream.py agent/tests/
git commit -m "refactor(agent): 工具参数名与默认值对齐远端（limit/format，去掉 foreground/exec_mode）"
```

---

### Task 7: 状态五态含中止

**Files:**
- Modify: `agent/tools.py`（等待终态的判定）、`agent/explain.py`
- Test: `agent/tests/test_task_status_aborted.py`

- [ ] **Step 1: 写失败测试**

```python
from agent import explain


def test_explain_maps_aborted():
    text = explain.status_label("aborted")
    assert text == "已中止"
```

（若 `tools.py` 有等待函数，加一条：aborted 立即返回，不继续循环。）

- [ ] **Step 2-4:** 跑失败 → `explain.py` 的映射表补 `aborted:"已中止"`；`tools.py` 等待集合由 `("succeeded","failed")` 改为含 `"aborted"` → 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/tools.py agent/explain.py agent/tests/test_task_status_aborted.py
git commit -m "fix(agent): 状态五态含已中止（等待与说明映射同步）"
```

---

### Task 8: 长任务探针改走远端

**Files:**
- Modify: `agent/runtime/long_task.py`、`agent/runtime/runtime.py`、`agent/runtime/tool_exec.py`、`agent/http_api.py`
- Test: `agent/tests/test_remote_probe.py`

**Interfaces:**
- Consumes: `McpHttpClient`（Task 2）。
- Produces: `RemoteTaskProbe(client)`，`probe(record)` 用 `hadoop_task_id` 调远端 `get_task_status`，成功终态再取 `get_task_result`；装配点 `http_api._get_runtime` 注入。

- [ ] **Step 1: 写失败测试**（用假客户端）

```python
from agent.runtime.long_task import RemoteTaskProbe


class FakeClient:
    def __init__(self, status):
        self._status = status
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "get_task_status":
            return {"ok": True, "task_id": args["task_id"], "status": self._status}
        return {"ok": True, "task_id": args["task_id"], "scores": {}, "counts": {}}


def test_probe_uses_hadoop_task_id():
    rec = {"hadoop_task_id": "H1", "agent_task_id": "LT-0001"}
    probe = RemoteTaskProbe(FakeClient("running"))
    out = probe.probe(rec)
    assert out["status"] == "running"


def test_probe_fetches_result_on_success():
    client = FakeClient("succeeded")
    probe = RemoteTaskProbe(client)
    probe.probe({"hadoop_task_id": "H1", "agent_task_id": "LT-0001"})
    assert ("get_task_result", {"task_id": "H1"}) in client.calls
```

- [ ] **Step 2-4:** 跑失败 → 在 `long_task.py` 新增 `RemoteTaskProbe`（复用 `DriverTaskProbe` 的形状但走客户端 `call_tool`）；`http_api._get_runtime` 用 `RemoteTaskProbe(client)` 作为 `task_probe` → 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/runtime/long_task.py agent/runtime/runtime.py agent/runtime/tool_exec.py agent/http_api.py agent/tests/test_remote_probe.py
git commit -m "feat(agent): 长任务探针改走远端（按 hadoop_task_id 查状态/取结果）"
```

---

### Task 9: 运行期装配真实客户端

**Files:**
- Modify: `agent/runtime/runtime.py`、`agent/http_api.py`、`agent/runtime/__init__.py`
- Test: `agent/tests/test_stage8_mcp_seam.py`（改造）

- [ ] **Step 1: 写失败测试**

```python
def test_runtime_uses_injected_client():
    from agent.runtime.runtime import Runtime
    from agent import mcp_config
    from agent.mcp_http_client import McpHttpClient
    client = McpHttpClient(mcp_config.McpConfig({"url": "http://x/mcp", "token": "t" * 20}),
                           session_factory=lambda: None)
    rt = Runtime(mcp_client=client)          # 新增关键字
    assert rt.tool_backend                            # 装配成功
```

- [ ] **Step 2-4:** 跑失败 → `Runtime.__init__` 增 `mcp_client=None` 关键字，`build_tool_backend(mcp_client=...)`；`http_api._get_runtime` 里 `mcp_config.load()` 后构造 `McpHttpClient` 注入；不可用时用 `NullMcpClient`（保持诚实报错）→ 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/runtime/runtime.py agent/http_api.py agent/runtime/__init__.py agent/tests/test_stage8_mcp_seam.py
git commit -m "feat(agent): 运行期装配真实 MCP 客户端（不可用则诚实报错）"
```

---

### Task 10: 原始数据上传下线，列表改远端只读

**Files:**
- Modify: `agent/http_api.py`、`agent/resources.py`
- Test: `agent/tests/test_resources_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_raw_upload_route_removed(client):
    resp = client.post("/api/data/raw/upload", data=b"x")
    assert resp.status_code == 404


def test_raw_list_uses_remote(monkeypatch, client):
    # 伪远端 list_raw_datasets 信封 → /api/data/raw 返回远端数据集
    ...
```

- [ ] **Step 2-4:** 跑失败 → 删 `http_api` 上传分支与 `resources.upload_raw/_extract_zip`；`_get_resources` 的 `/api/data/raw` 改为调用远端 `list_raw_datasets`（经客户端，失败如实转述）→ 跑通过（同步删 `test_resources_api.py` 里上传相关用例）。

- [ ] **Step 5: 提交**

```bash
git add agent/http_api.py agent/resources.py agent/tests/test_resources_api.py
git commit -m "feat(agent): 原始数据上传下线（远端不提供上传）；列表改远端只读"
```

---

### Task 11: 已发布数据/方案/产物清单改远端来源

**Files:**
- Modify: `agent/resources.py`、`agent/http_api.py`
- Test: `agent/tests/test_resources_api.py`

**要点：**
- 已发布数据：`/api/data/published` → 远端 `list_published_versions`。
- 方案文件：`/api/schemes` → 远端 `list_schemes`；内容 → `get_scheme_content`。
- 产物文件：`/api/tasks/{tid}/files` → 远端 `list_task_files`（`tid` 为 **hadoop_task_id**）。
- 预览：`/api/files/preview` → 远端 `read_task_file`（保留 `previewable` 语义）。

- [ ] **Step 1-5:** 按上述逐项写测试 → 实现 → 通过 → 提交（每个来源一项小提交）。

```bash
git commit -m "feat(agent): 资源四处来源改远端（已发布/方案/产物清单/预览）"
```

---

### Task 12: 二进制下载中转（流式）

**Files:**
- Create: `agent/mcp_download.py`
- Modify: `agent/resources.py`、`agent/http_api.py`
- Test: `agent/tests/test_mcp_download.py`

**Interfaces:**
- Produces: `open_task_stream(config, hadoop_task_id, relpath) -> (iterator[bytes], headers, error)`、`open_published_stream(config, data_version, table) -> (iterator[bytes], headers, error)`；`http_api` 的 `/api/files/download` 改为写头后分块转发。

- [ ] **Step 1: 写失败测试**（用 `httpx.MockTransport` 或本地假响应）

```python
import httpx
from agent import mcp_download as dl


def test_open_task_stream_relays_bytes(monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer " + "t" * 20
        return httpx.Response(200, content=b"hello",
                              headers={"content-disposition": 'attachment; filename="a.dat"'})
    cfg = ...  # McpConfig(url, token)
    it, headers, err = dl.open_task_stream(cfg, "H1", "cleaned/x/ratings.dat", transport=httpx.MockTransport(handler))
    assert err is None
    assert b"".join(it) == b"hello"
    assert "filename" in headers.get("content-disposition", "")


def test_open_task_stream_surfaces_structured_error(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"ok": False, "error": {"code": "FILE_NOT_FOUND", "message": "不存在"}})
    cfg = ...
    it, headers, err = dl.open_task_stream(cfg, "H1", "nope", transport=httpx.MockTransport(handler))
    assert err["code"] == "FILE_NOT_FOUND"
```

- [ ] **Step 2-4:** 跑失败 → 实现（httpx 流式 GET，带 `Authorization: Bearer`；非 2xx 读信封取 `error.code`；成功返回内容迭代器 + 透传 `Content-Disposition`/`Content-Length`）；改 `http_api` 下载处理为分块写 → 跑通过。

- [ ] **Step 5: 提交**

```bash
git add agent/mcp_download.py agent/resources.py agent/http_api.py agent/tests/test_mcp_download.py
git commit -m "feat(agent): 产物下载经服务只读路由，Agent 带口令流式中转"
```

---

### Task 13: health 暴露远端状态 + 依赖与启动脚本

**Files:**
- Modify: `agent/http_api.py`、`requirements.txt`、`start_backend.bat`
- Test: `agent/tests/test_health_remote.py`

- [ ] **Step 1: 写失败测试**

```python
def test_health_reports_mcp(client, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_URL", "http://127.0.0.1:8900/mcp")
    monkeypatch.setenv("AGENT_MCP_TOKEN", "t" * 20)
    body = client.get("/health").json()
    assert "mcp" in body
    assert body["mcp"]["token_set"] is True
```

- [ ] **Step 2-4:** 跑失败 → `/health` 增加 `mcp`（用 `safe_dict()`，**不含口令**）→ 跑通过。

- [ ] **Step 5:** `requirements.txt` 已有 `mcp`（Task 2）；`start_backend.bat` 增加注释说明可设置 `AGENT_MCP_URL`/`AGENT_MCP_TOKEN`（不写死口令）。

- [ ] **Step 6: 提交**

```bash
git add agent/http_api.py start_backend.bat
git commit -m "feat(agent): /health 暴露远端连接状态（脱敏）；启动脚本补远端环境变量说明"
```

---

### Task 14: 收口 —— 改既有测试与全量跑通

**Files:**
- Modify: `agent/tests/*`（受影响的旧用例）
- Run: 全量

- [ ] **Step 1:** 全量后端测试：`python -m pytest agent/tests -q`（把因参数改名/来源改写/上传下线而失败的用例改到新语义）。
- [ ] **Step 2:** 用假客户端补一条端到端：LLM 主控走一次「发起全流程 → 查状态 → 到终态取结果」的假链路。
- [ ] **Step 3: 提交**

```bash
git add agent/tests
git commit -m "test(agent): 收口 —— 全量用例按远端语义调整并通过"
```

---

## Self-Review（写完计划后自查）

- **规格覆盖**：文档 §1（接上远端）→ Task 1/2/9；§2（工具清单与参数）→ Task 3/6；§3（双编号）→ Task 8 + 全局约束；§4（信封/类别/路径）→ Task 3/11；§5（错误码）→ Task 5；§6/§8（资源与下载）→ Task 10/11/12；§7（五态）→ Task 7；§1.1 健康检查 → Task 2/13。
- **占位符扫描**：`mcp_tools.py` 有「其余 19 项照 tools.py 补齐」——执行者必须逐项照 `hadoop/mcpserver/tools.py` 写全，不得留空。
- **类型一致**：`McpClient` 方法名（`list_tools`/`call_tool`/`get_task_status`/`close`）在 Task 2/8/9 中一致；`hadoop_task_id` 贯穿 Task 8/10/11/12。
