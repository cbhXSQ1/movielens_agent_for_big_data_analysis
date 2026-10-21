# 中栏资源标签基建（注册表 + 模型/方案/文件视图）

Task: T1 — 把资源项（模型 / 方案 / 任务文件）作为**中栏标签**打开，替代左栏内联预览。
本任务只做**基建 + 视图**；不改左栏各面板的按钮（留待后续任务）。

## 变更文件

新增：
- `frontend/src/components/centerTabs.ts` — 资源标签类型 + key 构造。
- `frontend/src/stores/centerTabs.ts` — `useCenterTabsStore`（开合 / 去重 / 持久化）。
- `frontend/src/components/centerTabViews.ts` — `kind → 组件` 注册表 + 标签构造助手。
- `frontend/src/components/results/ml/modelArtifacts.ts` — 模型产物 role 分组 / 文本 kind 判定（共用）。
- `frontend/src/views/tabs/ModelDetailView.vue` — 模型详情中栏视图。
- `frontend/src/views/tabs/SchemeContentView.vue` — 方案内容中栏视图（美化 JSON）。
- `frontend/src/views/tabs/FilePreviewView.vue` — 产出文件中栏预览视图。
- 测试：`src/__tests__/stores.centerTabs.test.ts`、`centerTabs.views.test.ts`、`centerTabs.tabs.test.ts`。

修改：
- `frontend/src/components/shellConfig.ts` — `SHELL_STORAGE_KEYS` 增 `centerTabs: 'movielens.centerTabs'`。
- `frontend/src/components/CenterTabs.vue` — 标签集合追加资源标签；按 `kind` 经注册表渲染；关闭回落「对话」。

## 导出的接口

`@/components/centerTabs`
- `type ResourceTabKind = 'model' | 'scheme' | 'file'`
- `interface ShellTab { key; label; kind; payload }`
- `modelTabKey(modelId)` / `schemeTabKey(name)` / `fileTabKey(taskId, path)`

`@/stores/centerTabs`
- `useCenterTabsStore` → `{ tabs, open, close, isOpen, list, clear }`

`@/components/centerTabViews`
- `RESOURCE_TAB_VIEWS: Record<ResourceTabKind, Component>`
- `resourceViewFor(kind): Component | null`
- `modelTab(model)` / `schemeTab(name, label?)` / `fileTab(taskId, path, label?)`

`@/components/results/ml/modelArtifacts`
- `MODEL_ROLE_ORDER` / `MODEL_ROLE_LABEL` / `MODEL_TEXT_KINDS`
- `isTextArtifactKind(kind)` / `groupArtifactsByRole(artifacts)`

## 关键设计

- **key 命名**：资源 key 带 `resource:` 前缀（URL-ish），与产物标签 id（`scheme:xxx.json`、`task:t1:x`）
  不撞车：`resource:model:<id>` / `resource:scheme:<name>` / `resource:file:<taskId>:<path>`（taskId/path 各自 `encodeURIComponent`）。
- **去重语义**：`open(tab)` 按 key 去重，已存在则原位置就地刷新（不改序、不重复）。
- **持久化**：每次变更写 `localStorage[SHELL_STORAGE_KEYS.centerTabs]`；`readTabs()` 宽松校验并丢弃坏条目，
  存储不可用时全链路静默容错。
- **注册表分发**：新增标签种类只需补 `ResourceTabKind` + `RESOURCE_TAB_VIEWS` 登记，`CenterTabs` 不动。
- **复用**：模型指标按 `task_type` 复用 `results/ml` 结果视图（`mlViewFor`，直接喂 `model.metrics`）；
  下载走 `@/api/models` 的 `modelDownloadUrl`；方案 / 文件内容均经 resources store（组件不直接 fetch）。
- **payload 即 props**：`modelTab → { modelId }`、`schemeTab → { name }`、`fileTab → { taskId, path }`，
  容器用 `<component :is v-bind="payload">` 渲染。

## 运行命令与结果

```
cd frontend
npm run test    # 43 files / 332 tests 全通过
npm run build   # vue-tsc --noEmit 通过；vite build 成功（仅既有 chunk>500kB 警告）
```

新增测试单跑：`npx vitest run src/__tests__/stores.centerTabs.test.ts src/__tests__/centerTabs.views.test.ts src/__tests__/centerTabs.tabs.test.ts` → 3 files / 22 tests 通过。

## 遗留 / 顾虑

- `ModelsSection.vue` 仍内联一份 role 分组逻辑；本次未动它（属后续「改面板按钮」任务），
  新的共用件 `modelArtifacts.ts` 已就绪，后续可迁移过去以消除重复。
- `AppShell.vue` 的 `activeTab` 恢复只校验 `['chat', ...artifacts.openIds]`，刷新后若上次停在资源标签
  会回落「对话」（标签仍在，只是不自动选中）。未在 T1 范围内调整。
- 资源标签为**跨对话全局**（与对话无绑定）；切换对话时 `CenterTabs` 会把激活标签回落「对话」，资源标签保留。
