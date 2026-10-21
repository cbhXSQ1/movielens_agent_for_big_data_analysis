<script setup lang="ts">
// 右栏：只读上下文摘要（真实数据）。
// 当前任务 / 版本与时间边界（取自结果）/ 最近产物（artifacts store）/ 结果视图入口。
// 不承担上传、删除、模型管理；没有数据时如实显示"暂无"，不摆示例数字。
import { computed } from 'vue'
import type { ArtifactRef, ResultPayload, TaskView } from '@/api/types'
import type { ResultTabKey } from '@/components/shellConfig'
import {
  useResultBlocks,
  timeBoundaryRows,
  versionRows,
} from '@/composables/useResultBlocks'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { fileTab, schemeTab, schemeFileName } from '@/components/centerTabViews'

const props = defineProps<{
  task: TaskView | null
  result: ResultPayload | null
  latest: ResultPayload | null
  collapsed: boolean
}>()

const emit = defineEmits<{
  (e: 'open-result', key: ResultTabKey): void
  (e: 'collapse'): void
}>()

const artifacts = useArtifactsStore()
const conversations = useConversationsStore()
const centerTabs = useCenterTabsStore()

const { versions, timeBoundaries } = useResultBlocks(
  () => props.result ?? props.latest ?? null,
)

const verRows = computed(() => versionRows(versions.value))
const tbRows = computed(() => timeBoundaryRows(timeBoundaries.value))
const hasBoundaries = computed(() => verRows.value.length > 0 || tbRows.value.length > 0)

const recentArtifacts = computed<ArtifactRef[]>(() => {
  const cid = conversations.currentId
  if (!cid) return []
  return artifacts.list(cid).slice(0, 6)
})

const statusZh = computed(() => {
  const s = props.task?.status
  if (!s) return ''
  const map: Record<string, string> = {
    queued: '排队中',
    running: '进行中',
    succeeded: '已完成',
    failed: '失败',
    aborted: '已中止',
  }
  return map[s] ?? s
})

const statusTagClass = computed(() => {
  const s = props.task?.status
  if (s === 'succeeded') return 'ok'
  if (s === 'running' || s === 'queued') return 'blue'
  if (s === 'failed' || s === 'aborted') return 'danger'
  return ''
})

const progressPercent = computed(() => props.task?.progress_percent ?? 0)

const stageText = computed(() => {
  if (!props.task) return ''
  return `${props.task.stage || '—'} · ${progressPercent.value}%`
})

const durationText = computed(() => {
  const sec = props.task?.estimated_seconds
  if (!sec || sec <= 0) return '—'
  if (sec < 60) return `${Math.round(sec)} 秒`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m} 分 ${s} 秒`
})

const gridEntries: ResultTabKey[] = ['score', 'report', 'metrics', 'basis', 'ml', 'kg']

const gridLabels: Record<string, string> = {
  score: '五维评分',
  report: '评估报告',
  metrics: '指标 JSON',
  basis: '方案依据',
  ml: '迭代二模型',
  kg: '知识图谱',
}

function openResult(key: ResultTabKey): void {
  emit('open-result', key)
}

function openArtifact(artifact: ArtifactRef): void {
  if (artifact.source === 'scheme' && artifact.scheme_name) {
    const name = schemeFileName(artifact.scheme_name)
    centerTabs.open(schemeTab(name, artifact.title || name))
    return
  }
  if (artifact.task_id && artifact.path) {
    centerTabs.open(fileTab(artifact.task_id, artifact.path, artifact.title || artifact.path))
  }
}

function onCollapse(): void {
  emit('collapse')
}
</script>

<template>
  <aside class="rightrail" aria-label="上下文摘要" data-testid="right-rail">
    <div class="right-head">
      <b>上下文</b>
      <span class="sub">只读摘要</span>
      <button
        type="button"
        class="collapse"
        title="收起右栏"
        aria-label="收起右栏"
        data-testid="right-collapse"
        @click="onCollapse"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 18l6-6-6-6" />
        </svg>
      </button>
    </div>

    <!-- 当前任务 -->
    <div class="right-section">
      <div class="right-title">当前任务</div>
      <div v-if="task" class="rp-card">
        <div class="rp-top">
          <span class="rp-id">{{ task.agent_task_id }}</span>
          <span class="tag" :class="statusTagClass">{{ statusZh }}</span>
        </div>
        <p class="rp-name">{{ task.name || task.summary || '—' }}</p>
        <div class="rp-bar"><i :style="{ width: `${progressPercent}%` }"></i></div>
        <div class="rp-meta">
          <span>{{ stageText }}</span>
          <span>{{ durationText }}</span>
        </div>
      </div>
      <div v-else class="rp-card">
        <p class="rp-name" style="text-align: center; color: var(--text-4)">暂无任务</p>
      </div>
    </div>

    <!-- 版本与时间边界 -->
    <div class="right-section">
      <div class="right-title">版本与时间边界</div>
      <template v-if="hasBoundaries">
        <div v-for="row in verRows" :key="row.key" class="rp-row">
          <span>{{ row.label }}</span><b>{{ row.value || '—' }}</b>
        </div>
        <div v-for="row in tbRows" :key="row.key" class="rp-row">
          <span>{{ row.label }}</span><b>{{ row.value || '—' }}</b>
        </div>
      </template>
      <p v-else class="right-caption" data-testid="right-no-boundaries">暂无（先跑一次任务）</p>
    </div>

    <!-- 最近产物（真实 artifacts） -->
    <div class="right-section">
      <div class="right-title">最近产物</div>
      <template v-if="recentArtifacts.length">
        <button
          v-for="item in recentArtifacts"
          :key="item.id"
          type="button"
          class="rp-link"
          :title="item.path || item.title"
          @click="openArtifact(item)"
        >
          <span class="ic">{{ item.kind || '·' }}</span>
          {{ item.title || item.id }}
        </button>
      </template>
      <p v-else class="right-caption" data-testid="right-no-artifacts">暂无产物</p>
    </div>

    <!-- 结果视图 -->
    <div class="right-section">
      <div class="right-title">结果视图</div>
      <p class="right-caption">点开后在中栏新开标签页查看</p>
      <div class="rp-grid">
        <button
          v-for="key in gridEntries"
          :key="key"
          type="button"
          @click="openResult(key)"
        >
          {{ gridLabels[key] }}
        </button>
      </div>
    </div>

    <div class="rp-foot">只读摘要 · 上传、删除、模型管理等完整功能在左栏。</div>
  </aside>
</template>
