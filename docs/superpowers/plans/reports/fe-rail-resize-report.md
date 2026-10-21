# FE 报告：左栏可拖拽调宽 + 修顶部遮挡

范围：仅 frontend 文件（`frontend/`）。分支：`reconstruction`。

## 变更文件

修改（提交）：
- `frontend/src/components/shellConfig.ts` — `SHELL_STORAGE_KEYS` 新增 `leftWidth: 'movielens.shell.leftWidth'`；新增常量 `LEFT_RAIL_WIDTH = { min: 240, max: 560, default: 320, step: 16 }`（拖拽边界、默认、键盘步长单点定义）。
- `frontend/src/components/LeftRail.vue` —
  - 新增 `width` 模型（`defineModel<number>('width')`，默认 320），内联面板宽度经它回写。
  - 面板右缘新增分隔条 `data-testid="left-rail-splitter"`，仅宽屏展开态（`resizable = !narrow && !collapsed`）渲染：`pointerdown/move/up` 拖拽改宽（按指针位移、夹紧 MIN/MAX）、`dblclick` 复位 320、`keydown` 左右方向键 ±16px（阻止默认滚动）、`role="separator"` + `aria-orientation="vertical"` + `aria-valuenow/min/max` + `tabindex="0"`；`onBeforeUnmount` 清理 window 监听。
  - 面板宽度由固定 `flex: 0 0 320px; width: 320px` 改为内联 style（`width`/`flexBasis`），并加 `padding-top: 8px`（顶部呼吸感，修顶部遮挡）。
  - 新增 `.left-rail__splitter` 样式（6px 命中区、`col-resize`、hover/focus 时显示 2px 主色线）。
- `frontend/src/components/AppShell.vue` — 新增 `leftWidth` ref：启动时经 `readLeftWidth()` 从 localStorage 恢复（非法/越界回落默认并夹紧到 MIN/MAX），`watch` 回写 localStorage，`v-model:width="leftWidth"` 绑定到 LeftRail。

新增（提交）：
- `frontend/src/__tests__/leftRail.resize.test.ts` — 11 条用例（默认宽/恢复/拖拽改宽并持久化/夹紧/双击复位/方向键/可访问性语义/窄屏隐藏/收起隐藏/首行不被遮挡 ×2）。

未提交（按「仅提交 frontend 文件」约束）：本报告。

## TDD 记录

1. RED（先写测试后运行）：`npx vitest run src/__tests__/leftRail.resize.test.ts`
   结果：`Tests 10 failed | 1 passed (11)`；失败项为新增的分隔条/宽度/复位/方向键/顶部内边距断言，原因均为元素或属性缺失（`expected false to be true`、`expected undefined to be 'true'`），符合预期。（唯一通过项是「窄屏隐藏分隔条」——分隔条本就未实现，属平凡通过。）
2. GREEN（实现后同命令）：`Tests 11 passed (11)`。

## 全量验证

- 命令：`npm run test`（在 `frontend/`）
  输出：`Test Files 40 passed (40)`，`Tests 310 passed (310)`。
- 命令：`npm run build`（`vue-tsc --noEmit && vite build`）
  输出：类型检查无错误；`vite v6.4.4` 构建成功，`✓ built in 5.07s`（`dist/assets/index-*.js 684.04 kB`；仅 rollup 提示 chunk > 500kB，非错误）。

## 顶部遮挡：诊断与修复

诊断方法：`npm run build` 后用 `vite preview` 起静态服务，Edge headless 截图（1400×900），并对顶栏下方区域放大 3× 观察。

诊断结论（真实原因）：
- `.left-rail__panel` 是**满幅滚动容器**，此前**没有任何顶部内边距**（`overflow:auto; scrollbar-gutter:stable`，无 padding）；面板内容从 `app-shell__body` 顶部（即顶栏 72px 分隔线正下方）**贴边起排**。
- 各面板自带的首行内边距还不一致：列表/任务/对话树面板是 16px，而 `ResourcesPanel.vue` 的首行分段标签 `.resources-panel__tabs`（`padding: 10px 4px`）仅 10px——资源面板首行因此**紧贴顶栏分隔线**，观感上像被压住/切掉一截；同时图标条首图标有 14px 顶部内边距，两者顶部不对齐，进一步放大「贴边」错觉。
- 未发现 z-index/层叠或 sticky 头覆盖：顶栏是列向 flex 兄弟节点、无负 margin、无重叠层；图标条与面板并列，横向也无重叠（面板 240–560px、图标条 64px，互不侵占）。

修复：
- 给 `.left-rail__panel` 统一加 `padding-top: 8px`，所有面板首行（尤其资源分段标签行）不再贴边，获得稳定呼吸感；面板为 flex 拉伸高度，加顶部内边距后内容盒相应收缩，不会引入额外滚动或底部裁切。
- 拖拽调宽不改变该顶部留白（宽度只影响横向）。

对照验证：资源面板在左栏宽 320/460px 两种状态下截图，首行「原始数据 清洗后数据 方案文件 产出文件 模型」完整可见、下方留白约 18px；默认列表面板「新建对话」同样顶部留白、底部「刷新」正常，无回归。

## 关切 / 备注

- 分隔条命中区 6px，常态透明（视觉上由面板 1px 右边框分隔），仅 hover/focus 时显示 2px 主色线——保证命中易用且不喧宾夺主。
- MIN=240px 下资源面板 5 个 `white-space:nowrap` 标签（`flex:1 1 0`、`min-width:auto`）会略超容器宽度，由面板 `overflow:auto` 横向滚动兜底（不越界覆盖图标条）；若后续要更好观感可再允许标签换行或缩字号，本次未改以控制范围。
- 持久化键 `leftWidth` 存字符串像素值；非法值（NaN）回落默认，越界值读取即夹紧，写入始终为夹紧后的整数。
- 窄屏（≤1100px）抽屉模式行为完全不变：无分隔条，`n-drawer` 宽度仍 300，`rail-toggle` 仍隐藏。
