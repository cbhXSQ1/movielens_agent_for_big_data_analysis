# Agent Task 4 报告 —— 启动时用能力自描述校验工具表

- 计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 4: 启动时用能力自描述校验工具表」
- 状态：DONE
- 日期：2026-10-18

## 目标（本任务）

启动装配执行后端时，用远端 `describe_capabilities` 自描述校验本端声明的工具表
（22 项）与接口版本；**非阻断**——远端不在也不影响启动，结果留存供 Task 13 的
`/health` 暴露。任何失败都翻成带原因的结果 dict，**绝不抛异常**。

## 接口契约

### `McpHttpClient.verify_tools(declared) -> dict`（`agent/mcp_http_client.py`）

返回四个约定键：`{"ok", "missing", "extra", "version_ok", "peer_version"}`：

| 字段 | 含义 |
| --- | --- |
| `missing` | 本端声明而远端没有的工具名（排序列表）——declared − server |
| `extra` | 远端有而本端未声明的工具名（排序列表）——server − declared |
| `version_ok` | 接口版本是否**同主版本**（`mcp_contract.negotiate`，加性兼容） |
| `peer_version` | 远端 `interface_version` 原值（失败时为 `None`） |
| `ok` | 名字集合完全一致 **且** `version_ok` 时为 `True` |

失败路径（未抛）：客户端不可用 → `reason="远端能力服务未接入或未配置：verify_tools"`；
调用失败 / 非信封 → `reason=<远端 error.message 或兜底说明>`；两种都返回
`ok=False` 且四个约定键齐全。**不发任何网络请求**（不可用时直接返回）。

### 启动装配（`agent/runtime/tool_exec.py`）

- 新增 `verify_declared_tools(mcp_client, declared=None) -> dict|None`：同步单元；
  未注入客户端 / 客户端无 `verify_tools` 时返回 `None`；异常翻成带 `reason` 结果。
- `build_tool_backend(mcp_client=None)`：注入客户端时**在后台 daemon 线程**发起
  校验（非阻塞启动），结果存入模块级单槽；无论成败都照常返回 `RoutingBackend`。
- `startup_tool_check()`：读最近一次校验结果（供 `/health`）。
- `wait_for_startup_tool_check(timeout=None)`：等待后台线程并返回结果（测试/同步读用）。

## 改动的文件

| 文件 | 变更 |
| --- | --- |
| `agent/mcp_http_client.py` | 新增 `verify_tools()` 及私有 `_declined()` / `_tool_names_in()` |
| `agent/runtime/tool_exec.py` | 新增启动校验装配（后台线程）、`verify_declared_tools` / `startup_tool_check` / `wait_for_startup_tool_check` |
| `agent/tests/test_mcp_tools.py` | 新增 `TestVerifyTools`（7 条）+ `TestStartupToolCheck`（2 条） |

> 严格限定在任务给定的「Only touch」清单内：生产代码只碰 `mcp_http_client.py`、
> `runtime/tool_exec.py`，测试只碰 `tests/test_mcp_tools.py`。

## 关键实现点

- **TDD**：先写失败用例（9 条新用例全红：`AttributeError: verify_tools` /
  `wait_for_startup_tool_check`），再实现，再转绿。
- **无网络**：全部用例走注入的 `session_factory` 接缝，喂一个 `describe_capabilities`
  信封（`tools` 含 22 项 `{"name": ...}`）；不可用配置用例直接断言不发请求。
- **集合语义**：`missing`/`extra` 为**排序列表**，便于 `/health` 稳定展示与比对。
- **版本**：复用 `mcp_contract.negotiate`（同主版本即兼容，只做加性变更），
  peer 版本原样透传（`peer_version`）。
- **非阻断**：`build_tool_backend` 的校验放 daemon 后台线程、结果单槽 + `threading.Lock`；
  真正的连接/协议失败在 `McpHttpClient` 内已翻成结构化错误，装配层再兜一层 `try/except`。
- **口令红线**：本任务新增代码不写任何日志；`reason` 只来自远端 `error.message`，
  不含口令；不可用分支为静态文案。

## 测试命令与结果

定向（本任务新增用例 + 客户端 / 接缝回归）：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_mcp_tools.py agent/tests/test_mcp_client.py agent/tests/test_stage8_mcp_seam.py -q
```

结果：`66 passed, 29 subtests passed in 0.60s`
（`test_mcp_tools.py` 新增后共 21 条：其中 `TestVerifyTools` 7 条、`TestStartupToolCheck` 2 条全绿）

全量（无网络、不连远端服务）：

```
& "...\python.exe" -m pytest agent/tests -q
```

结果：`338 passed, 186 subtests passed in 38.54s`（基线为 `329 passed, 186 subtests`，
新增 9 条全部通过，无回归）

## 关注点 / 备注

1. **「非阻塞」的落地方式**：任务要求「NON-BLOCKING 调用 + 不让启动失败」，
   本实现理解为**不阻塞/不拖垮启动**：校验放在 daemon 后台线程，装配立即返回；
   远端缺席时前端/运行时照常可用。若评审希望改为**同步一次性探测**（更易预测、
   但可能给首个请求带来最长一个超时的延迟），改动很小，只需去掉线程。
2. **结果暂为进程内单槽**：`_STARTUP_TOOL_CHECK` 是模块级变量，供同进程的 `/health`
   读取。Task 13 暴露 `/health.mcp` 时可直接 `startup_tool_check()`；若届时 `Runtime`
   持有后端实例，也可改为挂在实例/客户端上（本任务不涉及）。
3. **`verify_tools` 内部同步调用网络**：`describe_capabilities` 走客户端短会话，
   超时遵循 `McpConfig.timeout_sec`（默认 15s）。线程化后不阻塞启动，但后台线程
   最长会存活到该超时（daemon，不阻止进程退出）。
4. **`tools` 条目的名称来源**：按远端 `hadoop/mcpserver/tools.py::describe_capabilities`
   的 `tools=[as_public_entry(t) ...]` 形状，取每条 `name`；同时兼容裸名字串，防远端
   简化格式。
