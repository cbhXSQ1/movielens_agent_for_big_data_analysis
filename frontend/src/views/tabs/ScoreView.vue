<script setup lang="ts">
// 五维评分对比（真实数据）。
// 数据来自 `result_payload.blocks`：score_comparison（五维前后 + 综合）、data_volume（数据量）、
// versions / time_boundaries（版本与时间边界）。无结果时如实显示空态，不摆示例数字。
import { computed } from 'vue'
import ScoreBars, { type ScoreDim } from '@/components/ScoreBars.vue'
import {
  useResultBlocks,
  timeBoundaryRows,
  versionRows,
} from '@/composables/useResultBlocks'
import { useTasksStore } from '@/stores/tasks'
import type { NamedCount } from '@/components/results/blocks'

const tasks = useTasksStore()
const { result, scoreComparison, dataVolume, versions, timeBoundaries } =
  useResultBlocks(() => tasks.currentResult)

const COLORS: Array<ScoreDim['color']> = ['blue', 'purple', 'mint', 'blue', 'pink', 'purple']

const dims = computed<ScoreDim[]>(() =>
  (scoreComparison.value?.rows ?? [])
    .filter((row) => row.key !== 'composite')
    .map((row, index) => ({
      name: row.label,
      color: COLORS[index % COLORS.length],
      before: row.before,
      after: row.after,
    })),
)

const composite = computed(
  () => scoreComparison.value?.rows.find((row) => row.key === 'composite') ?? null,
)

const hasScore = computed(() => (scoreComparison.value?.rows.length ?? 0) > 0)

const TABLES: Array<{ key: string; label: string; inputKey: string; outputKey: string }> = [
  { key: 'ratings', label: '评分 ratings', inputKey: 'ratings_lines', outputKey: 'ratings' },
  { key: 'users', label: '用户 users', inputKey: 'users_lines', outputKey: 'users' },
  { key: 'movies', label: '电影 movies', inputKey: 'movies_lines', outputKey: 'movies' },
]

function valueOf(list: NamedCount[], key: string): number | null {
  return list.find((item) => item.key === key)?.value ?? null
}

const dataRows = computed(() =>
  TABLES.map((table) => {
    const before = valueOf(dataVolume.value?.input ?? [], table.inputKey)
    const after = valueOf(dataVolume.value?.output ?? [], table.outputKey)
    return {
      key: table.key,
      label: table.label,
      before,
      after,
      change: before !== null && after !== null ? after - before : null,
    }
  }),
)

const hasVolume = computed(() =>
  dataRows.value.some((row) => row.before !== null || row.after !== null),
)

const verRows = computed(() => versionRows(versions.value))
const tbRows = computed(() => timeBoundaryRows(timeBoundaries.value))

function sumCounts(list: NamedCount[] | undefined): number | null {
  if (!list || list.length === 0) return null
  return list.reduce((sum, item) => sum + item.value, 0)
}

const dedupeTotal = computed(() => sumCounts(dataVolume.value?.dedupe))
const fixTotal = computed(() => sumCounts(dataVolume.value?.fix))

function fmtInt(value: number | null): string {
  return value === null ? '—' : value.toLocaleString('en-US')
}

function fmtChange(value: number | null): string {
  if (value === null) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  return sign + Math.abs(value).toLocaleString('en-US')
}

function fmtScore(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}
</script>

<template>
  <section class="view active" data-view="score" data-testid="view-score">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>五维评分对比</h2>
          <div class="panel-sub">同一份评分方案 · 数据来自最近一次任务结果</div>
        </div>
      </div>

      <p v-if="!result" class="panel-sub" data-testid="score-empty">
        暂无结果。先在对话里发起一次清洗 / 评分任务，结果会显示在这里。
      </p>

      <template v-else>
        <div v-if="hasScore" class="card">
          <div class="score-composite">
            <span class="before">{{ composite ? fmtScore(composite.before) : '—' }}</span>
            <span style="color: var(--text-4)">→</span>
            <span class="after">{{ composite ? fmtScore(composite.after) : '—' }}</span>
            <span v-if="composite" class="delta">
              综合分 {{ composite.delta >= 0 ? '+' : '−' }}{{ fmtScore(Math.abs(composite.delta)) }}
            </span>
          </div>
          <ScoreBars :dims="dims" />
        </div>
        <p v-else class="panel-sub" data-testid="score-no-score">该结果没有五维评分板块。</p>

        <div v-if="hasVolume" class="card">
          <h3>数据量变化</h3>
          <table class="data">
            <thead>
              <tr>
                <th>表</th>
                <th>清洗前</th>
                <th>清洗后</th>
                <th>变化</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in dataRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td>{{ fmtInt(row.before) }}</td>
                <td>{{ fmtInt(row.after) }}</td>
                <td>{{ fmtChange(row.change) }}</td>
              </tr>
            </tbody>
          </table>
          <div class="notice">
            隔离 {{ fmtInt(dataVolume?.quarantineTotal ?? null) }} 行 ·
            去重 {{ fmtInt(dedupeTotal) }} ·
            修复 {{ fmtInt(fixTotal) }}
            —— 三项独立统计；隔离 ≠ 修复。
          </div>
        </div>

        <div v-if="verRows.length || tbRows.length" class="card">
          <h3>版本与时间边界</h3>
          <table class="data">
            <tbody>
              <tr v-for="row in verRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td>{{ row.value || '—' }}</td>
              </tr>
              <tr v-for="row in tbRows" :key="row.key">
                <td>{{ row.label }}</td>
                <td>{{ row.value || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
  </section>
</template>
