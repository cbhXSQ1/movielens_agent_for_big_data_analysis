# FE-ML-3 报告 · 单位与错误码

## 状态

已完成。`npm run test` 与 `npm run build` 均通过；本地提交，未推送。

## 范围

实施计划 Task **FE-ML-3**（`frontend/src/api/http.ts`、相关组件 + 共享 util）；
错误码与单位约定以 `docs/hadoop/迭代二_机器学习_数据契约.md` §0（比率类 0–1 小数、四位）
与 §5（错误码）为准。

## 交付物

### 新增

- `frontend/src/utils/format.ts`：展示层共享的百分比格式化。`formatPercent(value, digits = 2)`
  把 **0–1 小数 ×100**、默认保留两位（`0.7846` → `"78.46%"`）；兼容数字字符串；
  缺失 / 空串 / 非数 / `NaN` / `Infinity` → 返回空字符串，**不造数字**。
- `frontend/src/__tests__/ml.format.test.ts`（5 条）：`errorCodeText('MODEL_NOT_FOUND')` 的中文与
  「模型库」指引；其它 ML 相关码可读性；百分比换算（含自定义小数位）；缺失/非数兜底。

### 修改

- `frontend/src/api/http.ts`：`ERROR_CODE_TEXT` 新增 `MODEL_NOT_FOUND`：
  `'模型编号不存在：请用「模型库」确认编号'`。其余 ML 相关码
  （`DEPENDENCY_MISSING` / `TASK_ALREADY_RUNNING` / `USAGE` / `TASK_FAILED` /
  `WAIT_LIMIT_REACHED` / `ABORTED` / `UNAUTHORIZED` / `FORBIDDEN_HOST`）此前已在表中，本次以注释
  指明其归属，未改文案。

## 设计要点

- **契约对齐**：§0 比率类指标由前端自行 ×100；helper 只做「×100 + 定点」，不做四舍五入策略之外的处理。
- **不造数字**：缺失值不由 helper 编造 `0.00%`；返回空串让调用方决定占位符。
- **避免跨任务改写**：FE-ML-1 已在 `components/results/ml/mlMetrics.ts` 内有同名
  `formatPercent`，并被分类卡引用。为避免与并发任务同时改同一文件，本任务**不**去重写
  `mlMetrics.ts` / 三个 ML 组件，只新增共享 util 并单测；后续收口（FE-ML-5）若需统一，
  可将 `mlMetrics.formatPercent` 改为从本 util 再导出。

## 验证

- `npm run test`：38 个测试文件、290 条用例全部通过（基线 285，+5）。
- `npm run build`（`vue-tsc --noEmit && vite build`）：通过。
- 新增文案不含契约禁用措辞（冒烟 / 对账 / 口径）。

## 约束遵循

- 仅改本任务路径：`frontend/src/api/http.ts`、新增 `frontend/src/utils/format.ts` 与
  `frontend/src/__tests__/ml.format.test.ts`。
- 未触碰并发的 `agent/runtime/long_task.py`、`agent/tests/test_ml_long_task.py`、
  `agent/tests/test_models_api.py`，未暂存 `frontend/.vite/` 等构建缓存。
- 本地提交，未推送。

## 备注 / 待联调

- `MODEL_NOT_FOUND` 由 Agent 侧（AG-ML-6）在预测 / 取模型时返回；前端已可直接展示中文。
- 若后续 `mlMetrics.formatPercent` 与 `utils/format.ts` 统一，注意两者对缺失值的兜底不同
  （前者空串、后者空串；本次已对齐为空串）。
