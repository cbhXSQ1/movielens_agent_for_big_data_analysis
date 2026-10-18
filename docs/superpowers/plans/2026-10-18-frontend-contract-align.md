# 前端与远端能力服务对齐 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让前端与「Agent 已接上远端 Hadoop 能力服务」后的语义一致：去掉原始数据上传、正确显示中止状态、按类别展示产物并折叠日志、直接展示版本与时间边界、长任务进度提示，以及下载走 Agent 中转（前端 URL 不变）。

**Architecture:** 前端保持现有 Vue3 + Pinia 结构；REST/SSE 客户端不变。改动集中在：资源面板（移除上传、原始数据改只读）、进度与结果呈现（状态与六块结果）、产物展示（类别徽标 + 日志折叠 + 版本/时间边界）、错误码中文表、以及测试补强。

**Tech Stack:** Vue 3、Vite、TypeScript、Naive UI、Pinia、Vitest、Vue Test Utils。

## Global Constraints

- **用户可见文案**用自然语言描述场景，**不得**出现「冒烟」「对账」「口径」等词。
- 任务状态取值与远端一致：`queued / running / succeeded / failed / aborted`；界面优先显示后端给的 `status_cn`。
- 文件类接口的 `task_id` 一律用 **`hadoop_task_id`**（界面展示用 `agent_task_id`）。
- 产物类别沿用远端 `artifacts[].category`：数据 / 隔离 / 报告 / 指标 / 元数据 / 日志；**日志类默认折叠**。
- 下载地址仍指向 Agent（`/api/files/download`），由 Agent 带口令取回；**前端不直连远端**。

---

## 文件结构（改动集中在现有文件）

**修改**
- `frontend/src/components/resources/RawDataSection.vue` — 移除上传，改只读 + 提示。
- `frontend/src/api/resources.ts`、`frontend/src/stores/resources.ts` — 移除 `uploadRawData`/`uploadRaw`。
- `frontend/src/api/types.ts` — 移除 Raw 上传类型；修正 `ArtifactRef.task_id` 语义注释；补产物类别联合类型。
- `frontend/src/components/progress/taskDisplay.ts` — 状态映射（已有中止，补测试与统一入口）。
- `frontend/src/components/chat/resultBlocks.ts` — 六块元数据（补「任务状态」），tab 目标改为对应产物。
- `frontend/src/views/tabs/ArtifactView.vue` — 类别徽标、日志折叠、版本/时间边界小字。
- `frontend/src/api/http.ts` — 错误码中文表扩充。
- `frontend/src/components/resources/TaskFilesSection.vue`、`frontend/src/components/progress/TaskDetail.vue` — 进度提示与文件区。

**测试**
- `frontend/src/__tests__/*`（既有）+ 新增针对中止、类别、结果块、错误码的用例。

---

### Task FE-1: 移除原始数据上传，改为只读 + 提示

**Files:**
- Modify: `frontend/src/components/resources/RawDataSection.vue`、`frontend/src/stores/resources.ts`、`frontend/src/api/resources.ts`、`frontend/src/api/types.ts`
- Test: `frontend/src/__tests__/stores.resources.test.ts`、`frontend/src/__tests__/resources.test.ts`

**Interfaces:**
- 移除：`uploadRawData(files, name?)`（api）、`uploadRaw(files, name?)`（store）、`RawUploadResponse`/`RawUploadedFile`（types）。
- 保留：`listRawData()` 与 `loadRaw()`；原始数据面板顶部加一行说明「原始数据在运行数据的机器上维护，请在那里上传」。

- [ ] **Step 1: 写失败测试**（store 不再有 `uploadRaw`；组件不再渲染上传按钮）

```ts
// stores.resources.test.ts 追加
import { useResourcesStore } from '@/stores/resources'
it('没有原始数据上传动作', () => {
  const store = useResourcesStore()
  expect('uploadRaw' in store).toBe(false)
})
```

```ts
// resources.test.ts 追加
it('原始数据区不再出现上传按钮', async () => {
  const wrapper = mount(RawDataSection)
  expect(wrapper.find('[data-testid="raw-upload-btn"]').exists()).toBe(false)
  expect(wrapper.text()).toContain('在运行数据的机器上')
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run test -- RawDataSection`（在 `frontend/` 下）
Expected: FAIL

- [ ] **Step 3: 实现**
  - 删 `RawDataSection.vue:17-19`（上传状态）、`:24-51`（`pick`/`onPick`）、`:102-136`（输入框与按钮、结果提示）；模板顶部加说明行。
  - 删 `stores/resources.ts:90-97`（`uploadRaw`）及其导出；删 `api/resources.ts:34-46`（`uploadRawData`）及 `requestForm` 导入。
  - 删 `api/types.ts:484-492`（`RawUploadResponse`/`RawUploadedFile`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `npm run test -- RawDataSection stores.resources`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/resources/RawDataSection.vue frontend/src/stores/resources.ts frontend/src/api/resources.ts frontend/src/api/types.ts frontend/src/__tests__/
