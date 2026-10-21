<script setup lang="ts">
// 阶段 D · FD.3 →（产物超链接）：消息流视图。逐条渲染 `chat.messages`（用户 / 助手），
// 流式中的助手消息追加一个打字机光标；错误挂在该条消息下。
// 助手回合带来的产物（§3.12）以**超链接**呈现在消息下方一行，点击请求中栏切到该产物标签。
// 文本本身的「逐段出现」由 chat store 累积 `message_delta` 驱动（组件只做绑定）。
// 助手文本按 Markdown 排版（ChatMarkdown）；用户消息保持纯文本（用户写什么显示什么）。
import { type PropType } from 'vue'
import type { ChatMessage } from '@/stores/chat'
import type { ArtifactRef, NodeId } from '@/api/types'
import ChatMarkdown from './ChatMarkdown.vue'

const props = defineProps({
  messages: {
    type: Array as PropType<ChatMessage[]>,
    required: true,
  },
  streamingNodeId: {
    type: Number as PropType<NodeId | null>,
    default: null,
  },
  /** 每条消息关联的产物引用（node_id → 产物列表）。 */
  artifacts: {
    type: Object as PropType<Record<NodeId, ArtifactRef[]>>,
    default: () => ({}),
  },
})

const emit = defineEmits<{
  (e: 'open-artifact', artifact: ArtifactRef): void
}>()

function isStreaming(msg: ChatMessage): boolean {
  return !msg.done && props.streamingNodeId === msg.node_id
}

function artifactsOf(nodeId: NodeId): ArtifactRef[] {
  return props.artifacts[nodeId] ?? []
}
</script>

<template>
  <div class="message-list" data-testid="chat-messages">
    <p v-if="!messages.length" class="message-list__empty" data-testid="chat-empty">
      还没有消息。选“自动”由模型选工具，或选“手动”自己选工具。
    </p>

    <article
      v-for="msg in messages"
      :key="msg.node_id"
      class="message"
      :class="`message--${msg.role}`"
      :data-testid="`message-${msg.role}`"
      :data-node-id="msg.node_id"
    >
      <div class="message__role">{{ msg.role === 'user' ? '我' : '助手' }}</div>
      <div class="message__body">
        <!-- 助手：按 Markdown 排版（标题/列表/代码块/表格/行内样式），
             数字护栏标红在 ChatMarkdown 内保留；用户消息：纯文本原样显示。 -->
        <ChatMarkdown
          v-if="msg.role === 'assistant'"
          class="message__text message__text--md"
          :text="msg.text"
          :warning-spans="msg.warningSpans ?? []"
        />
        <span v-else class="message__text">{{ msg.text }}</span>
        <span
          v-if="isStreaming(msg)"
          class="message__caret"
          data-testid="typing-caret"
          aria-hidden="true"
          >▍</span
        >
      </div>

      <p
        v-if="msg.warning && !msg.warningSpans?.length"
        class="message__warning"
        data-testid="message-guard-warning"
      >
        {{ msg.warning }}
      </p>

      <p
        v-if="artifactsOf(msg.node_id).length"
        class="message__artifacts"
        data-testid="message-artifacts"
      >
        <span class="message__artifacts-label">产物：</span>
        <template v-for="(artifact, index) in artifactsOf(msg.node_id)" :key="artifact.id">
          <a
            class="message__artifact-link"
            href="#"
            :data-testid="`artifact-link-${artifact.id}`"
            @click.prevent="emit('open-artifact', artifact)"
            >{{ artifact.title || artifact.id }}</a
          >
          <span
            v-if="index < artifactsOf(msg.node_id).length - 1"
            class="message__artifact-sep"
            aria-hidden="true"
            >·</span
          >
        </template>
      </p>

      <p v-if="msg.error" class="message__error" data-testid="message-error">
        {{ msg.error.code }}：{{ msg.error.message }}
      </p>
    </article>
  </div>
</template>

<style scoped>
.message-list {
  display: flex;
  flex-direction: column;
  gap: 24px;
  max-width: 780px;
  margin: 0 auto;
  padding: 28px 24px 8px;
}
.message-list__empty {
  margin: 40px auto;
  max-width: 440px;
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-md, 16px);
  line-height: var(--ml-leading, 1.7);
  text-align: center;
}
.message {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.message--user {
  align-items: flex-end;
}
.message--assistant {
  align-items: flex-start;
}
.message__role {
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-xs, 13px);
  font-weight: 500;
}
.message__body {
  max-width: 100%;
  color: var(--n-text-color-1, #111827);
  font-size: var(--ml-text-md, 16px);
  line-height: var(--ml-leading, 1.7);
  word-break: break-word;
}
/* 用户：右侧浅灰气泡；助手：无气泡、整段舒展。 */
.message--user .message__body {
  max-width: 82%;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  background: var(--ml-surface-sunken, #f0f1f3);
}
.message--user .message__text {
  white-space: pre-wrap;
}
.message--assistant .message__body {
  padding: 2px 0;
}
/* 助手消息是 Markdown 排版：块级元素自己控间距，不套 pre-wrap。 */
.message__text--md {
  display: block;
  white-space: normal;
}
.message__caret {
  display: inline-block;
  margin-left: 2px;
  color: var(--n-primary-color, #2f6fed);
  animation: caret-blink 1s steps(2, start) infinite;
}
.message__num-warn {
  color: var(--n-error-color, #d03050);
  text-decoration: underline wavy var(--n-error-color, #d03050);
  text-underline-offset: 3px;
}
.message__warning {
  align-self: stretch;
  margin: 0;
  padding: 7px 12px;
  border-radius: 9px;
  background: var(--n-warning-color-opacity, rgba(240, 160, 32, 0.12));
  color: var(--n-warning-color, #b45309);
  font-size: var(--ml-text-sm, 14px);
}
.message__artifacts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 2px 0 0;
  font-size: var(--ml-text-sm, 14px);
}
.message__artifacts-label {
  color: var(--n-text-color-3, #6b7280);
}
.message__artifact-link {
  color: var(--n-primary-color, #2f6fed);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}
.message__artifact-sep {
  color: var(--n-text-color-3, #9ca3af);
}
.message__error {
  align-self: stretch;
  margin: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--n-error-color-opacity, rgba(208, 48, 80, 0.1));
  color: var(--n-error-color, #d03050);
  font-size: var(--ml-text-sm, 14px);
}
@keyframes caret-blink {
  to {
    visibility: hidden;
  }
}
</style>
