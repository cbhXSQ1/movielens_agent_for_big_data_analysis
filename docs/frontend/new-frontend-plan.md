# MovieLens Agent 新前端 实施计划

> 版本：v1.0  
> 日期：2026-10-19  
> 目标工程：`ml-reconstruction/frontend`（Vue 3 + Vite + TS + Pinia + Naive UI）  
> 上游 Spec：`C:\Users\lenovo\Desktop\new-frontend-spec.md`（v1.3）  
> 视觉/交互基线：`C:\Users\lenovo\Desktop\new-frontend-demo.html`  
> 视觉参考：`C:\Users\lenovo\Desktop\前端demo3.html`  
> 交付方式：**一次性完成当前原型的全部页面与交互**，直接替换 `AppShell.vue`，不做新旧壳并存。

---

## 0. 已锁定前提

| 项 | 结论 |
|---|---|
| 目标工程 | `ml-reconstruction/frontend` |
| 壳替换 | **直接替换 `AppShell.vue`**；旧壳删除；回滚靠 git 分支 |
| 交付范围 | 当前 HTML 原型里的全部页面与交互，一次做完 |
| 数据 | 全部接真实 store/API/SSE；生产环境移除「演示状态」 |
| 结果标签 | 固定 7 个：对话、五维评分、评估报告.md、metrics.json、方案依据、迭代二模型、知识图谱 |
| 对话树 | 必须可分支，走现有后端 `branchFrom` |
| 状态标签 | 运行中、成功、失败都显示；无任务时隐藏 |
| 右栏最近产物 | 全局最近产物，不限制当前任务 |
| 暗色 | 下一版再做 |
| 移动端 | 760px 可用降级 |
| 视觉精度 | 骨架/间距/颜色以原型为准；真实数据下允许空态、长文本、更多标签的自适应 |
| 测试 | 保留现有 45 个测试文件与 SSE 契约，按新结构更新/补充 |

### 分支与回滚

- 从当前 `main` 拉 `feat/new-shell`。
- 按阶段提交，最终合并前不保留旧壳。
- 回滚策略：revert 分支或回到 `main`，不使用 feature flag。
- 原型 HTML 放入 `ml-reconstruction/docs/prototypes/new-frontend-demo.html`（或团队约定路径），只作为视觉基准，不进入构建产物。

---

## 1. 现状盘点

### 1.1 当前壳

`src/components/AppShell.vue` 目前是：

```text
AppShell
├─ TopBar（全宽，品牌 + 连接状态 + 设置）
└─ app-shell__body
   ├─ LeftRail（图标条 + 4 面板）
   └─ CenterTabs（对话 + 动态产物标签）
```

问题：

- 没有右栏；
- 品牌在 TopBar，不在左栏；
- TopBar 全宽，不是 demo3 的「中央顶栏」；
- 左栏没有品牌头与「点 Logo 收起」；
- 结果入口仍以标签/视图为主，没有demo3式的三栏摘要；
- 对话框/按钮/彩条仍是 reconstruction 视觉，不是 demo3 皮肤。

### 1.2 可复用资产

| 资产 | 复用方式 |
|---|---|
| `LeftRail.vue` | 保留图标条/拖宽/抽屉逻辑，新增品牌头、换皮肤 |
| `CenterTabs.vue` | 保留标签机制，固定 7 个标签，换皮肤 |
| `ConversationListPanel.vue` | 保留数据逻辑，换卡片/按钮样式 |
| `ConversationTreePanel.vue` | 保留入口，换缩略图/按钮 |
| `ResourcesPanel.vue` | 保留五分段/上传/下载逻辑，换样式 |
| `TasksPanel.vue` + `TaskDetail.vue` | 保留任务数据，改任务卡等宽、结果按钮网格 |
| `ConversationTreeWindow.vue` / `treeLayout.ts` | 保留树画布/分支逻辑，换皮肤 |
| `ChatView.vue` | 保留 SSE/工具调用/消息逻辑，重构输入区与结果摘要卡 |
| `ArtifactView.vue` | 保留 md/json/text 渲染，换容器样式 |
| stores：conversations / chat / tree / tasks / resources / models / centerTabs / artifacts / settings / connection | 直接作为真实数据源 |
| `SettingsModal.vue` | 保留，换按钮层级 |
| `theme/index.ts` / `styles/global.css` | 增加 demo3 token 与全局覆盖 |

