<script setup lang="ts">
// 新前端 · 中栏「对话」标签。
// 输入区结构按原型：模式/档位在输入卡片外，卡片内只有 textarea + 渐变发送键；
// 离线直调时思考胶囊置灰、发送键隐藏、卡片内换工具表单。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .composer-wrap
import { computed, nextTick, ref, watch } from 'vue'
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
const offlineForm = ref<InstanceType<typeof OfflineToolForm> | null>(null)

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
const thinkPopOpen = ref(false)

const REASONING_LABEL: Record<ReasoningLevel, string> = {
  default: '默认',
  low: '低',
  medium: '中',
  high: '高',
}

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
const llmUnconfigured = computed(() => mode.value === 'llm' && !settings.hasOverride)
const showHero = computed(() => visibleMessages.value.length === 0)
const canSend = computed(() => text.value.trim().length > 0 && !chat.sending)
const errorText = computed(() => {
  if (submitError.value) return submitError.value
  if (lastError.value) return `${lastError.value.code}：${lastError.value.message}`
  return null
})

const QUICK_CHIPS = [
  '跑到哪一步了',
  '给我看 5 条被隔离的评分记录',
  '为什么这么评分？',
  '已发布的数据版本有哪些',
]

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
    await chat.send(cid, 'offline', {
      tool: payload.tool,
      params: payload.params,
      text: `离线直调：${payload.tool}`,
    })
    offlineForm.value?.completeSubmit()
  } catch (err) {
    submitError.value = describeError(err)
    offlineForm.value?.failSubmit(submitError.value ?? undefined)
  }
}

function onEnter(): void {
  if (mode.value === 'llm' && canSend.value) void sendLlm()
}

function onResultNavigate(tab: string): void {
  nav?.(tab)
}

function onOpenArtifact(artifact: ArtifactRef): void {
  const cid = currentId.value
  if (cid) artifacts.openTab(cid, artifact.id)
  onResultNavigate(artifact.id)
}

function selectMode(next: AgentMode): void {
  mode.value = next
  if (next === 'offline') {
    thinkPopOpen.value = false
    nextTick(() => offlineForm.value?.$el?.focus?.())
  }
}

function selectReasoning(level: ReasoningLevel): void {
  reasoning.value = level
  thinkPopOpen.value = false
}

function onChip(chipText: string): void {
  const el = document.querySelector<HTMLTextAreaElement>('#chatInput')
  if (el) {
    el.value = chipText
    el.focus()
  }
  // 同步到 ref
  text.value = chipText
}

function onInputGrow(event: Event): void {
  const el = event.target as HTMLTextAreaElement
  text.value = el.value
  el.style.height = 'auto'
  el.style.height = Math.min(130, el.scrollHeight) + 'px'
}
</script>

