# Agent 远端 MCP 接入 · Task 9 报告：运行期装配真实客户端

计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 9: 运行期装配真实客户端」

## 目标

让 Agent 在运行期真正拿住远端能力服务的客户端：把已有的 `McpHttpClient`（真实客户端）、
`NullMcpClient`（诚实占位）、`RemoteTaskProbe`（远端探针）在装配点接上，使
`source=mcp` 的工具调用与长任务查状态/取结果都走远端；远端缺席时 Agent 照常启动，
远端调用以结构化错误如实说明，不崩、不半残。

## 改动

### 1. `agent/runtime/runtime.py`

- `Runtime.__init__` 新增关键字 `mcp_client=None`。
- 未显式传 `tool_backend` 时，改为 `build_tool_backend(mcp_client=mcp_client)`（此前不传客户端）。
- `task_probe` 注入路径保持不变（`LongTaskManager(..., probe=task_probe)`）。

### 2. `agent/http_api.py` `_get_runtime`

- 读取 `mcp_config.load()`（地址/口令来自配置或环境变量 `AGENT_MCP_*`，**不写死**）。
- `cfg.usable` 为真 → `McpHttpClient(cfg)`；否则 → `mcp_contract.NullMcpClient()`。
- `Runtime(mcp_client=client, task_probe=RemoteTaskProbe(client))`——生产探针由本机
  `default_probe`（driver）换成远端探针。
- 口令全程只经 `McpConfig`，不落日志、不进信封、不写死。

### 3. `agent/runtime/__init__.py`

- 无需改动：`RemoteTaskProbe`（Task 8 起）与 `Runtime` / `build_tool_backend` 等已导出。

### 4. 缺省/未配置行为

- 远端缺席（无地址或无口令）→ `NullMcpClient` 注入，`Runtime` 正常构造、服务正常起。
- 此时 `source=mcp` 的工具调用经 `McpToolBackend` → `NullMcpClient.call_tool` →
  结构化 `{"ok": false, "error": {"code": "MCP_UNAVAILABLE", ...}}`。
- 远端探针挂在 `NullMcpClient` 上时 `probe` 返回 `None`，长任务巡检按既有语义跳过。

## 测试（TDD，纯本地，不触网）

改造 `agent/tests/test_stage8_mcp_seam.py`（保留并沿用原有用例，新增两组用例）：

- `TestRuntimeInjectsMcpClient`
  - `test_injected_client_serves_mcp_tools`：`Runtime(mcp_client=fake)` → 路由后端的
    MCP 客户端即该 fake；执行 `source=mcp` 工具一路走它并回传其信封；全程不触模型。
  - `test_no_client_falls_back_to_null_and_reports_unavailable`：不注入客户端 →
    `NullMcpClient`；MCP 工具调用回报 `MCP_UNAVAILABLE`。
  - `test_injected_task_probe_still_works`：`task_probe=` 注入仍生效（探针可被调用）。
- `TestProductionRuntimeWiring`
  - `test_without_config_still_starts_with_null_client`：清空 `AGENT_MCP_URL` /
    `AGENT_MCP_TOKEN` → `_get_runtime()` 仍成功；客户端是 `NullMcpClient`；探针是
    `RemoteTaskProbe`；`get_samples` 调用回报 `MCP_UNAVAILABLE`。
  - `test_configured_env_wires_http_client_and_remote_probe`：设置地址与口令（假值）→
    客户端是 `McpHttpClient`、探针是 `RemoteTaskProbe` 且其客户端即该实例；脱敏视图
    不含口令。启动校验的后台线程以 `mock.patch.object(McpHttpClient, "verify_tools")`
    挡住，并 `wait_for_startup_tool_check` 等其结束，**不连远端**。

用例内只用假客户端/假探针，未涉及 `127.0.0.1:8900`。

## 验证

```
python -m pytest agent/tests -q
370 passed, 237 subtests passed
```

（改造前基线 365 passed；新增 5 条用例全部通过。）

## 约束核对

- 未对远端服务 `127.0.0.1:8900` 发起任何调用，测试全部本地。
- 口令仅出现在被 `mock.patch.object` 挡住的路径与脱敏断言中，不落日志/信封/代码。
- 文案未使用受限词。

## 提交

一个本地提交（不推送）：见提交记录。
