# FE-2 报告：任务状态映射收口与中止用例

计划：`docs/superpowers/plans/2026-10-18-frontend-contract-align.md` → Task FE-2
范围：仅 frontend 文件

## 变更文件

修改（提交）：
- `frontend/src/components/results/ResultsPanel.vue` — 结果外壳任务徽标的状态文案不再直接读 `task.status_cn`；改为 `statusText = statusLabel(task)`（引入 `@/components/progress/taskDisplay`）。`status_cn` 缺失/空白时经枚举兜底，仍显示「已中止」等，不再退化为空白。
- `frontend/src/__tests__/progress.panel.test.ts` — 新增 `FH.4 · 中止状态显示与终态行为`：
  1. 纯映射断言：`statusLabel({status:'aborted',status_cn:'已中止'})==='已中止'`、`statusTagType('aborted')==='warning'`、`isTerminal('aborted')===true`；
  2. 列表中的 `aborted` 任务显示「已中止」，状态标签 `type` 为 `warning`，详情仍显示「取结果」按钮（终态）；
  3. `status_cn` 缺失但 `status='aborted'` 时，列表与详情均经兜底显示「已中止」，详情仍可取结果。
- `frontend/src/__tests__/results.test.ts` — 新增结果外壳用例：把 `latestResult.task` 置为 `aborted` 且 `status_cn` 为空，断言徽标经 `statusLabel` 兜底显示「已中止」。

未提交（按“仅提交 frontend 文件”约束）：本报告。

## 确认（无需扩展）

`frontend/src/components/progress/taskDisplay.ts` 已是标签/配色/终态集合的唯一来源，且已满足本次要求，未作改动：
- `STATUS_LABELS.aborted = '已中止'`（`taskDisplay.ts:16`）
- `STATUS_TAG_TYPES.aborted = 'warning'`（`taskDisplay.ts:24`）
- `TERMINAL_STATUSES` 含 `aborted`（`taskDisplay.ts:28-32`）
- `TaskDetail.vue` / `TaskListItem.vue` 早已经 `statusLabel(task)` 渲染，无直接 `status_cn` 读取。

收口后，`frontend/src` 内直接读取 `task.status_cn` 的位置仅剩类型定义与 store 初值（`api/types.ts:238`、`stores/tasks.ts:24`）与 `taskDisplay.ts` 自身兜底逻辑，无组件直接消费。

## TDD 记录

1. RED（写测试后运行）：`npm run test -- progress.panel results.test`
   结果：`Test Files 2 failed (2)`，`Tests 2 failed | 12 passed (14)`。
   失败项：`results.test.ts` 的「status_cn 缺失时经 statusLabel 兜底显示已中止」（`expected 'a1' to contain '已中止'`，因 ResultsPanel 仍直接读 `status_cn`）；`progress.panel.test.ts` 的 warning 断言（初版以 CSS 类断言，Naive UI 未输出 `n-tag--warning` 类）。
2. GREEN（实现 + 修正断言后，同上命令）：`Test Files 2 passed (2)`，`Tests 14 passed (14)`。

## 全量验证

- 命令：`npm run test`（在 `frontend/`）
  输出：`Test Files 29 passed (29)`，`Tests 202 passed (202)`。
- 命令：`npx vue-tsc --noEmit`
  输出：无输出（类型检查通过）。

## 文案约束

新增/展示文案「已中止」「取结果」等不含「冒烟 / 对账 / 口径」。

## 关切 / 备注

- Naive UI `NTag` 不输出 `n-tag--warning` 之类的类型类名；测试改以 `findComponent(NTag).props('type') === 'warning'` 断言，更贴近组件契约。
- `statusLabel` 兜底对未知状态返回 `status` 本身或「未知」；ResultsPanel 现在在 `task` 存在时总会显示一段状态文案（此前 `status_cn` 为空则不显示），行为更一致。
