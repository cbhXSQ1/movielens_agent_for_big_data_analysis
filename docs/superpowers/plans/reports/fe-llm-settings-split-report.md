# 设置与对话模式拆分报告

## 状态

完成。

## 改动

- `frontend/src/components/SettingsForm.vue`
  - 移除「启用大模型主控」表单项及 `NSwitch` 引入与使用。
  - 保留 API 地址、API Key、模型名、测试连接、保存、关闭。
  - 新增说明：这里只保存供应商连接；选“自动”才会调用模型，选“手动”不调用。
- `frontend/src/components/SettingsModal.vue`
  - 标题与 `aria-label` 改为「设置 · 大模型供应商」。
- `frontend/src/stores/settings.ts`
  - `hasOverride` 只看 API 地址、API Key、模型名三个字段是否非空。
  - `toOverride()` 在字段配齐时固定返回 `enabled: true`。
  - `enabled` 字段继续保留，并在读取、更新、保存时按三个字段归一化，兼容旧 localStorage。
- `frontend/src/views/tabs/ChatView.vue`
  - 模式标签改为「自动（用大模型）」与「手动（自己选工具）」。
  - 欢迎语、未配置供应商提示、手动调用消息一并改为白话。
  - 自动模式未配置供应商时仍可发送，由后端默认配置或结构化错误处理。
- `frontend/src/components/chat/MessageList.vue`
  - 空消息提示改为「选“自动”由模型选工具，或选“手动”自己选。」
- 测试
  - `settings.form.test.ts` 覆盖无启用开关、三个字段直接生成 `toOverride()`。
  - `stores.settings.test.ts` 覆盖三字段决定 `hasOverride`，包括旧数据 `enabled=false` 的情况。
  - `chat.test.ts` 覆盖模式标签与空消息白话提示。
  - `chat.reasoning.test.ts` 用例描述同步改为自动 / 手动。

## 测试与构建

- `npm run test`：44 个测试文件、349 个用例全部通过。
- `npm run build`：`vue-tsc --noEmit` 与 `vite build` 通过。

## 关注点

- 构建仍有主 chunk 超过 500 kB 的既有告警（667.06 kB），与本次改动无关。
- `enabled` 仅作为旧存储兼容字段保留，不再参与是否使用模型的判断。