### 1.3 需要新增

- `RightRail.vue`
- `BrandHeader.vue`（或并入 LeftRail）
- `StatusChip.vue`
- `ScoreBars.vue`（五维分段彩条，供摘要卡和五维视图共用）
- `styles/shell-v2.css`（或等价全局样式）
- 对应测试文件

---

## 2. 目标结构

```text
AppShell
├─ LeftRail
│  ├─ BrandHeader（ML + MovieLens Agent + 副标题；点 Logo 收起）
│  ├─ icon rail（4 图标 + 底部收起箭头）
│  └─ panel（对话列表 / 对话树 / 资源 / 任务进度）
├─ CenterColumn
│  ├─ TopBar（标题 + 数据版本 + 状态标签 + 连接 + 右栏开关 + 设置）
│  ├─ CenterTabs（固定 7 标签）
│  └─ ChatView / ArtifactView
├─ RightRail（当前任务 / 版本与 T1T2 / 全局最近产物 / 结果入口）
└─ SettingsModal
```

响应式：

- `>1360px`：三栏常驻，右栏可收起。
- `1100–1360px`：右栏浮层，右缘 24px 竖条打开。
- `≤1100px`：左栏抽屉，品牌只留 Logo。
- `≤760px`：版本/连接隐藏，状态标签保留，右栏浮层 ≤88vw。

---

## 3. 工作分解

> 阶段是实施顺序，不是分阶段交付；所有阶段完成后一次性评审。

### 阶段 0：基线

- [ ] 拉 `feat/new-shell` 分支。
- [ ] 跑一遍 `npm run test`，记录当前 45 个测试基线。
- [ ] 跑 `npm run build`，确认当前 typecheck/build 基线。
- [ ] 把 `new-frontend-demo.html` 放入 `docs/prototypes/`，记录路径。
- [ ] 截图当前 reconstruction 页面（1440/1200/900），作为改造前对照。
- [ ] 盘点 `AppShell.vue` 的 props/emits、`LEFT_PANELS`、`ResultTabKey`、`SHELL_STORAGE_KEYS`。

**产出**：基线记录、原型路径、分支。

---

### 阶段 1：设计 token 与三栏壳

#### 1.1 设计 token

- [ ] 在 `src/theme/index.ts` 或 `src/styles/global.css` 增加 demo3 token：
  - 背景、surface、文字四级、边框；
  - 蓝/紫/粉/绿/黄/红；
  - `--grad`、`--grad-soft`；
  - 圆角 18/13/9；
  - 卡片/浮层阴影；
  - 14.5px 正文阶梯。
- [ ] 新增 `src/styles/shell-v2.css`，只放全局壳层/通用组件样式，避免把所有样式塞进 scoped 组件。
- [ ] 处理 Naive UI 变量映射：主色、圆角、边框、字体；不要换组件库。

#### 1.2 直接替换 AppShell

- [ ] 重写 `src/components/AppShell.vue` 模板：

```vue
<div class="app-shell">
  <LeftRail ...>
    <template #brand> <BrandHeader ... /> </template>
  </LeftRail>
  <main class="app-shell__center">
    <TopBar ... />
    <CenterTabs ... />
  </main>
  <RightRail ... />
  <SettingsModal ... />
</div>
```

- [ ] 移除「TopBar 在全宽顶部」的旧结构。
- [ ] 新增右栏状态：
  - `rightCollapsed`（宽屏）；
  - `rightOpen`（窄屏浮层）；
  - `localStorage` key：`mlgov.rightCollapsed`。
- [ ] 新增 `RightRail` 组件挂载。
- [ ] 保持 `activePanel` / `activeTab` / `leftWidth` / `collapsed` 的持久化逻辑。
- [ ] 保留 `provideResultTabNav`，确保对话内结果卡仍能切标签。

**验收**：1440px 下品牌、中央顶栏、右栏头部在 60px 线上；旧 TopBar 全宽布局消失。

---

### 阶段 2：左栏品牌与四面板

#### 2.1 品牌头

- [ ] 新增 `BrandHeader.vue`（或 `LeftRail` 内部插槽）：
  - 34px ML 渐变块；
  - `MovieLens Agent`；
  - `迭代二 · 数据治理与分析`；
  - 高度 60px，底部边框。
