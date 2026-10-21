# FE-5 报告：版本与时间边界展示

## 状态
完成。前端单测全绿（30 个文件、219 条用例），`npm run build`（`vue-tsc --noEmit` + `vite build`）通过。

## 目标
远端 `get_task_result` 已把 `versions`（四类：data/rule/scoring/policy）与
`time_boundaries`（T1/T2）透传进结果板块（Agent 侧已由 AG-15 落库）。前端要**直接展示**
后端给的原值，不推断、不换算；板块缺席时不显示。

## 改动文件（仅前端）
- `frontend/src/composables/useResultBlocks.ts`
  - 新增 `versions` / `timeBoundaries` 两个 computed：从 `result.blocks.versions` /
    `blocks.time_boundaries` **原值透传**，非记录/缺席时返回 `null`（不造空壳）。
  - `useResultBlocks` 新增可选参数 `source`（`MaybeRefOrGetter<ResultPayload|null|undefined>`）：
    组件可绑定某次具体任务的结果；缺省仍绑定 `store.latestResult`（既有行为不变，兼容既有用例）。
  - 新增纯展示辅助（导出）：`versionRows(raw)`、`timeBoundaryRows(raw)`，把原值拆成
    `{ key, label, value }` 展示行——后端给哪些键就显示哪些，未知键回落为键名；
    值若为字符串原样、若带 `version` 取其 `version`、其余 JSON 字符串化。仅做标签与显示，
    不改动、不推算任何业务数值。
- `frontend/src/components/progress/TaskDetail.vue`
  - 结果详情新增「版本」「时间边界（训练期/验证期）」小字区块（`data-testid="task-result-versions"`；
    行级 `task-version-<key>` / `task-time-boundary-<key>`）。区由 `loaded && (versions || timeBoundaries)`
    控制，缺席即不渲染。该区块绑定的是**本次任务**的结果（`useResultBlocks(result)`，`result` 为
    `tasks.getResult(agent_task_id)`），避免跨任务串值。
- 测试
  - `frontend/src/__tests__/composables.test.ts`：新增 FE-5 用例——无结果时 `versions`/`timeBoundaries`
    为 `null`；有值时为后端原值；板块缺席时保持 `null`（不造空壳）。
  - `frontend/src/__tests__/result.versions.test.ts`（新增）：任务详情组件用例——有值与时间边界时
    逐项显示原值（含 section 标题与 T1/T2 值）；只给部分版本时只显示这些键；板块缺席时该区不出现。

## TDD 过程
先写用例并运行确认失败（5 条按预期失败：composable 的 `versions` 未定义、组件 `task-result-versions`
区缺失），再实现至全绿。

## 验证
- `npm run test`（frontend/）：30 个文件、219 条用例全部通过。
- `npm run build`（frontend/）：`vue-tsc --noEmit` 无类型错误，`vite build` 成功。

## 约束
- 用户可见文案无「冒烟/对账/口径」。
- 仅改动前端文件；未触碰 `frontend/src/api/http.ts`（并发任务在改）与任何非前端文件。
- 展示的版本/时间边界值均直接来自后端板块，无任何推断。

## 关注点
- `versions` 形态两种都兼容：扁平字符串（`{"data":"...","rule":"1.0.0"}`）与带 `sha256` 的嵌套对象
  （`{"rule":{"version":"..","sha256":".."}}`）；嵌套对象取 `version` 显示，无 `version` 则原样 JSON。
- 时间边界顺序固定为 T1、T2（其余键按后端顺序附后）；标签为展示用中文，值原样。
- 展示位置选在任务详情（进度面板），因为它是消费本次任务结构化结果的视图；本任务未改动
  产物标签页 `ArtifactView.vue`（其结果上下文与该区无关）。
