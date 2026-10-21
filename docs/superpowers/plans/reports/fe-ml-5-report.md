# FE-ML-5 前端收口 报告

**任务：** Task FE-ML-5（迭代二 ML 前端收口）。
**范围：** 仅前端文件（`frontend/`）。
**状态：** 完成 —— `npm run test` 与 `npm run build` 均全绿。

## 变更

### 1. 百分比助手去重（单一事实源）

- **单一事实源：** `frontend/src/utils/format.ts` 的 `formatPercent`（FE-ML-3 引入），
  保持 0–1 小数 ×100、默认两位、缺失/非数 → 空字符串的兜底语义。
- **删除重复实现：** `frontend/src/components/results/ml/mlMetrics.ts` 中的本地
  `formatPercent` 移除（其余 `toNumber`/`formatNumber`/`formatInt`/规范化函数不动）。
- **调用方更新：**
  - `frontend/src/components/results/ml/ClassificationCard.vue` 改为从 `@/utils/format`
    引入 `formatPercent`，`mlMetrics` 仅保留 `formatInt`/`normalizeClassificationMetrics`/`orDash`。
  - `frontend/src/__tests__/ml.views.test.ts` 的 `formatPercent` 引入源同步改为 `@/utils/format`。
- 行为不变：空串兜底、×100、可配小数位；既有断言全部照常通过。

### 2. 增补关键用例（不重复既有覆盖）

- `frontend/src/__tests__/ml.registry.test.ts`：**无 `task_type` 时按 `dimensionality_reduction`
  板块推断**降维视图（既有用例只覆盖 clustering 推断，此处补降维推断分支 + 数值渲染）。
- `frontend/src/__tests__/models.panel.test.ts`：**未知 `role` 归入「其它」且已知分组按固定顺序在前**
  （既有用例只覆盖五个已知角色存在性，未覆盖未知角色兜底与分组排序）。

### 3. 既有测试

未删除、未弱化任何既有用例；仅同步了一处 import 来源。

## 验证证据

- `npm run test`：`Test Files 39 passed (39)`，`Tests 298 passed (298)`。
  - 基线为 38/290；本次新增 2 条（+2）；并发 FE-ML-4 任务另带入其自身用例。
- `npm run build`（`vue-tsc --noEmit && vite build`）：无 TS 错误，`✓ built`，退出码 0。

## 关注点 / 备注

- 收口过程中，并发 FE-ML-4 任务正在编辑其未提交文件
  （`frontend/src/components/chat/OfflineToolForm.vue`、新增
  `frontend/src/__tests__/ml.offline.form.test.ts`），一度因未使用符号触发
  `TS6133` 使全量 `vue-tsc` 短暂变红。按纪律**未触碰这些外来文件**，待该任务收敛后
  build 自行恢复全绿。
- 未做任何 `git reset/amend/rebase/stash/checkout/push`；提交仅包含本任务路径。

## 涉及文件

- `frontend/src/utils/format.ts`（单一事实源，未改）
- `frontend/src/components/results/ml/mlMetrics.ts`
- `frontend/src/components/results/ml/ClassificationCard.vue`
- `frontend/src/__tests__/ml.views.test.ts`
- `frontend/src/__tests__/ml.registry.test.ts`
- `frontend/src/__tests__/models.panel.test.ts`
- `docs/superpowers/plans/reports/fe-ml-5-report.md`

## 提交信息

`test(frontend): 迭代二收口（去重百分比助手 + 关键用例）`
