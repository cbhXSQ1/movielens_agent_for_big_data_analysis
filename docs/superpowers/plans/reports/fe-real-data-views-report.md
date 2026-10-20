# 结果标签与右栏接真实数据、移除示例数据 报告

**范围：** 仅前端（`frontend/src`），无后端改动。
**状态：** 完成 —— `npm run test` 与 `npm run build` 均通过。

## 变更文件

修改：
- `frontend/src/views/tabs/ScoreView.vue`
- `frontend/src/views/tabs/ReportView.vue`
- `frontend/src/views/tabs/MetricsView.vue`
- `frontend/src/views/tabs/BasisView.vue`
- `frontend/src/views/tabs/MlView.vue`
- `frontend/src/views/tabs/KgView.vue`
- `frontend/src/components/RightRail.vue`
- `frontend/src/components/TopBar.vue`
- `frontend/src/styles/global.css`（沿用工作区已有的五级字号调整）

删除：
- `frontend/src/components/scoreData.ts`（示例五维评分 / 数据量 / 格式化函数，已无引用）

## 示例数据 → 真实数据

| 位置 | 原示例内容 | 现在 |
| --- | --- | --- |
| ScoreView | `scoreData.ts` 的五维分数、综合分、清洗前后行数 | `useResultBlocks` 的 `score_comparison` / `data_volume` / `versions` / `time_boundaries`；无结果显示空态，无评分板块显示“该结果没有五维评分板块” |
| ReportView | 内置示例报告全文 | `blocks.report` 的 Markdown；复制 + 按真实 `hadoop_task_id` 下载；无结果 / 无报告分别给出空态 |
| MetricsView | 内置示例 JSON | 最近一次结果 `blocks` 原文（只读、可复制）；无结果显示空态 |
| BasisView | 示例版本 / 时间边界 / 局限 | `blocks.versions`、`blocks.time_boundaries`、`blocks.limitations`；方案正文经中栏“方案内容”标签读取 |
| MlView | 示例模型编号与指标 | `models` store 的 `list_ml_models` 结果；无模型 / 加载中 / 错误分别显示，模型详情在中栏打开 |
| KgView | 示意图与示例实体 | 明确说明迭代三尚未实现，不展示示意数据 |
| RightRail | 示例任务、示例版本、假产物、`data-toast` 按钮 | 当前任务、结果中的版本 / 时间边界、`artifacts` store 的最近产物、真实结果标签入口；无数据统一“暂无” |
| TopBar | 顶层 `blocks.data_version`（字段不存在，长期显示 `—`） | 最近结果 `blocks.versions.data`；缺失显示 `—` |

`git grep scoreData -- frontend/src` 在删除前后均为空（无引用）。

## 验证命令与结果

```
cd frontend
npm run test
```

结果：

```
Test Files  43 passed (43)
Tests       327 passed (327)
Duration    8.13s
```

```
cd frontend
npm run build
```

结果：

```
vue-tsc --noEmit 通过
vite v6.4.4 building for production...
✓ 2826 modules transformed.
dist/index.html                 0.43 kB │ gzip:   0.32 kB
dist/assets/index-DypRGRya.css 70.77 kB │ gzip:  12.03 kB
dist/assets/index-CKSlApdh.js 664.60 kB │ gzip: 202.48 kB
✓ built in 4.86s
```

仅剩既有 `chunk > 500 kB` 警告，与本次改动无关。

补充检查：

```
git grep -n -E "data-toast|ml-[0-9]{8}|97\.71|99\.79|95\.07|99\.95|1150241|1000209|SCORE_DIMS|COMPOSITE_(BEFORE|AFTER)|DATA_ROWS" -- <6 个视图> RightRail.vue TopBar.vue
→ 无匹配

git grep -n -E "冒烟|对账|口径|请[^问]" -- <6 个视图> RightRail.vue TopBar.vue
→ 无匹配
```

## 顾虑 / 备注

- 六个视图与右栏的示例数据在此前工作区改动中已大部分替换，本次完成收尾：删除 `scoreData.ts`、修正 TopBar 版本取值路径，并复核无残留示例编号 / 分数 / `data-toast`。
- `TopBar` 使用 `tasks.currentResult`（右栏为 `result ?? latest`）；当前任务无结果时显示 `—`，不借用其他任务的数据版本。
- `styles/global.css` 的五级字号调整属于工作区既有改动，一并保留提交，未改其他样式。
- 未执行 `git reset/amend/rebase/reflog/stash/checkout/push`；提交仅包含本任务路径。

---

## 复核修复（2026-10-20）

**状态：** 完成 —— `npm run test` 与 `npm run build` 均通过。

### 逐项处理

1. **MlView 当前任务 ML 结果**：当前结果含 `classification` / `clustering` / `dimensionality_reduction` 板块时，在已登记模型列表上方渲染 `MlResultView`（传入 `tasks.currentResult`）；`/api/models` 列表仍作为浏览区保留。
2. **结果来源统一**：`ScoreView` / `ReportView` / `BasisView` 改用 `useResultBlocks(() => tasks.currentResult)`，与 Metrics、TopBar、RightRail 保持同一事实源。
3. **MetricsView 真实下载**：从 artifacts store 中查找 `task_id` 与当前任务一致、路径含 `metrics` 的产物，经 `fileDownloadUrl` 生成下载链接；无该产物时不显示下载按钮。`blocks` 为空对象时按空态处理。
4. **ReportView Markdown**：报告全文改用 `MarkdownView` 渲染；报告块只有引用（无全文）但存在 `report.md` 产物时，提供该产物的下载链接。
5. **BasisView 方案文件**：不再写死 `cleaning_rules.v1.json` / `scoring_scheme.v1.json`；从 artifacts store 过滤 `source === 'scheme'` 并按 `scheme_name` 打开 `schemeTab`；没有方案产物时显示“该结果未提供方案文件”。

### 同批小项

- `MlView` 指标摘要：`f1` / `auc` / `precision` / `recall` / `pr_auc` / `silhouette` 按 0–1 小数 ×100 保留两位；`k` / `n_components` 原样；嵌套值 JSON 化，不再出现 `[object Object]`。
- `ScoreView`：缺失 `dedupe` / `fix` 时显示 `—`，不再回落成 `0`。

### 新增测试

- `frontend/src/__tests__/result.views.realData.test.ts`
  - Score / Report / Basis 绑定 `tasks.currentResult`（当前任务与最后到达结果不同）。
  - MlView 在当前结果含 ML 板块时渲染 `MlResultView`，并保留模型列表。
  - Metrics 空态、真实下载链接、无对应产物时不显示下载。
  - Report 使用 Markdown 渲染，以及引用态产物下载。
  - Basis 从产物 store 派生方案文件，打开对应 `schemeTab`，无产物时提示。
  - MlView 摘要格式与 ScoreView `—` 兜底。

### 验证结果

```
cd frontend
npm run test
→ Test Files  44 passed (44)
→ Tests       340 passed (340)
```

```
cd frontend
npm run build
→ vue-tsc --noEmit 通过
→ vite v6.4.4 built in 3.96s
```

仅剩既有 `chunk > 500 kB` 警告，与本次改动无关。

