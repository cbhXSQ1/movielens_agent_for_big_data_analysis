# FE-ML-1 报告 · 迭代二机器学习结果三视图（按 task_type 分发）

## 状态

已完成。`npm run test` 与 `npm run build` 均通过；单次本地提交，未推送。

## 范围

实施计划 Task **FE-ML-1**（结果三视图，按 `task_type` 分发）；字段口径以
`docs/hadoop/迭代二_机器学习_数据契约.md` §3.1–§3.5 为准。

## 交付物

### 新增（`frontend/src/components/results/ml/`）

- `ClassificationCard.vue`：分类结果卡（契约 §3.2）。F1 / AUC / PR-AUC / 精确率 /
  召回率 / 判定阈值 / 正类比例均按 **0–1 小数 ×100 保留两位**显示为百分比；
  混淆矩阵（tp/fp/tn/fn）；`feature_importance` 列表，存在 `feature_importance_note`
  时以说明文字呈现；`split_sizes`（train/valid/test）；`leakage` 防泄漏小字。
- `ClusteringPanel.vue`：聚类面板（契约 §3.3）。逐群表格展示
  `cluster_id` / `size` / `label` / `top_genres` / `behavior`（activity、avg_rating、
  rating_std、recency）；当 `k_selection` 存在时展示候选 `k` 与 `silhouette` 折线
  （纯 SVG polyline，无图表库）及逐点文本。
- `DimReductionPanel.vue`：降维面板（契约 §3.4）。`comparison`（before/after 的
  silhouette/davies_bouldin/k 与 agreement 的 ari/nmi）；`explained_variance_ratio`
  （NMF 为 `null` 时如实显示"不适用"）；`reconstruction_error`；`resource`
  （fit_seconds/mem_mb）；并说明坐标来自 `role=coords` 产物（本轮只下载）。
- `registry.ts`：`ML_VIEW_REGISTRY = { classification, clustering,
  dimensionality_reduction }` 与工厂 `mlViewFor(taskType)`（未知返回 `null`）。
  每个视图是独立、可插拔组件（多态），新增类型只需在注册表登记。
- `MlResultView.vue`：宿主。读取 `task_type`（顶层字段 → 任务视图字段 →
  `blocks.task_type`），取不到时按存在的板块键推断；经 `mlViewFor` 用
  `<component :is>` 动态渲染对应组件，未知类型给柔和兜底；无 ML 板块时整块不渲染。
  `result` 缺省回落 tasks store 的 `latestResult`。
- `mlMetrics.ts`：防御式规范化（unknown → 契约强类型）与展示格式化
  （`formatPercent` / `formatNumber` / `formatInt`）；缺失值不造数字。

### 修改

- `frontend/src/composables/useResultBlocks.ts`：新增并透出
  `classification` / `clustering` / `dimensionalityReduction`（取 `blocks` **原值**，
  不做规范化）。
- `frontend/src/api/types.ts`：新增契约 §3.2–§3.4 的指标类型
  （`MlClassificationMetrics` / `MlClusteringMetrics` / `MlDimReductionMetrics` 及
  其子结构、`MlTaskType`），`ResultBlocks` 增加 `task_type` 与三块键；字段保持
  unknown-tolerant（可选 + 索引签名）。
- `frontend/src/components/progress/TaskDetail.vue`：在结果区接入
  `<MlResultView :result="result">`，最小侵入；既有块标签、版本/时间边界渲染不变。

### 测试（`frontend/src/__tests__/`）

- `ml.views.test.ts`（15）：三份样例指标断言渲染字段与数值；分类百分比换算
  （0.7846→78.46%、0.5→50.00%、字符串数值）；缺失值返回空、不造数；NMF 解释方差
  比 `null` 显示"不适用"。
- `ml.registry.test.ts`（8）：注册表 `task_type → 组件` 映射与未知返回 `null`；
  宿主按 task_type 分发、无 task_type 时按板块推断、未知类型兜底、无 ML 板块不渲染。
- `ml.result.views.test.ts`（5）：`useResultBlocks` 原样透出三块（含缺席为 `null`）；
  TaskDetail 集成——含 ML 板块时渲染视图且保留块标签，无 ML 板块时不渲染。

合计新增 28 条用例。

## 设计要点

- **注册表/组件工厂**：三视图互不依赖，宿主只依赖 `registry`；`task_type` 是唯一
  分发键，符合多态扩展。
- **数据就绪**：组件接 `unknown` 原值，内部统一经 `mlMetrics.ts` 防御式规范化；
  契约以 0–1 小数给值，百分比换算集中在前端一处。
- **非侵入接线**：`MlResultView` 自身在无 ML 板块时输出空；TaskDetail 既有渲染路径
  完全保留。

## 验证

- `npm run test`：34 个测试文件、269 条用例全部通过。
- `npm run build`（`vue-tsc --noEmit && vite build`）：通过。
- 用户可见文案扫描：不含「冒烟/对账/口径」。

## 约束遵循

- 仅改本任务拥有的文件（ml 组件、`useResultBlocks.ts`、`types.ts`、宿主接线）；
  未触碰 `frontend/src/api/http.ts`。
- 单次本地提交，未推送。

## 备注 / 待联调

- Agent 侧 §3.1 的 `task_type` 落位（`result_payload` 顶层或任务视图）与 AG-ML-2
  同步；宿主对两处及 `blocks.task_type` 均已兼容，并可按板块键推断，故不受落位
  差异影响。
- `coords` 坐标散点本轮按契约"只下载"未内联绘制，仅给出说明；后续如提供预览/下载
  渲染，可在 `DimReductionPanel` 内扩展。
