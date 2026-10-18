# Task 12 报告：二进制下载中转（流式）

## 状态
完成（本地绿；未推送）。

## 目标
把 `/api/files/download` 从「整份读进内存再吐」改为「Agent 带口令经远端两条只读路由
流式取回并中转」，并新增已发布数据下载端点。浏览器始终不持有口令。

## 改动文件
- 新增 `agent/mcp_download.py`
- 新增 `agent/tests/test_mcp_download.py`
- 修改 `agent/http_api.py`（下载路由改中转 + 新增 published 下载）
- 修改 `agent/resources.py`（移除本机下载专用 `download_target`；`resolve_file` 保留本机用途）
- 修改 `agent/tests/test_resources_api.py`（下载用例改为远端中转断言）

## `agent/mcp_download.py` — 接口
- `open_task_stream(config, hadoop_task_id, relpath, transport=None) -> (iterator, headers, error)`
- `open_published_stream(config, data_version, table, transport=None) -> (iterator, headers, error)`
- `status_for_code(code, default=502)` — 错误码 → 浏览器 HTTP 状态
- URL：`config` 根地址（去掉 `/mcp` 后缀）+ `/download/task/…` 或 `/download/published/…`

要点：
- 普通 HTTP GET，请求头 `Authorization: Bearer <config.token>`（口令**只**进请求头）。
- `httpx.Client(transport=...)` 可注入 `httpx.MockTransport`（测试不连网）。测试另可通过
  `config.transport`（自定义属性）注入；生产 `McpConfig` 无此属性 → 走真实网络。
- 成功：返回 `response.iter_bytes()` 迭代器（流式），透传 `Content-Disposition` /
  `Content-Length`（远端没给就不造）。迭代器收尾统一关响应与客户端（对端断开也关）。
- 非 2xx：读取信封 JSON，**原样保留** `error.code` / `error.message` 及其余细节
  （如 `reason_kind`、`path`），不把结构化错误降级成泛化错误。无信封时按 HTTP 状态兜底。
- 连接异常翻成可读说明（复用 `mcp_http_client.explain`），码为 `DRIVER_UNREACHABLE`。
- 配置缺失 / 不可用 → `MCP_UNAVAILABLE`，不发任何请求。

状态映射（与远端 `hadoop/mcpserver/server.py` 同套）：
`FILE_NOT_FOUND→404`、`FORBIDDEN_PATH→400`、`UNAUTHORIZED→401`、`FORBIDDEN_HOST→403`、
`DRIVER_UNREACHABLE→503`、`DRIVER_TIMEOUT→504`；`MCP_UNAVAILABLE→503`；未知码 502。

## `agent/http_api.py` — 接线
- `GET /api/files/download?task_id=&path=` → `_relay_download(open_task_stream…)`
- `GET /api/data/published/download?data_version=&table=` → `_relay_download(open_published_stream…)`
- `_relay_download`：从 `self._get_runtime().mcp_client.config` 取配置（`NullMcpClient`
  或未配置 → 无 `config` → 结构化 `MCP_UNAVAILABLE`）；取流成功则 `_stream_response` 写状态行 +
  透传下载头，然后 `for chunk in iterator: self.wfile.write(chunk)` **分块转发**（不整份读入）。
  长度缺失时用 `Connection: close` 界定消息体，不臆造 `Content-Length`。
- 远端错误按上面映射转 HTTP 状态 + `{"ok": false, "error": {...}}` 信封。
- 浏览器契约不变：前端仍只请求 Agent 的 URL。

## 测试（全部无网络）
`agent/tests/test_mcp_download.py`（13 项）：
- 模块层：成功透传字节 + 下载头、URL 形状、返回迭代器而非 bytes、
  结构化错误码原样保留（`FILE_NOT_FOUND` / `FORBIDDEN_PATH` + `reason_kind`）、
  `UNAUTHORIZED→401`、published URL 与字节、未接入 `MCP_UNAVAILABLE`、口令不外泄。
- http_api 层（真起服务 + 假客户端 + 假传输）：中转写入字节与头、错误返回信封 + HTTP 状态、
  published 下载、`NullMcpClient` → `MCP_UNAVAILABLE`/503。

`agent/tests/test_resources_api.py`：原「本机中转」下载用例改为远端中转断言。

## 验证
- `pytest agent/tests/test_mcp_download.py agent/tests/test_resources_api.py -q` → 35 passed
- `pytest agent/tests -q` → **388 passed**, 237 subtests passed（前次 375；新增 13）
- 全程未访问 127.0.0.1:8900；口令未进日志 / 信封 / 响应。

