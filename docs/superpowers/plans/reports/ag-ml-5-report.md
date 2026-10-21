# AG-ML-5 报告：模型列表 / 详情

## 状态
完成。测试全绿。

## 交付
- **新建** `agent/ml_models.py`
  - `list_models(client)` → `client.call_tool("list_ml_models", {})` → `{"models":[...]}`（逐条保留对象的契约字段，过滤非对象项）。
  - `get_model(client, model_id)` → `client.call_tool("get_ml_model", {"model_id": model_id})` → `{"model":{...}}`。
  - `model_download_path(model_id, path)` → `/api/models/download?model_id=<urlencoded>&path=<urlencoded>`（两值 `quote(..., safe="")`，`None` 退化为空串）。
  - 复用 `resources._remote`：`client is None` → 结构化 `MCP_UNAVAILABLE`（不发网络请求）；远端 `ok=false` → 原样保留 `error.code`/`message` 与细节；非对象信封 → `DRIVER_BAD_OUTPUT`。
- **修改** `agent/http_api.py`
  - 顶层导入 `ml_models`；在 `_get_resources` 新增 `GET /api/models`（含尾斜杠）与 `GET /api/models/{model_id}`，经 `_resource_call` 用运行时客户端（`self._get_runtime().mcp_client`）调用，返回结构化信封，失败保留错误码。
  - 路由置于既有 `/api/models/download` 精确匹配之后，避免遮蔽下载中转。
- **测试** `agent/tests/test_models_api.py`（12 用例，假客户端，全程不连网）
  - 模块层：list/get 出口形状 + 调用参数；错误码与细节透传（`DRIVER_UNREACHABLE` / `MODEL_NOT_FOUND`）；`None` 客户端 → `MCP_UNAVAILABLE`；`model_download_path` 编码（含 `/`、空格、`None`）。
  - HTTP 层：真起服务注入假运行时，`GET /api/models`、`GET /api/models/{id}` 成功形状与错误码透传；`NullMcpClient` → `MCP_UNAVAILABLE`。

## 测试摘要
- 新增用例：`12 passed`。
- 全量：`492 passed, 353 subtests passed`（无回归；基线 480，+12）。

## 关注点 / 备注
- `list_models` 采用「薄封装」透传远端条目（过滤非对象项），不逐字段重映射，以兼容契约的加性变更。
- `ml_models.py` 复用了 `resources.py` 的私有 `_remote`，保证与资源端点错误语义完全一致；若日后需要独立演进可再抽出公共模块。
- `get_model` 未在 Agent 侧对 `model_id` 做形状校验，交给远端（契约 §4.1 回 `USAGE`），保持薄封装。
