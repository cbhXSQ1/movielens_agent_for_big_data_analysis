# 前端软护栏行内标红报告

## 状态

完成。前端已按计划 Task B 实现：无溯源数字直接在助手回答原文中行内标红，悬停显示原因；没有位置区间时在消息下方显示一条琥珀色提示。

## 变更文件

- `frontend/src/api/types.ts`：新增 `GuardSpan`，`RoundEnvelope` 增加 `guard_warning`、`guard_spans`。
- `frontend/src/stores/chat.ts`：`ChatMessage` 增加 `warning`、`warningSpans`；`send()` 按 `envelope.node_id` 落位；节点未出现时按 node_id 暂存，消息建立后再挂上；历史加载与重置时清空暂存。
- `frontend/src/components/chat/MessageList.vue`：按 `warningSpans` 切分原文，命中片段渲染为 `.message__num-warn`，红色文字加波浪下划线，悬停提示「未能在工具结果中找到」；只有 `warning` 时显示兜底提示。原文内容不变。
- `frontend/src/__tests__/chat.guard.test.ts`：新增 5 条测试，覆盖落位、暂存、标红、无区间、兜底提示。

## TDD 记录

1. 先写测试并运行 `npx vitest run src/__tests__/chat.guard.test.ts`。
2. 初始结果：`5 tests | 4 failed`。失败原因分别是节点上没有 warning/spans、页面找不到标红片段、找不到兜底提示，符合功能尚未实现的预期。
3. 实现后同一命令结果：`5 passed`。

## 全量验证

- `npm run test`：`Test Files 46 passed (46)`，`Tests 369 passed (369)`。
- `npm run build`：类型检查通过，`vite build` 成功；仅有既有的 chunk 超过 500 kB 提示，不是错误。

## 顾虑

- 区间来自后端；前端会丢弃越界、空区间和重叠区间，避免切片异常。若后端区间与原文不一致，该数字不会标红，但原文仍完整显示。
- 产物先到、消息后到的暂存逻辑保持原样；数字提示使用按 node_id 分开的暂存，不与产物暂存混用。
- 报告与前端改动一起提交；并行任务修改的 `agent/` 文件未纳入本次提交。
