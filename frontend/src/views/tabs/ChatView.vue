<script setup lang="ts">
// 阶段 D · 中栏「对话」标签（fe-chat）。
// FD.1 输入区 + 模式切换（LLM 主控 / 离线直调）
// FD.2 离线工具表单（按工具表渲染，提交经 chat.send → sendByMode(offline)）
// FD.3 流式消息（chat store 累积 message_delta，打字机逐段显示；message_done 收尾）
// FD.4 内嵌结果卡（助手回合带来的结构化结果 → 点击切中栏标签）
// FD.5 对话切换/新建的消费端：发送时若尚无当前对话，先新建并开流（由 conversations store 负责）。
// 事件与数据一律经 store（chat / conversations / tasks），组件自身不直接 fetch。
//
// 观感重做：统一的输入卡片（圆角大、浅边框、柔和阴影）——
//   上：无边框 textarea（随内容长高）；下：模式分段控件 + 提示（左）/ 圆形发送按钮（右）。
//   无消息 → 欢迎语居中、卡片居中；有消息 → 消息在上滚动、卡片贴底。
import { computed, nextTick, ref, watch } from 'vue'
import { NButton, NInput, NRadioButton, NRadioGroup, NTag } from 'naive-ui'
import { storeToRefs } from 'pinia'
import MessageList from '@/components/chat/MessageList.vue'
import OfflineToolForm from '@/components/chat/OfflineToolForm.vue'
import { useArtifactsStore } from '@/stores/artifacts'
import { useChatStore } from '@/stores/chat'
import { useConversationsStore } from '@/stores/conversations'
import { useSettingsStore } from '@/stores/settings'
import { useResultTabNav } from '@/composables/useResultTabNav'
import type { AgentMode, ArtifactRef, NodeId, ReasoningEffort } from '@/api/types'

const chat = useChatStore()
const conversations = useConversationsStore()
const artifacts = useArtifactsStore()
const settings = useSettingsStore()
const nav = useResultTabNav()

const { visibleMessages, streamingNodeId, lastError, artifactIdsByNode } =
  storeToRefs(chat)
const { currentId } = storeToRefs(conversations)

/** 每条消息关联的产物引用（chat 记 id，artifacts store 记内容）：node_id → 产物列表。 */
const messageArtifacts = computed<Record<NodeId, ArtifactRef[]>>(() => {
  const cid = currentId.value
  const map: Record<NodeId, ArtifactRef[]> = {}
  for (const [nodeId, ids] of Object.entries(artifactIdsByNode.value)) {
    const refs = ids
      .map((id) => artifacts.find(cid, id))
      .filter((a): a is ArtifactRef => a !== null)
    if (refs.length) map[Number(nodeId)] = refs
  }
  return map
})

const mode = ref<AgentMode>('llm')
const text = ref('')
const submitError = ref<string | null>(null)

// 离线表单句柄：提交落定后由本视图推进其生命周期（进行中 → 收起回执 / 失败复位）。
const offlineForm = ref<InstanceType<typeof OfflineToolForm> | null>(null)

// 思考档位（仅 LLM 主控有意义）：默认 / 低 / 中 / 高 ↔ `reasoning_effort`。
// 默认档不带该字段（与后端缺省行为一致）；档位记在 localStorage，跨会话沿用。
type ReasoningLevel = 'default' | ReasoningEffort

const REASONING_STORAGE_KEY = 'movielens.reasoning.level'
const REASONING_EFFORT: Record<ReasoningLevel, ReasoningEffort | undefined> = {
  default: undefined,
  low: 'low',
  medium: 'medium',
  high: 'high',
}

function readReasoningLevel(): ReasoningLevel {
  try {
    if (typeof localStorage === 'undefined') return 'default'
    const raw = localStorage.getItem(REASONING_STORAGE_KEY)
    if (raw === 'low' || raw === 'medium' || raw === 'high') return raw
  } catch {
    /* 存储不可用时回落默认 */
  }
  return 'default'
}

const reasoning = ref<ReasoningLevel>(readReasoningLevel())
const reasoningEffort = computed(() => REASONING_EFFORT[reasoning.value])

watch(reasoning, (value) => {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(REASONING_STORAGE_KEY, value)
    }
  } catch {
    /* 存储不可用时静默忽略 */
  }
})

