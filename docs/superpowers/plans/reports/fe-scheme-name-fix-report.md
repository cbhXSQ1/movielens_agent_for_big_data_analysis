# 方案查看文件名归一修复报告

## 问题

在左栏「方案文件」段点击「查看」，中栏打开标签 `config/cleaning_rules.v1.json`，
内容区报错：

> name 必须是方案文件名（形如 scoring_scheme.v1.json），收到 'config/cleaning_rules.v1.json'

## 根因

`SchemeSection.vue` 的 `openScheme()` 使用 `schemeName()`（`scheme.name ?? scheme.path`）。
远端列表通常只返回 `path: 'config/x.json'`，导致：

1. 中栏标签 key / label 变成 `config/x.json`；
2. `SchemeContentView` 把该路径原样传给 `getSchemeContent`，而远端能力服务只接受裸文件名。

删除路径此前已用 `schemeFile()` 取 basename，查看路径遗漏了同样的归一。

## 修复（defense in depth）

- `frontend/src/components/centerTabViews.ts`
  - 新增并导出 `schemeFileName(value)`：取最后一段路径，兼容 `/` 与 `\`。
- `frontend/src/components/resources/SchemeSection.vue`
  - `schemeFile()` 复用 `schemeFileName()`；
  - `openScheme()` 改用 `schemeFile()`，标签 label / payload 均为裸文件名。
- `frontend/src/views/tabs/SchemeContentView.vue`
  - 请求前用 `schemeFileName(props.name)` 归一，标题展示 basename。
- `frontend/src/views/tabs/BasisView.vue`、`frontend/src/components/RightRail.vue`
  - 打开方案时对 `artifact.scheme_name` 做同样归一。
- `frontend/src/views/tabs/ArtifactView.vue`
  - 取方案内容前也归一 `scheme_name`（同类防御）。

## 测试

- `resources.panel.test.ts`：列表只给 `path: 'config/cleaning_rules.v1.json'` 时，
  点击查看打开 `schemeTabKey('cleaning_rules.v1.json')`，label / payload 均为裸文件名。
- `centerTabs.views.test.ts`：
  - `SchemeContentView` 收到 path 形状的 `name` 时，以 `cleaning_rules.v1.json` 调
    `getSchemeContent`，标题显示 basename；
  - `schemeFileName` 单测覆盖 `/`、`\`、裸文件名与空串。
- 既有测试保持通过。

## 验证

- `npm run test`：44 个测试文件、345 个用例全部通过。
- `npm run build`：`vue-tsc --noEmit` + `vite build` 通过。

## 备注

- 已知构建告警：主 chunk 666.98 kB 超过 500 kB，属既有体积提示，与本次修复无关。
- 未改动删除链路行为；默认方案保护逻辑不变。
