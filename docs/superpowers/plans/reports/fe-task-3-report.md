# FE-3 结果六块元数据与导航修正 — 报告

## 状态

- 任务：`2026-10-18-frontend-contract-align.md` → `### Task FE-3: 结果六块元数据与导航修正`
- 结果：完成，前端测试全绿、类型检查与构建通过。
- 范围：仅改前端；未触碰后端/远端。

## 需求回顾

远端 `get_task_result` 的 `blocks` 有六块，而前端 `RESULT_BLOCK_META` 只有五块，
且其 `tab` 指向已失效的固定键（`score`/`evidence`/`report`）。要求：

1. 补第六块「任务状态」（无独立产物，展示用）。
2. 结果块按钮改为「打开对应产物的中栏标签」，而非落到失效的旧 tab 键。
3. 无对应产物的块只展示、不导航（不留死链）。
4. 不新造固定结果标签；保留动态产物标签模型。

## 改动清单

### `frontend/src/components/chat/resultBlocks.ts`

- `ResultBlockMeta`：删除失效的 `tab: ResultTabKey`，改为
  `artifactCategory: string | null`（该块对应产物的类别；`null` 表示无独立产物）。
- `RESULT_BLOCK_META` 由 5 项扩为 6 项，新增
  `{ key: 'task_status', label: '任务状态', artifactCategory: null }`；
  其余五块挂上远端产物类别：
  - 五维评分对比 → `评分`
  - 数据量变化 → `证据`
  - 隔离样例 → `证据`
  - 评估报告 → `报告`
  - 依据与局限 → `依据`
  （类别取值与 Agent 端 `agent/runtime/artifacts_ref.py::category_of` 一致。）
- 新增 `artifactForBlock(meta, artifacts)`：在产物列表里按 `artifactCategory`
  取第一个匹配项；无类别或无同类产物时返回 `null`。
- `presentResultBlocks` 语义不变（仍按 `blocks[key]` 有值过滤）。

### `frontend/src/components/progress/TaskDetail.vue`

- 注入 `useArtifactsStore`，新增 `blockArtifacts` 计算：为每个有值的结果块解析
  当前对话产物里可打开的产物。
- 新增 `openArtifact(block)`：`artifacts.openTab(conversationId, artifact.id)` +
  `nav(artifact.id)`，复用既有的「打开产物标签」机制（与 `ChatView.onOpenArtifact`
  同一路径），不报文到旧 tab 键。
- 模板：块按钮的显示条件由 `v-if="nav"` 改为 `v-if="blockArtifacts[block.key] && nav"`，
  点击改为 `openArtifact(block)`；无可打开产物的块回落为只读 `n-tag`（不导航）。

## 测试

- `frontend/src/__tests__/results.test.ts`（新增 `FE-3` 用例）：
  - `RESULT_BLOCK_META` 恰有 6 项且含 `task_status`。
  - `task_status` 的 `artifactCategory` 为 `null`（只展示）。
  - 六块均不再保留失效的 `tab` 键。
  - `artifactForBlock`：有同类产物则解析出该产物；无同类产物 / 无类别则为 `null`。
- `frontend/src/__tests__/progress.panel.test.ts`（更新既有用例）：
  - 终态取结果后，点击「评估报告」块会打开对应产物标签（`isOpen` 为真）并经
    `nav` 激活该产物 id（不再断言旧键 `report`）。
  - 产物列表里无对应产物时，结果块只作展示，点击不触发 `nav`（无死链）。

## 验证

- `npm run test`（`frontend/`）：29 个测试文件、208 条用例全部通过。
- `npm run build`（`vue-tsc --noEmit && vite build`）：通过（仅既有的 chunk 体积提示，非错误）。

## 设计取舍与关切

- 「数据量变化」与「隔离样例」在 Agent 端同属类别 `证据`（`counts.json` 与
  `quarantine/*` 均归此类）。当前按类别取首个匹配产物，故二者可能都指向同一
  条证据产物。若后续要精确区分，可在元数据上再加路径/标题限定；本轮从简。
- 用户可见文案未出现「冒烟 / 对账 / 口径」等词。
- 未新增固定结果标签，动态产物标签模型保持不变。
