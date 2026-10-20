<script setup lang="ts">
// 新前端 · 中央顶栏（只覆盖中栏，不再全宽）。
// 结构：当前对话标题 + 数据版本胶囊 + 状态标签 + 后端连接 + 右栏开关 + 设置。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .topbar
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import ConnectionStatus from './ConnectionStatus.vue'
import { useConversationsStore } from '@/stores/conversations'
import { versionRows } from '@/composables/useResultBlocks'
import type { ResultPayload, TaskView } from '@/api/types'

const props = defineProps<{
  task: TaskView | null
  result: ResultPayload | null
}>()

const emit = defineEmits<{
  (e: 'open-settings'): void
  (e: 'toggle-right'): void
  (e: 'status-click'): void
}>()

const conversations = useConversationsStore()
const { current } = storeToRefs(conversations)

const title = computed(() => current.value?.title || '新对话')

/** 数据版本：取最近结果的 blocks.versions.data；无则占位。 */
const dataVersion = computed(() => {
  const row = versionRows(props.result?.blocks?.versions).find(
    (item) => item.key === 'data',
  )
  return row?.value || null
})

/** 状态标签：running/succeeded/failed 都显示；无任务隐藏。 */
const status = computed(() => props.task?.status ?? null)
const hasStatus = computed(() => Boolean(props.task) && status.value !== null)

const statusClass = computed(() => {
  switch (status.value) {
    case 'running':
    case 'queued':
      return 'warn'
    case 'succeeded':
      return 'ok'
    case 'failed':
    case 'aborted':
      return 'danger'
    default:
      return ''
  }
})

/** 紧凑文案。 */
const compactText = computed(() => {
  if (!props.task) return ''
  const t = props.task
  if (t.status === 'running') {
    return `运行中 · ${t.stage || '处理中'} ${t.progress_percent || 0}%`
  }
  if (t.status === 'queued') return '排队中'
  if (t.status === 'succeeded') return `已完成 · ${t.progress_percent || 100}%`
  if (t.status === 'failed' || t.status === 'aborted') {
    const reason = t.summary || t.stage || '失败'
    return `失败 · ${reason}`
  }
  return t.status_cn || t.status
})

/** 悬停完整文案（仅截断时设置 title）。 */
const fullText = computed(() => {
  if (!props.task) return ''
  const t = props.task
  if (t.status === 'running') {
    return `运行中 · ${t.stage || ''} ${t.progress_percent || 0}% · ${t.summary || ''}`
  }
  if (t.status === 'succeeded') {
    return `${t.summary || '完成'} · ${t.progress_percent || 100}% · 已完成`
  }
  if (t.status === 'failed' || t.status === 'aborted') {
    return `任务在「${t.stage || ''}」阶段失败 · ${t.summary || ''} · 编号 ${t.agent_task_id}`
  }
  return compactText.value
})

const chipRef = ref<HTMLButtonElement | null>(null)
const txtRef = ref<HTMLSpanElement | null>(null)
const truncated = ref(false)

function refreshTruncated(): void {
  const el = txtRef.value
  if (!el) {
    truncated.value = false
    return
  }
  truncated.value = el.scrollWidth > el.clientWidth + 1
}

watch([compactText, hasStatus], () => {
  nextTick(refreshTruncated)
})

onMounted(() => {
  refreshTruncated()
  window.addEventListener('resize', refreshTruncated)
})

function onRightToggle(): void {
  emit('toggle-right')
}

function onSettings(): void {
  emit('open-settings')
}

function onStatus(): void {
  emit('status-click')
}
</script>

<template>
  <header class="topbar" data-testid="top-bar">
    <div class="topbar-title">
      <span class="title" :title="title" data-testid="topbar-title">{{ title }}</span>
      <span class="chip" data-testid="data-version-chip">
        数据版本 {{ dataVersion || '—' }}
      </span>
      <button
        v-if="hasStatus"
        ref="chipRef"
        type="button"
        class="status-chip"
        :class="statusClass"
        :title="truncated ? fullText : undefined"
        data-testid="status-chip"
        @click="onStatus"
      >
        <span class="status-dot" aria-hidden="true"></span>
        <span ref="txtRef" class="txt">{{ compactText }}</span>
      </button>
    </div>
    <div class="top-actions">
      <ConnectionStatus />
      <button
        type="button"
        class="icon-btn"
        title="收起右栏"
        aria-label="收起右栏"
        data-testid="right-toggle"
        @click="onRightToggle"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M15 4v16" />
        </svg>
      </button>
      <button
        type="button"
        class="icon-btn"
        title="设置"
        aria-label="设置"
        data-testid="settings-entry"
        @click="onSettings"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.14.37.36.7.66.96.3.26.68.4 1.07.44H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z" />
        </svg>
      </button>
    </div>
  </header>
</template>
