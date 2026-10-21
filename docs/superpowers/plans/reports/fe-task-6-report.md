# FE-6 报告：长任务进度提示（轮询不变）

## 任务
在长任务轮询场景下，界面如实反映任务是否仍在运行；中止时进度停在中断处，不强制到 100%。

## 改动
- `frontend/src/components/progress/taskDisplay.ts`
  - 新增纯展示辅助：`isActiveTask(status)`（queued/running）、`activeHint(status)` → 「仍在运行，可继续等待」、`abortedHint(status)` → 「任务已中止，进度停在中断处」。非对应状态返回 `null`，不展示。
- `frontend/src/components/progress/TaskListItem.vue`
  - 列表项在排队/运行时显示提示（`data-testid="task-running-hint-<id>"`）；中止时显示停止说明（`task-aborted-hint-<id>`）。保留原有阶段名与百分比显示。
- `frontend/src/components/progress/TaskDetail.vue`
  - 详情区在排队/运行时显示提示（`detail-running-hint`）；中止时显示说明（`detail-aborted-note`）。进度字段（`detail-percent`）继续取 `clampPercent(progress_percent)`，**未**在任何状态下强制 100%。未改动 FE-5 引入的版本/时间边界展示，未回退 FE-5。
- `frontend/src/views/panels/TasksPanel.vue`：无需改动。

## TDD
- 先在 `frontend/src/__tests__/progress.panel.test.ts` 增加 `FH.5 · 长任务进度提示（轮询不变）` 三条用例（运行中提示、排队中提示、中止停在 60% 且不强制 100%），运行确认 3 条失败（元素不存在）。
- 实现后转绿。

## 验证
- `npm run test -- progress.panel`：13 passed。
- `npm run test`（全量）：30 文件 / 224 用例全绿。
- `npm run build`（`vue-tsc --noEmit && vite build`）：通过，无类型错误。

## 约束核对
- 文案不含「冒烟 / 对账 / 口径」。
- 仅改动前端文件。
- 中止时进度停在停下的值（测试断言 60%），未强制 100%。

## 关注点
- 提示为前端根据状态即时展示；轮询机制本身未改动（按任务要求「轮询不变」）。