- [ ] 点 ML Logo 触发 `toggleCollapsed`。
- [ ] 收起态：文字隐藏，只留 Logo；宽度 64px。
- [ ] 底部箭头在收起态旋转 180°，与展开态对称。
- [ ] `title` / `aria-label` 同步「收起左栏 / 展开左栏」。
- [ ] 面板窄屏抽屉仍从品牌下方展开，不盖品牌。

#### 2.2 对话列表

- [ ] 搜索、新建、切换、删除保留。
- [ ] 新建改为虚线按钮。
- [ ] 当前项浅紫底 + 渐变左竖条。
- [ ] 点击后中央顶栏标题同步。

#### 2.3 对话树面板

- [ ] 保留缩略图 + 当前分支 + 旧支数量。
- [ ] 「打开大图」改 ghost 按钮。
- [ ] 点击缩略图打开 `ConversationTreeWindow`。

#### 2.4 资源

- [ ] 保留五分段与真实上传/下载。
- [ ] 卡片换 13px 圆角、浅边框、hover。
- [ ] 上传区虚线样式。
- [ ] 空态/加载/错误态真实展示。

#### 2.5 任务

- [ ] 任务卡：`display:block; width:100%; text-align:left;`，所有卡等宽。
- [ ] 任务详情：
  - 双编号、状态、阶段、用时、数据版本；
  - 结果按钮 2 列网格；
  - 第 5 个「模型结果」跨两列；
  - 点击结果按钮切中栏标签。
- [ ] 顶部「新任务 / 刷新」改 ghost 按钮。

**验收**：左栏 4 面板全部可用；任务卡等宽；结果按钮 2 列等宽。

---

### 阶段 3：中央顶栏、标签与对话

#### 3.1 重写 TopBar

- [ ] 移除品牌块（移到左栏）。
- [ ] 左：当前对话标题 + 数据版本胶囊 + 状态标签。
- [ ] 右：连接状态、右栏开关图标、设置图标。
- [ ] 标题过长省略，`title` 全文。
- [ ] 右栏开关 `title` 随状态切换。
- [ ] TopBar 只覆盖中栏，不再是全宽。

#### 3.2 StatusChip

- [ ] 新增 `StatusChip.vue`。
- [ ] 状态映射：
  - queued：`排队中`（可用中性色）；
  - running：`运行中 · 阶段 x/9`，黄色；
  - succeeded：`已完成 · 9/9`，绿色；
  - failed：`失败 · 阶段 · 原因`，红色。
- [ ] 紧凑文案 + 全文 `fullText`。
- [ ] 只有 `scrollWidth > clientWidth` 时设置 `title`；未截断不设置。
- [ ] 点击触发 `open-tasks`，AppShell 将 `activePanel` 设为 `progress`。
- [ ] 成功态也显示，不隐藏。
- [ ] 无任务时整个 chip 隐藏。

#### 3.3 标签

- [ ] `ResultTabKey` 固定为 7 个：
  - `chat`
  - `score`
  - `report`
  - `metrics`
  - `basis`
  - `ml`
  - `kg`
- [ ] 更新 `shellConfig.ts` 的标签配置与持久化校验。
- [ ] 移除旧的一级结果导航（如果有）。
- [ ] 关闭标签只关视图，不删产物；全部关闭回 `chat`。

#### 3.4 ChatView 输入区

- [ ] 按 demo3 重构：
  - 模式切换 + 思考档位在输入卡片**外**；
  - 快捷提问 chips 在输入卡片上方；
  - 卡片内只有 textarea + 渐变发送键。
- [ ] 思考档位改为胶囊 + 弹出层（低/中/高），默认高。
- [ ] 离线直调：思考胶囊置灰、发送键隐藏、卡片内换工具表单。
- [ ] 保留发送、流式、工具调用、SSE 逻辑。
- [ ] `Ctrl + Enter` 发送。

#### 3.5 结果摘要卡与五维彩条

- [ ] 新增 `ScoreBars.vue`：
  - 输入 before/after；
  - 实色前值 + 渐变增量；
  - 真实宽度；
  - 图例；
  - `title` 完整数值。
- [ ] 摘要卡使用 `ScoreBars`，包含综合分、数据量小表、隔离≠修复提示、打开完整视图。
- [ ] 五维评分完整视图复用同一组件，避免两套画法。

**验收**：聊天默认；输入区结构完全按 demo3；状态标签全部状态；五维彩条语义正确。

