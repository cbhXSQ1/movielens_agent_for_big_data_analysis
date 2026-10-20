# AG LLM Providers — 报告

## 状态

已完成。命名的大模型供应商档案落本机文件，支持保存/同名覆盖、列出、删除；
设置面板无需浏览器存储，也不需要新增环境变量。

## 变更文件

- `agent/llm_providers.py`（新增）
- `agent/http_api.py`
- `agent/tests/test_llm_providers.py`（新增）
- `docs/superpowers/plans/reports/ag-llm-providers-report.md`（本报告）

## 存储

`PROVIDERS_PATH = <repo>/config/llm.providers.json`，该路径已在 `.gitignore` 中。

文件形状：

```json
{
  "providers": {
    "<name>": {
      "api_base": "https://api.example.com/v1",
      "api_key": "sk-...",
      "model": "model-a",
      "mode": "llm",
      "updated_at": "2026-10-20T06:30:00.123456+00:00"
    }
  }
}
```

- `list_providers()`：始终返回 `ok=true`；缺失、不可读、非法 JSON、错误顶层形状
  均按空档案处理；条目按名称排序。
- `save_provider(name, api_base, api_key, model, mode="llm")`：校验名称非空且为单段
  （拒绝 `/`、`\`、`..`）及三个必填字段非空；同名覆盖；临时文件 + `os.replace`
  原子落盘；返回保存后的条目。
- `delete_provider(name)`：名称非法 → `USAGE`；不存在 → `FILE_NOT_FOUND`；
  成功 → `{"ok": true, "name": ...}`。
- 读改写加进程内互斥锁，避免同进程并发保存互相覆盖。
- API Key 会写入档案并随列表读回（设置面板需要回填），但所有错误信息与日志都
  不包含 Key。

## HTTP 接口

所有 JSON 请求/响应均为 UTF-8。错误是结构化信封；`USAGE` 返回 HTTP 400，
`FILE_NOT_FOUND` 返回 404，写入失败 `IO_ERROR` 返回 500。

### 1. `GET /api/llm/providers`

请求无 body。HTTP 200。

```json
{"ok": true, "providers": []}
```

有档案时：

```json
{"ok": true, "providers": [{"name": "work", "api_base": "https://api.example.com/v1", "api_key": "sk-example", "model": "model-a", "mode": "llm", "updated_at": "2026-10-20T06:30:00.123456+00:00"}]}
```

### 2. `POST /api/llm/providers`

请求（`mode` 可省略，默认 `"llm"`）：

```json
{"name": "work", "api_base": "https://api.example.com/v1", "api_key": "sk-example", "model": "model-a", "mode": "llm"}
```

成功 HTTP 200（同名再次 POST 会覆盖，不会新增第二条）：

```json
{"ok": true, "provider": {"name": "work", "api_base": "https://api.example.com/v1", "api_key": "sk-example", "model": "model-a", "mode": "llm", "updated_at": "2026-10-20T06:30:00.123456+00:00"}}
```

校验失败 HTTP 400：

```json
{"ok": false, "error": {"code": "USAGE", "message": "api_key 不能为空"}}
```

名称非法时 HTTP 400（消息文本不包含 Key）：

```json
{"ok": false, "error": {"code": "USAGE", "message": "供应商名称不能为空，且必须是单段名称（不能含 /、\\ 或 ..）"}}
```

### 3. `DELETE /api/llm/providers/{name}`

名称需 URL 编码（例如中文名、空格）。请求无 body。成功 HTTP 200：

```json
{"ok": true, "name": "work"}
```

不存在 HTTP 404：

```json
{"ok": false, "error": {"code": "FILE_NOT_FOUND", "message": "供应商档案不存在"}}
```

名称非法 HTTP 400：

```json
{"ok": false, "error": {"code": "USAGE", "message": "供应商名称不能为空，且必须是单段名称（不能含 /、\\ 或 ..）"}}
```

## 测试

`agent/tests/test_llm_providers.py` 共 33 例，全部离线、无网络：

- 保存/列出、文件形状、默认 `mode=llm`；
- 同名覆盖且更新时间刷新；
- 按名称排序；
- 删除指定项、删除不存在项；
- 非法名称（空、路径分隔符、`..`）与空必填字段；
- 缺失/损坏/错误形状文件返回空档案，之后仍可正常保存；
- 模拟写入失败时返回 `IO_ERROR`，并断言错误 JSON 不含 API Key；
- 真 HTTP 服务覆盖 GET/POST/DELETE 往返、URL 编码中文名、400/404 状态和
  “错误不回显 Key”。

TDD 过程：先写测试，确认因模块不存在而 RED；实现存储层后，HTTP 用例继续因
路由缺失 RED；补路由后转 GREEN。

全量验证（repo 根）：

```text
533 passed, 353 subtests passed in 65.04s
```

基线 500 例，本次新增 33 例；无网络请求，原有用例保持全绿。

## 关注点

- 按需求，列表接口会回传 `api_key` 供设置面板回填；档案是本机明文 JSON，
  依赖文件系统权限与 `.gitignore` 保护，不要在报告中或日志里扩散真实 Key。
- 现有 HTTP 服务使用 `Access-Control-Allow-Origin: *`；新增接口同样继承该 CORS
  策略。若未来服务暴露到非本机网络，建议限制来源/增加本机鉴权。
- 删除接口对非法名称与不存在名称分别返回 400/404；前端应据此区分“改名字”
  与“已不存在”。
