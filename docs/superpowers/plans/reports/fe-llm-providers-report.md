# 前端：模型供应商命名保存（长期有效，同名覆盖）

## 状态

已完成。前端测试与生产构建均通过。

## 改动

- `frontend/src/api/llmProviders.ts`
  - `listProviders()` → `GET /api/llm/providers`
  - `saveProvider(payload)` → `POST /api/llm/providers`
  - `deleteProvider(name)` → `DELETE /api/llm/providers/{name}`（名称做 URL 编码）
- `frontend/src/api/types.ts`
  - 增加 `LlmProvider`、`LlmProviderInput` 及列表 / 保存 / 删除响应类型。
- `frontend/src/stores/settings.ts`
  - 增加 `providers` 状态与 `loadProviders()`、`saveProvider(name)`、`applyProvider(name)`、`removeProvider(name)`。
  - 当前 `llm` 仍常驻内存，`.env.local` 仍是初始默认值。
  - `saveProvider` 提交当前连接字段，同名由后端覆盖，成功后刷新列表。
- `frontend/src/components/SettingsForm.vue`
  - 增加「供应商名称」输入。
  - 增加已保存供应商下拉选择与列表；选择或点击名称会预填连接字段。
  - 「保存」有名称时长期保存并提示「已保存（长期有效）」；无名称时保留原来的本次会话保存行为。
  - 每条已保存供应商可删除，删除前经 `window.confirm` 确认。
  - 「测试连接」继续使用当前表单字段。
  - API Key 不展示、不输出。

## 测试

新增 / 扩展：

- `frontend/src/__tests__/llm.providers.test.ts`：三个端点的路径、方法、请求体与名称编码。
- `frontend/src/__tests__/stores.settings.test.ts`：列表读取、按名称保存并刷新、同名覆盖、预填、删除。
- `frontend/src/__tests__/settings.form.test.ts`：名称保存、选择已保存供应商预填、删除确认；原有测试保留。

结果：

- `npm run test`：45 个测试文件，364 条测试全部通过。
- `npm run build`：`vue-tsc --noEmit` 与 Vite 构建通过。

## 说明

- 后端接口按并行实现给出的契约编写。后端未启动时，表单会提示已保存供应商读取失败；当前连接字段仍可编辑与测试。
- 保存失败不会回滚内存中的当前表单值，方便用户修正后重试。