git commit -m "feat(frontend): 原始数据改只读（远端不提供上传）"
```

---

### Task FE-2: 任务状态映射收口与中止用例

**Files:**
- Modify: `frontend/src/components/progress/taskDisplay.ts`（仅补强，不新增状态）
- Test: `frontend/src/__tests__/progress.panel.test.ts`、`frontend/src/__tests__/stores.tasks.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { statusLabel, statusTagType, isTerminal } from '@/components/progress/taskDisplay'
it('中止状态显示为已中止且为终态', () => {
  expect(statusLabel({ status: 'aborted', status_cn: '已中止' } as any)).toBe('已中止')
  expect(statusTagType('aborted')).toBe('warning')
  expect(isTerminal('aborted')).toBe(true)
})
```

并在 `progress.panel.test.ts` 增加：一个 `aborted` 任务在列表里显示「已中止」、在详情里出现「取结果」按钮。

- [ ] **Step 2-4:** 跑失败 → 若已有映射则仅确认行为；把 `TaskDetail.vue`/`ResultsPanel.vue` 中直接读 `status_cn` 的地方统一改用 `statusLabel(task)` → 跑通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "test(frontend): 中止状态显示与终态行为用例；状态标签统一入口"
```

---

### Task FE-3: 结果六块元数据与导航修正

**Files:**
- Modify: `frontend/src/components/chat/resultBlocks.ts`、`frontend/src/components/progress/TaskDetail.vue`
- Test: `frontend/src/__tests__/results.test.ts`

**Interfaces:**
- `RESULT_BLOCK_META` 补第 6 项：`{ key: 'task_status', label: '任务状态', tab: null }`（任务状态直接由进度页展示，无独立产物）。
- 其余五块（五维评分对比/数据量变化/隔离样例/评估报告/依据与局限）的 `tab` 由失效的 `score/evidence/report` 改为「打开对应产物标签」的动作，而非旧 tab 键。

- [ ] **Step 1: 写失败测试**

```ts
import { RESULT_BLOCK_META } from '@/components/chat/resultBlocks'
it('结果块含六项且含任务状态', () => {
  expect(RESULT_BLOCK_META).toHaveLength(6)
  expect(RESULT_BLOCK_META.map(b => b.key)).toContain('task_status')
})
```

- [ ] **Step 2-4:** 跑失败 → 改 `resultBlocks.ts`（补第六项；导航改为发出「打开产物」而非旧 tab）。`TaskDetail.vue:115-139` 的按钮改为调用注入的 `openArtifact`（无产物可打开的块只展示不导航）→ 跑通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(frontend): 结果六块（含任务状态），结果按钮改为打开对应产物"
```

---

### Task FE-4: 产物类别徽标与日志折叠

**Files:**
- Modify: `frontend/src/views/tabs/ArtifactView.vue`、`frontend/src/components/resources/TaskFilesSection.vue`、`frontend/src/api/types.ts`
- Test: `frontend/src/__tests__/artifact.view.test.ts`（新增或并入既有）

**Interfaces:**
- 新增类别联合：`type ArtifactCategory = '数据' | '隔离' | '报告' | '指标' | '元数据' | '日志' | '其它'`（`category` 仍允许字符串）。
- 徽标颜色映射：数据=`info`、报告=`success`、日志=`default`、指标/元数据=`warning`、隔离=`error`、其它=`default`。

- [ ] **Step 1: 写失败测试**

```ts
it('日志类产物默认折叠且显示类别徽标', async () => {
  const wrapper = mount(ArtifactView, { props: { artifact: { id: 'task:H1:logs/driver.log', title: 'driver.log', category: '日志', kind: 'text', source: 'task_file', task_id: 'H1', path: 'logs/driver.log', scheme_name: null } } })
  expect(wrapper.text()).toContain('日志')
  expect(wrapper.find('[data-testid="artifact-log-collapsed"]').exists()).toBe(true)
})
```

- [ ] **Step 2-4:** 跑失败 → `ArtifactView.vue` 加类别徽标配色（`:129-131`），日志类（`category==='日志'` 或路径在 `logs/`）默认折叠（用 `<details>` 或 Naive UI 折叠面板，`data-testid="artifact-log-collapsed"`）；`TaskFilesSection.vue` 文件行加类别/种类徽标 → 跑通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(frontend): 产物类别徽标 + 日志默认折叠"
```

---

### Task FE-5: 版本与时间边界展示

**Files:**
- Modify: `frontend/src/views/tabs/ArtifactView.vue`、`frontend/src/components/progress/TaskDetail.vue`、`frontend/src/composables/useResultBlocks.ts`
- Test: `frontend/src/__tests__/composables.test.ts`

**Interfaces:**
- `useResultBlocks` 暴露 `versions`（四类：data/rule/scoring/policy）与 `timeBoundaries`（T1/T2）；直接取自 `get_task_result` 返回，不做推断。

- [ ] **Step 1: 写失败测试**

```ts
it('结果暴露版本与时间边界', () => {
  // 给 tasks.latestResult 塞入 versions/time_boundaries，断言 computed 透传
})
```

