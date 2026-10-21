# FE-7 报告：下载继续走 Agent（修正编号语义）

- 计划：`docs/superpowers/plans/2026-10-18-frontend-contract-align.md` → Task FE-7
- 日期：2026-10-18
- 范围：仅前端（`frontend/`）

## 目标

下载仍指向 Agent（URL 不变：`/api/files/download?task_id=&path=`），但传给接口的 `task_id`
必须是 **Hadoop 任务编号**（Agent 侧再转发到远端服务）。同时修正 `ArtifactRef.task_id`
的类型/注释——它实际承载的是 Hadoop 任务编号，而非 Agent 任务号。

## 改动

### 1. `frontend/src/api/types.ts`

`ArtifactRef.task_id` 由 `AgentTaskId | null` 改为 `string | null`，并更新注释：

```ts
/** **Hadoop 任务编号**（不是 Agent 任务号）；下载 / 预览一律按此定位远端任务目录。 */
task_id: string | null
```

类型上 `AgentTaskId = string`，故为纯语义修正，不改变运行行为；仅纠正此前误导性的命名。

### 2. `frontend/src/views/tabs/ArtifactView.vue`

核验：下载地址与预览均使用 `artifact.task_id`（现明确为 Hadoop 任务编号），
地址来自 `resources.downloadUrl(a.task_id, a.path)` → `fileDownloadUrl` → `/api/files/download`。
无路径误传 Agent 任务号；仅补一行说明性注释，防止后续回归。

### 3. `frontend/src/components/resources/TaskFilesSection.vue`

核验（未改动）：列举 / 预览 / 下载一律走 `hadoopTaskId`（`selectedTask.hadoop_task_id`），
选中态才用 `agent_task_id`。此前任务已就位，本次无需改动。

### 4. 测试 `frontend/src/__tests__/resources.test.ts`

新增 FE.7 用例（真实 pinia + 真实 `@/api/resources`，不 mock 下载构造）：

```ts
const url = store.downloadUrl('H1', 'cleaned/x/ratings.dat')
expect(url).toContain('/api/files/download')
expect(url).toContain('task_id=H1')
expect(new URL(url).origin + new URL(url).pathname).toBe(`${API_BASE}/api/files/download`)
```

## 验证

- `npx vitest run src/__tests__/resources.test.ts` → 13 passed（含新增 FE.7 用例）。
- `npx vitest run`（全量）→ 30 files / 225 tests 全绿。
- `npm run build`（`vue-tsc --noEmit && vite build`）→ 类型检查通过、构建成功。

## 约束核对

- 用户可见文案未新增「冒烟 / 对账 / 口径」等词。
- 仅改动前端文件；下载 URL 仍指向 Agent，未直连远端。
- 未回退他人近期对 TaskFilesSection / ArtifactView / types 的改动（均为增量）。
- 本地单次提交，未推送。

## 结论

编号语义修正完成，行为无回归；下载继续经 Agent 中转，`task_id` 语义明确为 Hadoop 任务编号。
