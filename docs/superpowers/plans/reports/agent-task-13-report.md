# Task 13 报告：health 暴露远端状态 + 依赖与启动脚本

## 状态
完成（本地绿；未推送）。

## 目标
在 `/health` 暴露远端能力服务的连接状态（脱敏，**不含口令**），并顺带带出 AG-4 的
启动工具表一致性校验结果；给 `start_backend.bat` 补上远端环境变量的说明注释。

## 改动文件
- 修改 `agent/http_api.py`（`/health` 增 `mcp` 段 + 两个只读辅助函数）
- 修改 `start_backend.bat`（补 `AGENT_MCP_URL` / `AGENT_MCP_TOKEN` 注释，口令不写死）
- 新增 `agent/tests/test_health_remote.py`（4 项，真实 HTTP 实测）
- `requirements.txt` 未改（`mcp==2.3.0` 已在 AG-2 加入）

## `agent/http_api.py` — 接线
`GET /health` 响应新增一段：

```json
"mcp": {
  "url": "http://127.0.0.1:8900/mcp" | null,
  "token_set": true|false,
  "timeout_sec": 15.0,
  "interface_version": "1.0",
  "usable": true|false,
  "startup_check": { "ok":…, "missing":[…], "extra":[…],
                     "version_ok":…, "peer_version":… } | null
}
```

要点：
- 连接状态取自 `mcp_config.load().safe_dict()` —— 该视图**从不包含口令**，只有
  `token_set` 布尔；地址、超时、接口版本、`usable` 一并给出。
- `startup_check` 取「实际使用的执行后端」的持有者：运行时 `_tool_backend` →
  `tool_exec.startup_tool_check(backend)`。结果由 AG-4 的启动后台线程写入，故可能读到
  `None`（未注入客户端 / 远端不在 / 尚未跑完），即 best-effort。
- **不崩**：取配置失败 → 回退 `{"usable": false, "token_set": false}`；运行时不可用 /
  无后端 / 读取异常一律 `startup_check = null`，不影响 `/health` 其余字段。
- 远端客户端是 `NullMcpClient` / 未配置时，`usable=false`，如实报告不可用，不连网。

## `start_backend.bat`
在「可选后端默认模型」块后追加注释块，说明远端连接经这两个环境变量配置：
`AGENT_MCP_URL`（示例 `http://127.0.0.1:8900/mcp`，宿主 8900 → 虚拟机 8765）与
`AGENT_MCP_TOKEN`；并注明**不要把口令写进脚本**、未配置时后端照常启动、远端工具报不可用。

## 测试（`agent/tests/test_health_remote.py`，全部无网络）
真起 `ThreadingHTTPServer`（注入只暴露 `_tool_backend` 的桩运行时），覆盖：
- 配了 `AGENT_MCP_URL`/`AGENT_MCP_TOKEN` → `mcp.token_set=true`、`usable=true`，
  且**响应原文不含口令值**；
- 两者都未配 → `mcp.usable=false`、`token_set=false`，`/health` 仍 `ok=true`（不崩）；
- 注入持有者（`build_tool_backend(FakeVerifyClient)`，AG-4 校验结果）→
  `mcp.startup_check` 带出 `ok/missing/extra/version_ok/peer_version`；
- 无后端持有者 → `startup_check` 为 `null`。

TDD：先写好 4 项，确认因缺 `mcp` 段而全数失败（`AssertionError: 'mcp' not found`），
再实现，转绿。

## 验证
```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_health_remote.py -q
4 passed

& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
396 passed, 237 subtests passed in 44.08s
```
（前次 392 passed；净增 4 项。）全程未访问 127.0.0.1:8900；口令未进日志 / 信封 / 响应；
文案无「冒烟」「对账」「口径」等词。

## 关切 / 备注
- `/health` 为取 `startup_check` 会经 `_get_runtime()`；若运行时尚未创建则触发一次
  惰性装配（缺 LangGraph 时抛异常并被捕获，`startup_check` 返回 null），不影响健康检查语义。
- `safe_dict()` 已含 `url`（可能含主机信息），符合「暴露远端连接状态」的意图，且不含凭据。