## 关切 / 备注
- 全量套件首次运行时 `test_stage3b_stream.py::TestAsyncRuntime::
  test_run_turn_async_two_conversations_isolated` 曾偶发失败（store OperationalError），
  单独运行与本次全量重跑均通过；属既有偶发，与本次改动无关。
- 下载端点未做「路径 / 版本」的本机预校验，一律由远端判定（避免两端各说一套）；
  越界等错误由远端结构化返回并原样转述。
- 计划文件列了修改 `resources.py`；本次仅移除已无引用的 `download_target`，未动其余逻辑。

---

# Task 12 修复报告（review finding，Important）：越界路径本机拦截

## 问题
出站下载 URL 用 `quote(..., safe="/")` 拼，但 httpx 发送前按 RFC 3986 归一化点段：
`../../etc/passwd` 被静默改写（路由段丢失 → 普通 404 报成 `FILE_NOT_FOUND`），
`../../published/v1/ratings` collapsing 成 published 路由且 `task_id` 被丢弃。
结果：相对穿越输入永远走不到远端 `FORBIDDEN_PATH` 契约。原
`test_forbidden_path_code_preserved_and_status_mapped` 用的是 `../../etc/passwd`，
但其 MockTransport 忽略请求 URL，给的是假信心。

## 改动
`agent/mcp_download.py`
- 新增 `FORBIDDEN_PATH_ERROR = {"code": "FORBIDDEN_PATH", "message": "该路径不允许访问"}`。
- 新增本机守卫 `_has_forbidden_form` / `_safe_relpath` / `_safe_segment`：
  - `open_task_stream(relpath)`：拒绝对路径（`/` 开头）、含 `\` 或 NUL、含 `..` 段；
    多段 `a/b/c` 仍放行；隐藏/控制文件判断留给远端（第二道防线）。
  - `open_published_stream(data_version, table)`：两者都须是**单一分段**，
    拒绝含 `/`、为 `.`/`..`、绝对或含 `\`/NUL。
  - 命中即 `return None, None, dict(FORBIDDEN_PATH_ERROR)`，**不发任何请求**。
  - 原 `.replace("\\", "/")` 归一化删除（反斜杠现在直接拒，不再静默转义）。
- 放行的路径里已无 `..` 段，httpx 归一化不会再折叠掉路由段，路径原样到达服务端。

`agent/http_api.py`
- `_stream_response` 分块转发循环的 `except` 增加 `httpx.HTTPError`（含 `ReadError`）：
  远端转发中途断流不再从处理函数冒出来（头/部分体已发出，无法再回错误，按断流收尾）；
  惰性导入 httpx，缺依赖时降级为原异常集。

## 测试（全部无网络，未访问 127.0.0.1:8900）
`agent/tests/test_mcp_download.py`（17 项，原 13）：
- `test_open_task_stream_relays_bytes_and_headers`：MockTransport **在 handler 内断言**
  出站 URL 为 `…/download/task/H1/cleaned/x/ratings.dat`（路径完整到达）。
- `test_forbidden_path_code_preserved_and_status_mapped`：改用本机放行的普通路径 `x`，
  断言请求确已发出，且服务端 `FORBIDDEN_PATH` 信封与 `reason_kind` 原样保留、映射 400。
- 新增 `test_task_traversal_rejected_locally_without_request`（`../../etc/passwd` →
  本机 `FORBIDDEN_PATH`、请求不发）与
  `test_task_other_forbidden_paths_rejected_locally_without_request`
  （绝对路径 / 反斜杠 / NUL / 中间 `..`）。
- 新增 `test_published_forbidden_segments_rejected_locally_without_request`
  （版本或表名带 `/`、`..`、反斜杠 → 本机拒）。
- 新增 `test_stream_response_swallows_upstream_read_error`：用最小 `self` 替身直调
  `Handler._stream_response`，迭代器中途抛 `httpx.ReadError` 时不外泄，保留已写字节。

## 验证
```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
392 passed, 237 subtests passed in 41.84s
```
定向：`pytest agent/tests/test_mcp_download.py -q` → `17 passed in 2.66s`。
（修复前全量 388 passed；净增 4 项。）

## 关切 / 备注
- 本机守卫先于配置可用性检查：配置不可用 + 越界路径会得 `FORBIDDEN_PATH` 而非
  `MCP_UNAVAILABLE`（无既有用例覆盖该组合；如需反向优先可调整）。
- 守卫只看字面 `..` 段；`%2e%2e` 等编码穿越由服务端解码后的第二道防线兜底。
