# 前端模型供应商配置改读 .env.local · 报告

## 状态

已完成。前端 LLM 连接信息默认从 `frontend/.env.local` 读取，保存只在当前会话内存中生效；不再写入浏览器存储。

## 改动文件

- `frontend/src/env.d.ts`
- `frontend/src/stores/settings.ts`
- `frontend/src/components/SettingsForm.vue`
- `frontend/src/__tests__/stores.settings.test.ts`
- `frontend/src/__tests__/settings.form.test.ts`
- `frontend/src/__tests__/chat.send.test.ts`
- `frontend/src/__tests__/chat.test.ts`
- `docs/superpowers/plans/reports/fe-llm-config-file-report.md`

## 具体改动

- 在 `ImportMetaEnv` 声明 `VITE_LLM_API_BASE`、`VITE_LLM_API_KEY`、`VITE_LLM_MODEL`、`VITE_LLM_MODE`，均为可选只读字符串。
- `defaultLlmSettings()` 改为读取上述四个环境变量并去掉首尾空白；缺失为空字符串，模式缺失或为空时回落到 `llm`。
- 删除设置 store 中所有 localStorage 读写和 `LLM_STORAGE_KEY` 导出。
- `update()`、`save()` 只更新内存；`load()`、`reset()` 重新套用环境变量默认值。
- 保留 `hasOverride` 判断（三个供应商字段非空）和 `toOverride()` 的 `enabled: true` 行为。
- 设置表单底部提示改为说明连接信息来自前端 `.env.local`，不进浏览器存储、不上传服务端，修改后重启前端生效；保存提示改为本次会话生效。页面不展示密钥内容。
- 测试改用 `vi.stubEnv` 提供默认值，覆盖环境变量默认值、空白裁剪、模式回落、表单预填、内存保存、`load()`/`reset()`、`hasOverride`/`toOverride`，并确认保存后旧 localStorage 键仍为空。
- 对话相关测试补充环境变量隔离，避免本地 `.env.local` 影响“未配置”场景。

## 命令与结果

- `npm run test`：44 个测试文件、352 个用例全部通过。
- `npm run build`：`vue-tsc --noEmit` 与 Vite 构建通过；仅有原有的 chunk 超过 500 kB 提示。

## 注意事项

- 保存只作用于当前页面会话，刷新后恢复为 `.env.local` 的值；改配置文件后需要重启前端开发服务。
- Vite 会在构建时把 `VITE_*` 变量写入前端产物，因此这种方式不把密钥放进浏览器存储，但拿到前端代码或构建产物的人仍可能看到密钥。需要真正保密时应改由服务端保管。
- 测试通过环境变量隔离读取，避免把本机 `.env.local` 的内容带入断言或输出。