const hasConversation = computed(() => currentId.value !== null)
// 未在设置里配齐大模型：如实提示，发送仍走通（由后端默认配置决定是否可用）。
const llmUnconfigured = computed(() => mode.value === 'llm' && !settings.hasOverride)
// 空态：无可见消息时，欢迎语与输入卡片居中呈现。
const showHero = computed(() => visibleMessages.value.length === 0)
const canSend = computed(() => text.value.trim().length > 0 && !chat.sending)
const errorText = computed(() => {
  if (submitError.value) return submitError.value
  if (lastError.value) return `${lastError.value.code}：${lastError.value.message}`
  return null
})

// 消息 / 流式内容 / 产物链接 / 从空态进入会话 变化后，自动把消息区滚到底部。
// flush:'post' 保证在 DOM 更新之后再滚动；deep 兜住 message_delta 对同一条消息的续写。
const streamEl = ref<HTMLElement | null>(null)

function scrollStreamToBottom(): void {
  const el = streamEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

watch(
  [visibleMessages, streamingNodeId, messageArtifacts, showHero],
  () => {
    void nextTick(scrollStreamToBottom)
  },
  { deep: true, flush: 'post' },
)

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** 当前无对话时先新建并打开其推送流（stream 由 conversations store 管理）。 */
async function ensureConversation(): Promise<string | null> {
  if (conversations.currentId) return conversations.currentId
  const conv = await conversations.createAndOpen('新对话')
  return conv?.conversation_id ?? null
}

async function sendLlm(): Promise<void> {
  const content = text.value.trim()
  if (!content) return
  submitError.value = null
  try {
    const cid = await ensureConversation()
    if (!cid) {
      submitError.value = '无法创建对话'
      return
    }
    text.value = ''
    await chat.send(cid, 'llm', {
      text: content,
      ...(reasoningEffort.value
        ? { reasoning_effort: reasoningEffort.value }
        : {}),
    })
  } catch (err) {
    submitError.value = describeError(err)
  }
}

async function sendOffline(payload: {
  tool: string
  params: Record<string, unknown>
}): Promise<void> {
  submitError.value = null
  try {
    const cid = await ensureConversation()
    if (!cid) {
      submitError.value = '无法创建对话'
      offlineForm.value?.failSubmit('无法创建对话')
      return
    }
    // offline 请求体只带 tool/params（sendByMode 会忽略 text）；这里附一段可读文本，
    // 让 chat store 为本地用户回合生成气泡（不进入后端请求体）。
    await chat.send(cid, 'offline', {
      tool: payload.tool,
      params: payload.params,
      text: `离线直调：${payload.tool}`,
    })
    // 提交成功：收起工具/参数表单，换成回执摘要（结果已作为助手回合出现在上方对话）。
    offlineForm.value?.completeSubmit()
  } catch (err) {
    submitError.value = describeError(err)
    offlineForm.value?.failSubmit(submitError.value ?? undefined)
  }
}

/** Enter 发送（Shift+Enter 换行）；仅在 LLM 主控下生效。 */
function onEnter(): void {
  if (mode.value === 'llm' && canSend.value) void sendLlm()
}

function onResultNavigate(tab: string): void {
  nav?.(tab)
}

/** 点对话里的产物超链接 → 新开（幂等）并激活该产物标签。 */
function onOpenArtifact(artifact: ArtifactRef): void {
  const cid = currentId.value
  if (cid) artifacts.openTab(cid, artifact.id)
  onResultNavigate(artifact.id)
}
</script>

<template>
  <div
    class="chat-view"
    :class="{ 'chat-view--hero': showHero }"
    data-testid="view-chat"
  >
    <div v-if="!showHero" ref="streamEl" class="chat-view__stream">
      <MessageList
        :messages="visibleMessages"
        :streaming-node-id="streamingNodeId"
        :artifacts="messageArtifacts"
        @open-artifact="onOpenArtifact"
      />
    </div>

    <div class="chat-view__composer-area">
      <div v-if="showHero" class="chat-view__hero">
        <h2 class="chat-view__welcome" data-testid="chat-welcome">
          你好，我能帮什么忙？
        </h2>
        <p class="chat-view__welcome-sub">
          向数据分析 Agent 发消息，或切到「离线直调」直接选择工具。
        </p>
      </div>

      <div class="chat-view__composer" data-testid="chat-composer">
        <div class="chat-view__field" @keydown.enter.exact.prevent="onEnter">
          <n-input
            v-if="mode === 'llm'"
            v-model:value="text"
            class="chat-view__textarea"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 10 }"
            :bordered="false"
            placeholder="给数据分析 Agent 发消息…"
            data-testid="chat-input"
          />
          <OfflineToolForm v-else ref="offlineForm" @submit="sendOffline" />
        </div>

        <div class="chat-view__bar">
          <div class="chat-view__controls">
            <n-radio-group
              v-model:value="mode"
              size="small"
              data-testid="chat-mode"
            >
              <n-radio-button value="llm" data-testid="chat-mode-llm">
                LLM 主控
              </n-radio-button>
              <n-radio-button value="offline" data-testid="chat-mode-offline">
                离线直调
              </n-radio-button>
            </n-radio-group>

            <div
              v-if="mode === 'llm'"
              class="chat-view__reasoning"
              data-testid="chat-reasoning"
            >
              <span class="chat-view__reasoning-label">思考档位</span>
              <n-radio-group v-model:value="reasoning" size="small">
                <n-radio-button value="default" data-testid="chat-reasoning-default">
                  默认
                </n-radio-button>
                <n-radio-button value="low" data-testid="chat-reasoning-low">
                  低
                </n-radio-button>
                <n-radio-button value="medium" data-testid="chat-reasoning-medium">
                  中
                </n-radio-button>
                <n-radio-button value="high" data-testid="chat-reasoning-high">
                  高
                </n-radio-button>
              </n-radio-group>
            </div>
            <n-tag
              v-if="!hasConversation"
              size="small"
              :bordered="false"
              data-testid="chat-no-conversation"
            >
              发送时将自动新建对话
            </n-tag>
            <n-tag
              v-if="llmUnconfigured"
              size="small"
              type="warning"
              :bordered="false"
              data-testid="chat-llm-unconfigured"
            >
              未配置大模型，将用后端默认（可在「设置」里配置）
            </n-tag>
          </div>

          <n-button
            v-if="mode === 'llm'"
            class="chat-view__send"
            type="primary"
            circle
            :disabled="!canSend"
            :loading="chat.sending"
            aria-label="发送"
            data-testid="chat-send"
            @click="sendLlm"
          >
            <template #icon>
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M12 19V5" />
                <path d="M5 12l7-7 7 7" />
              </svg>
            </template>
          </n-button>
        </div>
      </div>

      <p v-if="errorText" class="chat-view__error" data-testid="chat-error">
        {{ errorText }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--ml-surface, #ffffff);
}
.chat-view--hero {
  justify-content: center;
  align-items: center;
}
.chat-view__stream {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
.chat-view__composer-area {
  flex: 0 0 auto;
  box-sizing: border-box;
  width: 100%;
  max-width: 780px;
  margin: 0 auto;
  padding: 16px 24px 24px;
}
.chat-view--hero .chat-view__composer-area {
  padding-top: 0;
}
.chat-view__hero {
  margin-bottom: 28px;
  text-align: center;
}
.chat-view__welcome {
  margin: 0 0 10px;
  color: var(--n-text-color-1, #111827);
  font-size: var(--ml-text-3xl, 28px);
  font-weight: 600;
  letter-spacing: -0.01em;
}
.chat-view__welcome-sub {
  margin: 0;
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-md, 16px);
}

/* 统一输入卡片：大圆角、浅边框、柔和阴影；聚焦时给出克制的强调环。 */
.chat-view__composer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: var(--ml-radius-2xl, 20px);
  background: var(--ml-surface, #ffffff);
  box-shadow: var(--ml-shadow-card);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.chat-view__composer:focus-within {
  border-color: rgba(47, 111, 237, 0.45);
  box-shadow: var(--ml-shadow-card), 0 0 0 3px rgba(47, 111, 237, 0.12);
}
.chat-view__field {
  min-width: 0;
}

/* 无边框 textarea：随内容长高，字号/行高与正文一致。 */
.chat-view__textarea :deep(.n-input__border),
.chat-view__textarea :deep(.n-input__state-border) {
  display: none;
}
.chat-view__textarea :deep(.n-input-wrapper) {
  padding: 0;
}
.chat-view__textarea :deep(.n-input__textarea-el) {
  padding: 0;
  color: var(--n-text-color-1, #111827);
  font-size: var(--ml-text-md, 16px);
  line-height: var(--ml-leading, 1.7);
}
.chat-view__textarea :deep(.n-input__placeholder) {
  font-size: var(--ml-text-md, 16px);
}

.chat-view__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.chat-view__controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.chat-view__send {
  flex: 0 0 auto;
}
.chat-view__reasoning {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.chat-view__reasoning-label {
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-xs, 12px);
}
.chat-view__error {
  margin: 10px 2px 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--ml-text-sm, 14px);
}
</style>
