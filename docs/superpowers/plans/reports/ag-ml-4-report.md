# AG-ML-4 报告：模型下载中转

## 状态
完成。测试先行（RED→GREEN），全量套件绿。

## 改动
- `agent/mcp_download.py`
  - 新增 `open_model_stream(config, model_id, relpath, transport=None) -> (iterator, headers, error)`：
    - URL = `base_url(config)` + `/download/model/{quote(model_id)}/{quote(relpath, safe="/")}`；
      `relpath` 允许多级（保留 `/`），`model_id` 按单段编码。
    - `model_id` 守卫 `_safe_model_id`：必须匹配 `[A-Za-z0-9._-]+` 且不是 `.` / `..`，
      否则本机返回 `USAGE`（不发请求）。
    - `relpath` 复用既有 `_safe_relpath`：拒 `..` 段 / 绝对路径 / 反斜杠 / NUL，
      否则本机返回 `FORBIDDEN_PATH`（不发请求）。
    - 成功/失败语义完全复用 `_open` / `_error_from_response`：同一 Bearer 头、
      保留服务端 `error.code` 与细节（如 `reason_kind`）、同一 `STATUS_BY_CODE` 映射、
      同一 `_iter_stream` 逐块转发（不整份读入内存）。
  - 文档字符串更新为三条只读路由；新增 `USAGE_ERROR` 常量。
- `agent/http_api.py`
  - 新增 `GET /api/models/download?model_id=&path=`，复用 `_relay_download` 流式中转：
    配置取自运行时客户端（`Runtime.mcp_client.config`，经 `_runtime_or_error` /
    `_get_runtime`）；`NullMcpClient` / 未配置 → `MCP_UNAVAILABLE`；失败回结构化
    错误信封 + 与既有下载相同的 HTTP 状态映射。
  - 既有 `/api/files/download`、`/api/data/published/download` 行为零改动。
- `agent/tests/test_ml_download.py`（新增，16 例）：全部使用 `httpx.MockTransport`，
  不连网、不碰 `127.0.0.1:8900`。

## 测试
`python -m pytest agent/tests -q` → **476 passed, 339 subtests passed**（新增 16 例）。
单文件：`agent/tests/test_ml_download.py` → 16 passed。

覆盖：
- 成功流式返回字节 + 透传 `Content-Disposition` / `Content-Length` + Bearer 头；
- 多级 `relpath`（`sub/coords.csv`）的斜杠保留；
- `FILE_NOT_FOUND` / `FORBIDDEN_PATH` 原样保留（含 `reason_kind`）且状态映射正确；
- `..` / 绝对 / 反斜杠 / NUL 越界 `relpath` 本机拒绝、不发请求；
- `model_id` 含 `/`、空串、空格、反斜杠、NUL、`.` / `..` → `USAGE`，不发请求；
- 契约形状 `ml-YYYYMMDD-HHMMSS-xxxx` 放行；
- 未配置 → `MCP_UNAVAILABLE`；错误信封不含口令 / `Bearer`；
- `http_api` 中转：成功流式、404 信封、越界本机 400、`NullMcpClient` → 503。

## 约束遵循
- 测试全程无网络（`httpx.MockTransport`）。
- 口令只进请求头，未写日志 / 信封 / 用户可见输出。
- 只改 `agent/mcp_download.py`、`agent/http_api.py`，新增 `agent/tests/test_ml_download.py`。
- 既有任务 / 已发布下载行为零改动。

## 关注点
- `model_id` 在严格正则之外额外拒绝 `.` / `..`（正则本身会放行纯点号）；这是防
  URL 点段归一化的纵深防御，不影响契约形状。
- `_relay_download` 统一从 `Runtime.mcp_client.config` 取配置，与任务/已发布下载
  共用同一条路径，避免第二套配置读取逻辑。