- [ ] **Step 2-4:** 跑失败 → 在 `useResultBlocks.ts` 增加 `versions`/`timeBoundaries` computed；在结果相关视图（产物页或详情页）以小字展示 → 跑通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(frontend): 展示四类版本与两个时间边界（不推断）"
```

---

### Task FE-6: 长任务进度提示（轮询不变）

**Files:**
- Modify: `frontend/src/components/progress/TaskListItem.vue`、`TaskDetail.vue`、`frontend/src/views/panels/TasksPanel.vue`
- Test: `frontend/src/__tests__/progress.panel.test.ts`

**要点：** 长任务期间展示阶段名与百分比（已有）；状态为 `queued/running` 时提示「仍在运行，可继续等待」；`aborted` 时进度停在停下的那一步（不显示 100%），提示「任务已中止」。

- [ ] **Step 1: 写失败测试**

```ts
it('运行中显示可继续等待提示', () => {
  const wrapper = mount(TaskListItem, { props: { task: { agent_task_id: 'LT-1', status: 'running', status_cn: '运行', progress_percent: 40, stage: '清洗' } as any } })
  expect(wrapper.text()).toContain('仍在运行')
})
```

- [ ] **Step 2-4:** 跑失败 → 列表项与详情在非终态显示提示文案 → 跑通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(frontend): 长任务进行中提示可继续等待；中止停在当前进度"
```

---

### Task FE-7: 下载继续走 Agent（修正编号语义）

**Files:**
- Modify: `frontend/src/api/types.ts`（`ArtifactRef.task_id` 注释改为「Hadoop 任务编号」）、`frontend/src/views/tabs/ArtifactView.vue`、`frontend/src/components/resources/TaskFilesSection.vue`
- Test: `frontend/src/__tests__/resources.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
it('下载地址用 hadoop 任务编号且指向 Agent', () => {
  const store = useResourcesStore()
  const url = store.downloadUrl('H1', 'cleaned/x/ratings.dat')
  expect(url).toContain('/api/files/download')
  expect(url).toContain('task_id=H1')
})
```

- [ ] **Step 2-4:** 跑失败 → 确认 `resources.downloadUrl`/`ArtifactView` 传入的是 `hadoop_task_id`（`ArtifactRef.task_id` 已是远端编号，仅修正类型注释）；无需改 URL → 跑通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "fix(frontend): 下载统一按 Hadoop 任务编号，经 Agent 中转"
```

---

### Task FE-8: 错误码中文表扩充

**Files:**
- Modify: `frontend/src/api/http.ts`
- Test: `frontend/src/__tests__/http.test.ts`（新增或并入既有）

**Interfaces：** 在 `ERROR_CODE_TEXT` 增补远端新码的中文说明（照文档 §5 建议话术）：`ABORTED`、`FILE_NOT_FOUND`、`FORBIDDEN_PATH`、`CONTENT_TOO_LARGE`、`NOT_PREVIEWABLE`、`PRODUCT_INVALID`、`CANCEL_FAILED`、`UNAUTHORIZED`、`FORBIDDEN_HOST`、`WAIT_LIMIT_REACHED`、`INTERNAL_ERROR`、`DRIVER_UNREACHABLE`、`DRIVER_TIMEOUT`、`TASK_NOT_FINISHED`。

- [ ] **Step 1: 写失败测试**

```ts
import { errorCodeText } from '@/api/http'
it('新错误码有中文说明', () => {
  expect(errorCodeText('ABORTED')).toContain('已中止')
  expect(errorCodeText('WAIT_LIMIT_REACHED')).toContain('等待')
})
```

- [ ] **Step 2-4:** 跑失败 → 补表 → 跑通过（文案不得含「冒烟/对账/口径」）。

- [ ] **Step 5: 提交**

```bash
git commit -m "feat(frontend): 扩充远端错误码中文说明"
```

---

### Task FE-9: 收口 —— 全量前端测试

- [ ] **Step 1:** `npm run test`（`frontend/`）全绿；对新改组件补 3~5 条关键用例。
- [ ] **Step 2:** `npm run build` 通过（类型检查无错）。
- [ ] **Step 3: 提交**

```bash
git commit -m "test(frontend): 收口 —— 全量用例与构建通过"
```

---

## Self-Review

- **规格覆盖**：文档 §6.1（上传移除、状态映射、完成判定）→ FE-1/FE-2/FE-6；§6.2（资源四段来源）→ 前端随 Agent 接口不变，已在 Agent 计划处理；§6.3（结果六块）→ FE-3/FE-5；§7（五态）→ FE-2；§9（类别徽标/版本边界/进度提示）→ FE-4/FE-5/FE-6；§8（下载经 Agent）→ FE-7；§5（错误码）→ FE-8。
- **占位符扫描**：FE-4 的类别颜色映射、FE-5 的展示位置需执行者按现有组件结构落地，不得留空。
- **类型一致**：`ArtifactCategory` 联合与 `ERROR_CODE_TEXT` 键在各任务中一致；`hadoop_task_id` 贯穿 FE-7。
