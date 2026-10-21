# FE-9 收口报告 —— 全量前端测试与构建

任务：`docs/superpowers/plans/2026-10-18-frontend-contract-align.md` → `### Task FE-9: 收口 —— 全量前端测试`
范围：仅前端（`frontend/`）。无网络依赖，前端用例本地执行。

## 1. 全量前端测试（收口前基线）

命令（在 `frontend/` 下）：

```
npm run test
```

对应脚本：`vitest run`（见 `frontend/package.json`）。

结果（基线，新增用例前）：

```
Test Files  30 passed (30)
     Tests  225 passed (225)
  Duration  ~7.0s
```

全绿，无失败、无跳过。

## 2. 构建 / 类型检查

命令（在 `frontend/` 下）：

```
npm run build
```

对应脚本：`vue-tsc --noEmit && vite build`。

结果：`vue-tsc` 无类型错误；`vite` 生产构建成功：

```
dist/index.html                 0.43 kB  │ gzip:   0.32 kB
dist/assets/index-BpladsxQ.css 36.65 kB  │ gzip:   6.13 kB
dist/assets/index-C6MagHyu.js  657.25 kB │ gzip: 197.86 kB
✓ built in ~4.7s
```

唯一提示是 Vite 的「chunk 大于 500 kB」体积告警（非错误，与本次改动无关）。

## 3. 新增用例（3–5 条，只补真实缺口）

新增文件：`frontend/src/__tests__/fe9.contract.test.ts`（共 **5** 条用例）。

新增前先核对既有覆盖，按「已覆盖则不重复」处理：

| 评审点 | 是否已覆盖 | 处理 |
| --- | --- | --- |
| 非终态「仍在运行」提示对 `succeeded`/`failed` 缺席 | **否**（既有仅验 `aborted` 无进行中提示，未验 `succeeded`/`failed`） | 补 |
| `aborted` 进度停在停止值（非 100%） | 组件级已覆盖（`progress.panel.test.ts` FH.5）；**事件/store 级未覆盖** | 在 store 事件流层补 |
| `RESULT_BLOCK_META` 6 项含 `task_status` 且无旧 tab 键 | 已覆盖（`results.test.ts` FE-3：`toHaveLength(6)`、含 `task_status`、`'tab' in meta === false`） | 不重复 |
| 日志类产物默认折叠 | 组件级已覆盖（`artifact.view.test.ts` FE.4：`artifact-log-collapsed` 为闭合 `<details>`）；**纯函数辅助未单测** | 在纯函数层补 |
| `downloadUrl` 指向 Agent `/api/files/download` | 已覆盖（`resources.test.ts` FE.7 + `fileDownloadUrl` 用例） | 不重复 |

补的 5 条用例：

1. `activeHint` 仅对 `queued`/`running` 给出「仍在运行」，`succeeded`/`failed`/`aborted` 一律 `null`；`abortedHint` 仅对 `aborted` 给出「已中止 / 停在中断处」，其余为 `null`。
2. 组件级：`succeeded`/`failed` 任务的 `TaskListItem` 不渲染任何进行中 / 中止提示（`task-running-hint-*`、`task-aborted-hint-*` 均不存在）。
3. store 事件流：任务运行到 40% 后，收到**省略 `progress_percent` 的中止事件**时，进度保持 40%（既不重置也不补到 100%），状态变为 `aborted`。
4. 纯函数：`categoryTagType` 各类别徽标配色固定（数据=info、隔离=error、报告=success、指标/元数据=warning、日志/其它=default），未知 / 缺失类别回落 `default`。
5. 纯函数：`isLogArtifact`（类别为日志 / 路径含 `logs/` / `.log` 文件名）与 `deriveCategoryFromPath` 的路径归类（日志 / 隔离 / 报告 / 指标 / 元数据 / 其它）。

未削弱、未删除任何既有用例。

## 4. 收口后结果

```
Test Files  31 passed (31)
     Tests  230 passed (230)
  Duration  ~7.2s
```

- 新增 1 个测试文件、5 条用例：`30 → 31` 文件、`225 → 230` 用例，全部通过。
- `npm run build`（含 `vue-tsc`）在此之后再次执行，仍无类型错误、构建成功。
- 新增文案已检查，不含「冒烟」「对账」「口径」等词；仅改动前端文件。

## 5. 结论与关注点

- 状态：全量前端用例绿（230/230），构建与类型检查干净。
- 关注点：
  - 「日志折叠」「结果六块」「下载地址」三项在既有 FE-3/FE-4/FE-7 用例里已覆盖，本次按要求**不再重复**，仅在纯函数层补了产物类别辅助（徽标配色 / 日志识别 / 路径归类），因该文件此前无直接单测。
  - `aborted` 进度保持依赖 store `upsert` 的浅合并（事件缺键则保留旧值）。若后续远端改为在中止事件里显式下发 `progress_percent: 0` 或 `100`，此行为会随之改变——需以远端契约为准。
  - 构建仍有 Vite 的 chunk 体积告警（657 kB > 500 kB），属既有状态，非本次引入。
