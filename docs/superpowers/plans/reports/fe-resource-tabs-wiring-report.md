# fe-resource-tabs-wiring — 资源面板「查看/预览」改为中栏开标签

## 状态
完成。左栏资源面板（模型 / 方案 / 产出文件）的「查看 / 预览」不再在左栏内联渲染，
改为向中栏资源标签 store（`useCenterTabsStore`）登记标签；内联预览/详情区块及相关状态已删除。
`npm run test` 与 `npm run build` 均通过。

## 改动文件
- `frontend/src/components/resources/ModelsSection.vue`
  - 「查看」改为 `centerTabs.open(modelTab(model))`。
  - 删除内联模型详情 / 产物分组 / 内联产物预览（`model-detail`、`model-artifact-*`、`model-preview-*`）
    及 `current`/`detailLoading`/`groups`/`preview`/`previewText`/`openPreview`/`downloadUrl`
    等状态与计算；删除相应样式。保留列表 + 刷新 + 指标摘要。
- `frontend/src/components/resources/SchemeSection.vue`
  - 「查看」改为 `centerTabs.open(schemeTab(schemeName(scheme)))`。
  - 删除内联方案内容面板（`scheme-content`）及 `content` 状态、`viewScheme`（不再左栏 `loadSchemeContent`）。
  - 保留列表、上传（含 validation/overwritten 回显）、删除。保留 `resource-block` 样式（validation 仍在用）。
- `frontend/src/components/resources/TaskFilesSection.vue`
  - 「预览」改为 `centerTabs.open(fileTab(hadoopTaskId, file.path))`（taskId 为 Hadoop 任务号）。
  - 删除内联预览面板（`file-preview-panel`）及 `preview` 状态、`previewText`、`openPreview`、
    `MarkdownView`/`describeError` 导入与相关样式。保留文件列表、类别徽标、下载链接。
- `frontend/src/__tests__/models.panel.test.ts`
  - 详情/预览/下载相关断言改为断言中栏标签已打开（`useCenterTabsStore().isOpen(modelTabKey(...))`），
    并断言左栏不再内联渲染详情/产物（`model-detail` / `model-artifact-*` / `model-preview-panel` 不存在），
    且「查看」不再在左栏内联拉取模型详情。保留列表断言。
- `frontend/src/__tests__/resources.panel.test.ts`
  - 方案「查看」与文件「预览」改为断言中栏标签已打开（`schemeTabKey('scoring_scheme.v1.json')` /
    `fileTabKey('job_1', ...)`），并断言左栏不再有内联内容面板 / 不再内联请求
    `/api/schemes/content`、`/api/files/preview`。移除不再使用的 `schemeContent`/`preview` 假端点。
    保留列表、上传、删除、类别徽标、hadoop_task_id 一致性、错误隔离等断言。

## 执行命令与结果
```
# 目标测试（RED → GREEN）
npx vitest run src/__tests__/models.panel.test.ts src/__tests__/resources.panel.test.ts
# 实现前：2 files failed | 7 failed | 13 passed（预期：isOpen 为 false / 内联仍渲染）
# 实现后：Test Files 2 passed (2)；Tests 20 passed (20)

# 全量测试
npm run test
# Test Files 43 passed (43)；Tests 328 passed (328)；Duration ~8.9s

# 构建
npm run build   # vue-tsc --noEmit && vite build
# vue-tsc 通过；vite build 成功（built in 5.47s）
# 仅剩既有 chunk >500kB 的告警，与本改动无关
```

## 备注 / 关注点
- 中栏标签视图（`ModelDetailView` / `SchemeContentView` / `FilePreviewView`）为已落地基建，本次未改动；
  预览/下载能力已由这些视图承担，面板测试不再覆盖其内部渲染（由 `centerTabs.views.test.ts` 覆盖）。
- `SchemeSection` 的「查看」传入 `schemeName`（可能为 `config/x.json` 形态，与 store `loadSchemeContent` 一致性保持一致；
  删除仍走单层文件名 `schemeFile`）。
- 环境噪声：vitest 打印 `--localstorage-file was provided without a valid path` 警告，与本次改动无关。