---

### 阶段 4：右栏

- [ ] 新增 `src/components/RightRail.vue`。
- [ ] 数据：
  - 当前任务：tasks store（id、status、stage、progress、duration、data_version）；
  - 版本/T1/T2：result；
  - 全局最近产物：artifacts store，按最近生成/打开排序；
  - 结果入口：切 `activeTab` 到 7 个标签。
- [ ] 只读：不出现上传/删除/模型管理。
- [ ] 收起方式：
  - 顶栏右栏图标；
  - 右栏头部箭头；
  - 收起后右缘 24px「上下文」竖条。
- [ ] 收起状态 `localStorage`。
- [ ] `≤1360px` 浮层，点击结果入口后自动关闭。
- [ ] 空态/加载/错误态。
- [ ] 移除原型里的「演示状态」控件。

**验收**：右栏真实数据；三种收起/展开；窄屏浮层；无假数字。

---

### 阶段 5：对话树

- [ ] 左栏树面板接入现有缩略图数据。
- [ ] `ConversationTreeWindow` 换皮肤：
  - 网格画布；
  - 当前节点渐变；
  - 旧支淡显；
  - 缩略卡；
  - 「在此继续」soft 按钮；
  - 刷新/关闭/Esc。
- [ ] `branchFrom(tipId)` 走现有 tree store/API，不伪造节点。
- [ ] 新分支后刷新左栏缩略/当前路径。
- [ ] 知识图谱「在对话树中继续」打开对应节点。
- [ ] 对话内「打开对话树」入口。
- [ ] 补充/更新 tree layout 与 branchFrom 测试。

**验收**：树看起来是树；悬停有缩略；能跳回；能分支且旧支保留。

---

### 阶段 6：结果视图

- [ ] 五维评分：综合分、`ScoreBars`、数据量、版本/T1T2。
- [ ] 评估报告.md：真实报告接口，复制/下载。
- [ ] metrics.json：真实指标，格式化/复制/下载。
- [ ] 方案依据：真实方案版本/时间边界。
- [ ] 迭代二模型：
  - 分类/聚类/降维真实指标；
  - 模型列表/详情/下载走 models store；
  - 模型编号 = 任务编号。
- [ ] 知识图谱：真实图谱数据；在树中继续。
- [ ] 所有空态/加载/错误态真实。

**验收**：7 个标签全部接真实数据；无硬编码业务数字。

---

### 阶段 7：视觉与按钮层级

- [ ] 渐变只用于：品牌、发送、进度、当前标签/图标、综合分、彩条增量、树当前节点。
- [ ] 普通按钮：
  - 新建对话：虚线；
  - 大多数：ghost；
  - 少数关键动作：soft 浅紫；
  - 渐变只保留发送 + 设置保存。
- [ ] 卡片 18px、控件 13px、小控件 8–9px。
- [ ] 正文 14.5px，表格 11.5–12.5px。
- [ ] 亮色，无暗色切换。
- [ ] 窄屏 760px 不横向滚动。

---

### 阶段 8：测试、构建与验收

#### 8.1 自动化

- [ ] 更新 `AppShell` 测试：三栏结构、TopBar 在中央、RightRail 存在、旧全宽壳消失。
- [ ] 更新 `TopBar` 测试：品牌移除、标题/版本/状态/连接/右栏/设置。
- [ ] 更新 `LeftRail` 测试：品牌头、Logo 点击收起、箭头旋转、任务卡等宽。
- [ ] 新增 `StatusChip` 测试：四种状态、截断 title、点击打开任务。
- [ ] 新增 `RightRail` 测试：真实数据映射、全局产物排序、三种收起。
- [ ] 新增 `ScoreBars` 测试：宽度、颜色、图例、title。
- [ ] 更新 `TaskDetail` 测试：结果按钮 2 列、第 5 个跨列。
- [ ] 更新 `ChatView` 测试：模式/档位在卡片外、卡片内只有 textarea + send。
- [ ] 保留并跑通 tree/SSE/artifact/resource 测试。
- [ ] `npm run test` 全过。
- [ ] `npm run build`（含 `vue-tsc`）通过。

#### 8.2 手工矩阵

