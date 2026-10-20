<script setup lang="ts">
// 新前端 · 右栏：只读上下文摘要。
// 内容：当前任务 / 版本与时间边界 / 最近产物 / 结果入口。
// 只读：不出现上传/删除/模型管理。可收起。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .rightrail
import { computed } from 'vue'
import type { ResultPayload, TaskView } from '@/api/types'
import type { ResultTabKey } from '@/components/shellConfig'

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

/** 数据版本：优先结果里的 data_version。 */
const dataVersion = computed(() => {
  const blocks = props.result?.blocks as { data_version?: string } | undefined
  return blocks?.data_version || null
})

/** 时间边界 T1/T2：从结果的 blocks.score_comparison 或局限性里取；无则占位。 */
const t1 = computed(() => {
  const blocks = props.result?.blocks as { t1?: string } | undefined
  return blocks?.t1 || null
})
const t2 = computed(() => {
  const blocks = props.result?.blocks as { t2?: string } | undefined
  return blocks?.t2 || null
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
      <div class="rp-row"><span>数据版本</span><b>{{ dataVersion || '—' }}</b></div>
      <div class="rp-row"><span>清洗规则</span><b>{{ result ? '1.0.0' : '—' }}</b></div>
      <div class="rp-row"><span>评分方案</span><b>{{ result ? '1.0.0' : '—' }}</b></div>
      <div class="rp-row"><span>T1 训练期</span><b>{{ t1 || '—' }}</b></div>
      <div class="rp-row"><span>T2 验证期</span><b>{{ t2 || '—' }}</b></div>
    </div>

    <!-- 结果视图：点开后在中栏以标签页查看（与左栏的产物查看互不重复） -->
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
