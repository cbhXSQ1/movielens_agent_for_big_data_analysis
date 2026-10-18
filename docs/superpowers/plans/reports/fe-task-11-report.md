# FE-11 报告 —— 前端对齐「删除方案」远端能力

任务：新增 `POST /api/schemes/delete` 的前端接线（与上传成对）。
范围：仅前端（`frontend/`）。Agent 侧并发改动（`agent/`、`hadoop/`）未触碰。
背景：远端能力 `delete_scheme` 的 Agent 端点返回 `{ok:true, deleted:true, scheme, schemes, note}`；失败回结构化错误信封，码为
`USAGE`（含「默认方案永不可删」）/ `FORBIDDEN_PATH` / `TASK_ALREADY_RUNNING`（带 `occupier`）/ `FILE_NOT_FOUND`（带 `existing`）。
随仓库交付的两份默认方案 `cleaning_rules.v1.json`、`scoring_scheme.v1.json` 永不可删。

## 1. 改动清单

| 文件 | 改动 |
| --- | --- |
| `frontend/src/api/types.ts` | 新增 `SchemeDeleteResponse`：`deleted?` / `scheme?: SchemeInfo` / `schemes?: SchemeInfo[]` / `note?`（宽松，继承 `BaseEnvelope`）。 |
| `frontend/src/api/resources.ts` | 新增 `deleteScheme(name)` → `POST /api/schemes/delete`，JSON body `{name}`（复用既有 `postJson`，与上传封装同风格）；补注释列出各错误码。 |
| `frontend/src/stores/resources.ts` | 新增 `deleteScheme(name)` action：调 API 成功后 `await loadSchemes()` 就地重列；**失败原样抛出、不吞**，交视图层按码提示。 |
| `frontend/src/components/resources/SchemeSection.vue` | 每行自定义方案加「删除」按钮，走 `NPopconfirm` 二次确认（文案「确定删除「x」？此操作不可撤销。」）；两份默认方案不渲染删除入口；成功后列表已由 store 刷新；`TASK_ALREADY_RUNNING` 显示「有任务在运行，等它结束再删」，其余走 `describeError` 的码表中文；错误挂 `data-testid="scheme-delete-error"`。列表项常只给 `path`（`config/x.json`），删除前取 basename 传给接口、默认方案也按 basename 识别。 |
| `frontend/src/api/http.ts` | **未改**：`TASK_ALREADY_RUNNING` 既有中文「已有任务正在运行」已可读，且 `ERROR_CODE_TEXT` 全表无「冒烟/对账/口径」；本次未引入新错误码（`USAGE`/`FORBIDDEN_PATH`/`FILE_NOT_FOUND` 均已有中文）。 |

## 2. 测试（TDD：先失败后通过）

新增 / 修改用例（先跑红，再实现转绿）：

1. `frontend/src/__tests__/resources.test.ts` — `deleteScheme` 的 `POST /api/schemes/delete` + JSON body `{name}` 与回执解析；`TASK_ALREADY_RUNNING` 抛 `ApiError` 且 `detail.occupier` 保留；码表中文含「任务」且不含禁用词。
2. `frontend/src/__tests__/stores.resources.test.ts` — `deleteScheme` 后 `listSchemes` 恰调用一次（就地刷新）；失败时抛出且**不**刷新列表。
3. `frontend/src/__tests__/resources.panel.test.ts` — 假后端 `/api/schemes/delete`：默认方案（path-only）无删除按钮、自定义方案有；确认后调用接口且 body 为单层文件名、方案列表被二次请求；`TASK_ALREADY_RUNNING` → 出现「有任务在运行，等它结束再删」。

先跑：7 条新用例因 `deleteScheme` 未实现 / 无删除入口而失败；实现后全绿。未削弱、未删除任何既有用例。

## 3. 结果

命令（在 `frontend/` 下）：

```
npm run test      # vitest run
npm run build     # vue-tsc --noEmit && vite build
```

- 测试：**31 个文件 / 241 条用例全部通过**（改动前 31 / 233；净增 8 条）。
- 构建：`vue-tsc` 无类型错误；`vite` 生产构建成功（`✓ built in 7.72s`）。唯一提示为 Vite 的 chunk 体积告警（既有状态，非本次引入）。

## 4. 结论与关注点

- 状态：前端全量用例绿（241/241），类型检查与生产构建干净；仅改动前端文件，未与 Agent 侧并发改动重叠。
- 默认方案护栏：以**文件名（basename）**判定，兼容 driver 只回 `path`（`config/cleaning_rules.v1.json`）的真实形状；两份默认方案在 UI 上直接没有删除入口，与远端「无条件拒绝」一致。
- 错误提示：`TASK_ALREADY_RUNNING` 单独给「等它结束」的明确文案；`FILE_NOT_FOUND` / `FORBIDDEN_PATH` 走码表中文字；`USAGE`（如默认方案）回显远端给出的中文说明。删除成功后列表由 store 的 `loadSchemes()` 刷新，不依赖回执里的 `schemes`。
- `http.ts` 无需改动：所需码的中文文案本已齐备，且无禁用词；如后续远端再增码，按既有 `ERROR_CODE_TEXT` 约定补一行即可。