| 宽度 | 检查 |
|---|---|
| 1440 | 三栏、60px 对齐、右栏展开 |
| 1360 | 右栏浮层/边缘条 |
| 1200 | 右栏收起、边缘条 |
| 1100 | 左栏抽屉 |
| 900 | 图标条 + 抽屉，品牌只留 Logo |
| 760 | 版本/连接隐藏，状态标签可用，无横向滚动 |

状态：

- queued / running / succeeded / failed；
- 无任务空态；
- 接口失败错误态；
- 长标题、长错误、长产物名。

交互：

- 点 ML Logo 收起/展开；
- 底部箭头方向对称；
- 右栏三种收起/展开；
- 7 个标签打开/关闭；
- 状态标签点击打开任务；
- 树跳回/分支；
- 五维彩条 hover 数值；
- 真实上传/下载/模型下载/设置测试。

---

## 4. 文件级改动清单

### 4.1 壳与全局

| 文件 | 动作 |
|---|---|
| `src/components/AppShell.vue` | **重写**：三栏、TopBar 入中栏、RightRail、收起状态 |
| `src/components/TopBar.vue` | **重写**：标题/版本/状态/连接/右栏/设置 |
| `src/components/LeftRail.vue` | 增加品牌头插槽/品牌组件、换皮肤、保持拖宽/抽屉 |
| `src/components/BrandHeader.vue` | **新增** |
| `src/components/RightRail.vue` | **新增** |
| `src/components/StatusChip.vue` | **新增** |
| `src/components/ScoreBars.vue` | **新增** |
| `src/components/CenterTabs.vue` | 固定 7 标签、换样式 |
| `src/components/shellConfig.ts` | 更新 `ResultTabKey`、标签配置、右栏存储 key |
| `src/theme/index.ts` | 增加 demo3 token / Naive UI 主题映射 |
| `src/styles/global.css` | 引入/补充全局 token |
| `src/styles/shell-v2.css` | **新增** 全局壳层样式（或等价文件） |
| `src/views/tabs/ChatView.vue` | 重构输入区、摘要卡、布局 |
| `src/views/tabs/ArtifactView.vue` | 换容器/按钮/空态样式 |
| `src/components/progress/TaskDetail.vue` | 结果按钮 2 列网格 |
| `src/views/panels/ConversationListPanel.vue` | 换卡片/新建按钮 |
| `src/views/panels/ConversationTreePanel.vue` | 缩略图/打开大图 |
| `src/views/panels/ResourcesPanel.vue` | 换卡片/上传区/按钮 |
| `src/views/panels/TasksPanel.vue` | 任务卡等宽/按钮 |
| `src/components/tree/ConversationTreeWindow.vue` | 换皮肤/保留 branchFrom |
| `src/components/tree/TreeThumbnail.vue` | 换缩略卡样式 |
| `src/components/SettingsModal.vue` | 按钮层级/间距 |
| `docs/prototypes/new-frontend-demo.html` | 放入原型（路径可按团队约定） |

### 4.2 数据层

优先复用，不新造 store：

- `stores/conversations.ts`
- `stores/chat.ts`
- `stores/tree.ts`
- `stores/tasks.ts`
- `stores/resources.ts`
- `stores/models.ts`
- `stores/centerTabs.ts`
- `stores/artifacts.ts`
- `stores/settings.ts`
- `stores/connection.ts`

只在必要时增加**选择器/组合函数**（例如右栏的 `currentTaskSummary`、`globalRecentArtifacts`），不复制数据、不建第二套状态。

### 4.3 测试

| 文件 | 动作 |
|---|---|
| `src/components/__tests__/AppShell.spec.ts`（以实际路径为准） | 更新三栏/右栏 |
| `TopBar` 测试 | 更新品牌移除/状态标签 |
| `LeftRail` 测试 | 增加品牌点击/箭头/任务卡 |
| `StatusChip` 测试 | 新增 |
| `RightRail` 测试 | 新增 |
| `ScoreBars` 测试 | 新增 |
| `TaskDetail` 测试 | 更新结果网格 |
| `ChatView` 测试 | 更新输入区结构 |
| tree/SSE/artifact/resource 测试 | 保持并跑通 |

---

## 5. 数据接线要点

### 5.1 顶栏标题与版本

- 标题：`conversations.current` 的标题；无对话时「新对话」。
- 数据版本：当前任务 `result.data_version`；无结果时显示占位「数据版本 —」，不编造 v3。
- 状态：`tasks.current` 的 `status/stage/progress/error`。

