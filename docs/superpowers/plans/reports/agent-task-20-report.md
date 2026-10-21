# Agent Task 20 — 报告

## 标题
下线 Agent 侧遗留的本地 driver 直取端点（`http_api.py`），只保留远端能力与本地快速演示

## 背景
当前 Agent 除「本地快速演示清洗」外，一切都经远端能力服务。但 `agent/http_api.py` 仍
暴露一组**遗留端点**，它们直接调用本地驱动层（`agent/tools.py` → `agent/driver_client.py`）：

- `GET  /api/schemes`（本地 `tools.list_schemes()`）
- `GET  /api/tasks`（本地 `tools.list_tasks()`）
- `GET  /api/tasks/{tid}/status`（本地 `tools.get_task_status`）
- `GET  /api/tasks/{tid}/result`（本地 `tools.get_task_result`，含 `?explain=1`）
- `GET  /api/tasks/{tid}/samples`（本地 `tools.get_samples`）
- `GET  /api/tasks/{tid}/report`（本地 `tools.get_report`）
- `POST /api/tasks`（本地 `tools.start_cleaning_task` + 记运行参数）

对远端任务这些路径必然失败，且前端已不再调用。它们由 `_get_legacy`（GET 兜底分派）与
`do_POST` 里的一段 `if u.path == "/api/tasks"` 提供。本任务把这批本地直取路由整段移除，
使除快速演示外不再残留任何「本地 driver 支撑」的 HTTP 入口。

## 变更（仅 `agent/http_api.py`）

1. **删除 `_get_legacy` 方法**：GET 分派里对 `/api/schemes`、`/api/tasks…` 的本地直取分支
   全部删除。
2. **`_get_resources` 兜底改为 404**：原来末尾 `return self._get_legacy(parts, u, q)` 改为
   `self._send(404, _error_envelope("USAGE", "未知路径：%s" % u.path))`。未匹配的路径不再
   回落本地，直接落「未知路径」。
3. **`POST /api/tasks` 分支删除**：`do_POST` 里发起本地清洗任务 + 记 `remember_opts` 的整段
   删除。
4. **方案列表只剩远端一条路径**：`/api/schemes` 的分支由 `u.path == "/api/schemes"` 改为
   `u.path in ("/api/schemes", "/api/schemes/")` —— 带尾斜杠的 `/api/schemes/` 过去会落到
   遗留的本地 `tools.list_schemes()`，现在与无尾斜杠一样由 `resources.list_schemes`（远端）
   服务。**确保恰好一个处理器服务该路径（远端）。**
5. **清理已失效导入/文档**：`from . import explain, resources, tools` 去掉 `explain`
   （仅被删除的 `/result?explain=1` 分支使用）；模块顶部说明改为「`/api/chat`、`/api/validate`、
   `/api/llm/test` 保留；原 driver 直取端点已下线，任务与方案一律经远端能力服务」。

### 保留（未动）
- `GET /api/tasks/{tid}/files`（远端 `list_task_files`）；
- `/api/validate`、`/api/schemes/content`（远端 `get_scheme_content`）、`/api/schemes/upload`
  （仍是本机落盘）、`/health`、`/api/tools`；
- 全部 `/api/conversations/...` 路由；
- `agent/tools.py` 全部函数、`explain.py`、离线 / 对话路径；
- 快速演示清洗路径（未触碰）。

## 测试
`agent/tests/test_resources_api.py`（真实 HTTP，假远端客户端，不连网）新增 9 例。既有
`SchemeTest` / `TaskFilesTest` 等远端语义用例原样保留，未削弱覆盖。

`SchemeTest`：
- `test_list_schemes_trailing_slash_still_remote`：`GET /api/schemes/` 命中远端，断言罐头
  信封形状且 `mcp.calls[-1] == ("list_schemes", {})`（锁死「只有远端一条路径」）。

新增 `RemovedLegacyRoutesTest`（沿用 `ResourceServerBase` 的真服务）：
- `test_schemes_list_only_remote_not_local`：不给远端罐头信封时 `GET /api/schemes` 返回
  `MCP_UNAVAILABLE`（证明未回落到本地 `tools.list_schemes`）；
- `test_tasks_list_removed`、`test_task_status_removed`、`test_task_result_removed`、
  `test_task_result_explain_removed`、`test_task_samples_removed`、`test_task_report_removed`：
  GET 一律 **404 + `USAGE`**，不再返回本地结果；
- `test_post_tasks_removed`：`POST /api/tasks` 同样 404 + `USAGE`。

说明：仓库内原无用例直接调用这批被删端点（`test_stage2_policy_facts.py` 里的
`http_api.tools.list_tasks` 只是借道同一 `tools` 模块，用运行时事实收集，未走 HTTP 端点，
故不受影响、保持原样），因此本次为**补测**而非改动既有断言。

## 验证
- 全量本地套件（repo 根）：
  `& "...\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q`
  → **426 passed, 240 subtests passed**（基线 417；本次净 +9），保持全绿、无网络。
- `python -m py_compile agent/http_api.py agent/tests/test_resources_api.py` → 通过。

## 影响面
- 仅 `agent/http_api.py` 与配套测试。
- 远端任务与方案相关的 HTTP 入口收敛到远端能力路由；本地 driver 只在 `tools.py` /
  `driver_client.py` 内部保留（供离线 / 对话路径与本地快速演示），不再经 HTTP 端点暴露。
- 后端 / 驱动层、离线路径、快速演示路径行为不变。

## 遗留 / 关注点
- 对已删除路径的请求现在统一落「404 未知路径」（`USAGE`）；若将来需要显式「已下线」提示，
  需另加约定，本任务按既定的「未匹配即未知路径」处理。
- 文档（`docs/agent/...`）里仍有对这批历史端点的描述，属文档层面，未在本任务的代码清理
  范围内。

## 提交
一次性本地提交，仅含 agent 文件（`agent/http_api.py`、`agent/tests/test_resources_api.py`），
未推送。
