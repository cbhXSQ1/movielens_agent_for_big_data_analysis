# 对话树浮窗：描述缩略改为仅悬停显示

## 问题

浮窗中的节点描述（缩略窗）并非仅悬停出现：未悬停时会一直显示“当前节点”的描述，悬停其他节点时才切换。原因是 `selectedId` 在点击时被写入，且关闭/重开浮窗时不清空；`detailId = hoveredId ?? selectedId` 使点击过的节点持续“固定”住缩略窗。

## 修复（root cause）

`frontend/src/components/tree/ConversationTreeWindow.vue`

- 删除 `selectedId`、`onSelect()`、节点上的 `@click`，以及 `tree-round--selected` 的 class 与 scoped 样式。
- `detailId` 语义改为直接使用 `hoveredId`：缩略窗只在悬停时存在，`mouseleave` 立即隐藏。
- `watch(show)` 在打开和关闭时都重置 `hoveredId = null`，避免脏状态跨开关残留。
- 保留 `tree-round--current` 高亮与「在此继续」逻辑不变。
- `.tree-thumb` 保持 `pointer-events: none`，缩略窗不拦截鼠标事件，避免指针移向它时抖动。

`frontend/src/__tests__/tree.window.test.ts`

- 保留原有断言：未悬停无 `tree-thumb`；`mouseenter` 出现；`mouseleave` 消失。
- 新增回归测试：悬停后关闭并重开浮窗，未悬停前不得出现 `tree-thumb`。
- 测试中不再出现 `tree-round--selected`。

## 验证

- `npm run test`：44 个测试文件、353 个测试全部通过；`tree.window.test.ts` 9/9 通过。
- `npm run build`：`vue-tsc --noEmit` + `vite build` 成功（仅既有 chunk 体积提示）。

## 备注

新回归测试通过 `show` prop 驱动关闭/重开（`setProps({ show: false })` → `{ show: true }`），因为组件关闭时只 emit `update:show`；测试内直接重复设置 `show: true` 不会再次触发打开。关闭按钮本身仍由 FF.1 测试覆盖。
