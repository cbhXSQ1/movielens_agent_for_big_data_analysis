# FE-1 报告：移除原始数据上传，改为只读 + 提示

计划：`docs/superpowers/plans/2026-10-18-frontend-contract-align.md` → Task FE-1
分支：`reconstruction`
范围：仅 frontend 文件

## 变更文件

修改（提交）：
- `frontend/src/components/resources/RawDataSection.vue` — 移除隐藏 file input、上传按钮、上传状态与 `pick`/`onPick`/`refresh` 中的上传逻辑及相关 CSS；保留只读数据集列表；新增提示行「原始数据在运行数据的机器上维护，请在那里上传。」（`data-testid="raw-hint"`）。空态文案由「暂无原始数据，可上传 .dat 或 .zip」改为「暂无原始数据」。
- `frontend/src/api/resources.ts` — 删除 `uploadRawData` 与 `RawUploadResponse` 导入；保留 `requestForm`（`uploadScheme` 仍在用）；更新顶部注释。
- `frontend/src/stores/resources.ts` — 删除 `uploadRaw` 及其导出、`uploadRawData`/`RawUploadResponse` 导入；更新顶部注释。保留 `loadRaw`/`listRawData`。
- `frontend/src/api/types.ts` — 删除 `RawUploadedFile`、`RawUploadResponse`（已确认无其它引用）。
- `frontend/src/__tests__/stores.resources.test.ts` — 移除 `uploadRawData` mock 与「uploadRaw 成功后就地刷新」用例；新增「没有原始数据上传动作」用例（断言 `'uploadRaw' in store === false`）。
- `frontend/src/__tests__/resources.test.ts` — 移除 `uploadRawData` 导入与原始数据上传 FormData / 错误码用例；将错误码路径用例改用 `uploadScheme` 覆盖（`UPLOAD_TOO_LARGE`）。
- `frontend/src/__tests__/resources.panel.test.ts` — 移除原始数据上传 handler/用例；新增「不再渲染上传按钮，并显示维护位置提示」与「只读列表照常渲染数据集」用例。

未提交（按“仅提交 frontend 文件”约束）：本报告。

## TDD 记录

1. RED（写失败测试后运行）：
   `npm run test -- stores.resources resources.panel resources.test`
   结果：`Test Files 2 failed | 1 passed (3)`，`Tests 2 failed | 27 passed (29)`；失败项正是新增的「没有原始数据上传动作」与「不再渲染上传按钮，并显示维护位置提示」，失败原因均为 `expected true to be false`（上传能力仍在），符合预期。
2. GREEN（实现后运行，同上命令）：`Test Files 3 passed (3)`，`Tests 29 passed (29)`。

## 全量验证

- 命令：`npm run test`（在 `frontend/`）
  输出：`Test Files 29 passed (29)`，`Tests 198 passed (198)`。
- 命令：`npm run build`（`vue-tsc --noEmit && vite build`）
  输出：类型检查无错误；`vite v6.4.4` 构建成功，`✓ built in 11.36s`（`dist/assets/index-*.js 649.18 kB`）。

## 文案约束

新增用户可见文案「原始数据在运行数据的机器上维护，请在那里上传。」不含「冒烟 / 对账 / 口径」。

## 关切 / 备注

- `requestForm` 未删除：`uploadScheme` 仍依赖它。
- 计划原文把组件级只读断言放在 `resources.test.ts`，但该文件为 API 层测试；为匹配既有约定，组件级断言落在 `resources.panel.test.ts`（已有面板挂载与 raw 段用例），store 级断言落在 `stores.resources.test.ts`。
- 远端 `/api/data/raw/upload` 端点仍在后端，本次仅前端不再调用；后续如需彻底下线端点需另在 Agent/远端处理。