<template>
  <div
    class="chat-view"
    :class="{ 'chat-view--hero': showHero }"
    data-testid="view-chat"
  >
    <div v-if="!showHero" ref="streamEl" class="chat-scroll">
      <div class="chat-col">
        <MessageList
          :messages="visibleMessages"
          :streaming-node-id="streamingNodeId"
          :artifacts="messageArtifacts"
          @open-artifact="onOpenArtifact"
        />
      </div>
    </div>

    <div class="composer-wrap">
      <div v-if="showHero" class="chat-view__hero">
        <h2 class="chat-view__welcome" data-testid="chat-welcome">
          你好，我能帮什么忙？
        </h2>
        <p class="chat-view__welcome-sub">
          向数据分析 Agent 发消息，或切到「离线直调」直接选择工具。
        </p>
      </div>

      <div class="composer-tools">
        <div class="seg" data-testid="chat-mode">
          <button
            type="button"
            :class="{ active: mode === 'llm' }"
            data-testid="chat-mode-llm"
            @click="selectMode('llm')"
          >
            <svg class="mini" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />
            </svg>
            LLM 主控
          </button>
          <button
            type="button"
            :class="{ active: mode === 'offline' }"
            data-testid="chat-mode-offline"
            @click="selectMode('offline')"
          >
            <svg class="mini" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0V8ZM12 17v5" />
            </svg>
            离线直调
          </button>
        </div>
        <div class="think-wrap" data-testid="chat-reasoning">
          <button
            type="button"
            class="think-pill"
            :class="{ dimmed: mode !== 'llm' }"
            :aria-expanded="thinkPopOpen"
            aria-haspopup="listbox"
            data-testid="chat-reasoning-pill"
            @click="mode === 'llm' && (thinkPopOpen = !thinkPopOpen)"
          >
            <span class="lv-bars" aria-hidden="true"><i></i><i></i><i></i></span>
            思考档位 <b>{{ REASONING_LABEL[reasoning] }}</b>
          </button>
          <div class="think-pop" :class="{ show: thinkPopOpen }" role="listbox">
            <div
              class="opt"
              :class="{ on: reasoning === 'default' }"
              role="option"
              :aria-selected="reasoning === 'default'"
              data-testid="chat-reasoning-default"
              @click="selectReasoning('default')"
            >
              <span class="tick">✓</span>
              <div><b>默认</b><small>后端缺省行为</small></div>
            </div>
            <div
              class="opt"
              :class="{ on: reasoning === 'low' }"
              role="option"
              :aria-selected="reasoning === 'low'"
              data-testid="chat-reasoning-low"
              @click="selectReasoning('low')"
            >
              <span class="tick">✓</span>
              <div><b>低 · 快</b><small>单步任务、简单查询，最快返回</small></div>
            </div>
            <div
              class="opt"
              :class="{ on: reasoning === 'medium' }"
              role="option"
              :aria-selected="reasoning === 'medium'"
              data-testid="chat-reasoning-medium"
              @click="selectReasoning('medium')"
            >
              <span class="tick">✓</span>
              <div><b>中 · 均衡</b><small>常规分析与多工具编排</small></div>
            </div>
            <div
              class="opt"
              :class="{ on: reasoning === 'high' }"
              role="option"
              :aria-selected="reasoning === 'high'"
              data-testid="chat-reasoning-high"
              @click="selectReasoning('high')"
            >
              <span class="tick">✓</span>
              <div><b>高 · 深度</b><small>复杂推理与结果解读，耗时更长</small></div>
            </div>
          </div>
        </div>
      </div>

      <div class="chips">
        <button
          v-for="chip in QUICK_CHIPS"
          :key="chip"
          type="button"
          class="chip"
          @click="onChip(chip)"
        >
          {{ chip }}
        </button>
      </div>

      <div class="composer" data-testid="chat-composer">
        <div v-if="mode === 'llm'" class="llm-composer">
          <textarea
            id="chatInput"
            v-model="text"
            rows="1"
            placeholder="对 Agent 说点什么…（Ctrl + Enter 发送）"
            data-testid="chat-input"
            @keydown.enter.ctrl.exact.prevent="onEnter"
            @keydown.enter.meta.exact.prevent="onEnter"
            @input="onInputGrow"
          />
        </div>
        <OfflineToolForm
          v-else
          ref="offlineForm"
          data-testid="offline-tool-form"
          @submit="sendOffline"
        />
        <button
          v-if="mode === 'llm'"
          type="button"
          class="send"
          :disabled="!canSend"
          :data-loading="chat.sending"
          aria-label="发送"
          data-testid="chat-send"
          @click="sendLlm"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5" />
            <path d="M5 12l7-7 7 7" />
          </svg>
        </button>
      </div>

      <p v-if="llmUnconfigured" class="hint" data-testid="chat-llm-unconfigured">
        未配置大模型，将用后端默认（可在「设置」里配置）
      </p>
      <p v-if="!hasConversation" class="hint" data-testid="chat-no-conversation">
        发送时将自动新建对话 · Ctrl + Enter 发送
      </p>
      <p v-else class="hint">Ctrl + Enter 发送 · 结果来自真实 Agent</p>

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
}
.chat-view--hero {
  justify-content: center;
}
.chat-view__hero {
  text-align: center;
  margin-bottom: 12px;
}
.chat-view__welcome {
  margin: 0 0 10px;
  font-size: var(--fs-xl);
  font-weight: 600;
}
.chat-view__welcome-sub {
  margin: 0;
  font-size: var(--fs-md);
  color: var(--text-3);
}
.chat-view__error {
  max-width: 720px;
  margin: 9px auto 0;
  color: var(--danger);
  font-size: var(--fs-xs);
}
</style>