### 5.2 右栏

- 当前任务：`tasks.current`；无任务显示空态。
- 版本/T1/T2：当前任务 result；无结果显示「任务完成后显示」。
- 最近产物：`artifacts` 全局最近 N 条；点击 `centerTabs.open`。
- 结果入口：只在对应产物存在时可用；不存在时禁用或隐藏，不伪造。

### 5.3 树

- 节点/边来自 `tree` store / `treeLayout`。
- 当前节点来自 tree store。
- 「在此继续」调用现有 `branchFrom(tipId)`；成功后刷新树和左栏缩略。
- 失败时 toast/错误态，不本地伪造节点。

### 5.4 状态标签

- 状态映射函数放 `StatusChip.vue` 或独立 `statusText.ts`，便于测试。
- `fullText` 与 `compactText` 分开；截断检测在 `onMounted` / `watch` / `ResizeObserver` 中做。
- 只有截断才设 `title`。
- 点击 emit `open-tasks`，AppShell 设 `activePanel='progress'`。

---

## 6. 风险与处理

| 风险 | 处理 |
|---|---|
| 直接替换 AppShell 导致现有测试大面积失败 | 先跑基线；按测试文件逐个更新，不删除断言 |
| Naive UI 样式与自定义 CSS 冲突 | 优先 CSS 变量/作用域样式；不换组件库 |
| 右栏新增挤压中栏 | 320px；≤1360 浮层；中栏 min-width 0 |
| 树布局/分支状态被换皮肤破坏 | 只改展示层，不动 `treeLayout.ts`/`branchFrom` 逻辑 |
| SSE/标签状态错乱 | 保留 `activeTab` 唯一事实源；补 conversation 切换测试 |
| 真实数据空态导致页面空 | 每个区块做 skeleton/empty/error；不显示假数字 |
| 状态标签过长 | compact/full 分离；max-width + ellipsis；仅截断设 title |
| 任务卡/结果按钮宽度回退 | 加组件测试/截图检查；`width:100%` + grid |
| 渐变滥用 | 代码审查只允许发送/保存及指定装饰；其余 ghost/dashed/soft |

---

## 7. 提交顺序建议

1. `chore: baseline tests and prototype`
2. `feat(shell): three-column shell + tokens`
3. `feat(left): brand header, collapse, panel restyle`
4. `feat(topbar): center topbar + status chip`
5. `feat(chat): composer restructure + score bars`
6. `feat(right): context rail with real data`
7. `feat(tree): tree skin + branch integration`
8. `feat(results): 7 tabs + real result views`
9. `test: update shell/topbar/left/right/chat/tree`
10. `style: button hierarchy + responsive polish`

> 每个提交都要能跑 `npm run test`；最后统一 `npm run build`。

---

## 8. 完成定义（DoD）

- [ ] `AppShell.vue` 已直接替换，旧全宽壳不存在。
- [ ] 三栏结构与 60px 对齐符合原型。
- [ ] 左栏品牌可收起；Logo/箭头双向切换；任务卡等宽。
- [ ] 中央顶栏标题/版本/状态/连接/右栏/设置齐全；状态标签运行中/成功/失败都显示。
- [ ] 7 个结果标签固定；不再有一级结果导航。
- [ ] 输入区结构完全按 demo3；发送/设置保存之外无渐变按钮。
- [ ] 五维彩条为实色前值 + 渐变增量，真实宽度，带图例。
- [ ] 右栏只读、真实数据、全局最近产物、三种收起方式。
- [ ] 树可跳回、可分支、旧支保留，走真实后端。
- [ ] 资源/模型/设置/图谱全部接真实数据。
- [ ] 所有空态/加载/错误态真实，无硬编码业务数字。
- [ ] 现有测试 + 新增测试全过；`npm run build` 通过。
- [ ] 1440/1360/1200/1100/900/760 手工矩阵通过。
- [ ] 暗色未做（按约定下一版）。

---

## 9. 不在本计划内

- 暗色主题；
- 移动端完整功能；
- 树的分支合并/diff/导出；
- 多用户/权限；
- 自定义评分方案编辑器；
- 新后端接口/新 Agent 服务。

---

*实施时以 `new-frontend-spec.md` v1.2 为准；如真实数据暴露新的空态/字段，按 spec 的「不伪造」原则处理并记录 blocker。*
