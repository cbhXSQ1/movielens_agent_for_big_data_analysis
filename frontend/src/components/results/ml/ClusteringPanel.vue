<script setup lang="ts">
// FE-ML-1 · 聚类面板（契约 §3.3）。
// 逐群展示 cluster_id / size / label / top_genres / behavior；当后端返回
// `k_selection`（未传 k 时选定簇数的过程）时，另给候选 k 与轮廓系数的折线。
import { computed } from 'vue'
import { formatInt, formatNumber, normalizeClusteringMetrics } from './mlMetrics'

const props = defineProps<{ metrics: unknown }>()

const m = computed(() => normalizeClusteringMetrics(props.metrics))
const clusters = computed(() => m.value?.clusters ?? [])

const summary = computed(() => {
  const data = m.value
  if (!data) return []
  return [
    { key: 'k', label: '簇数 k', value: formatInt(data.k) },
    { key: 'silhouette', label: '轮廓系数', value: formatNumber(data.silhouette, 4) },
    {
      key: 'davies_bouldin',
      label: '戴维斯-布尔丁',
      value: formatNumber(data.davies_bouldin, 4),
    },
  ].filter((row) => row.value !== '')
})

const kSelection = computed(() => m.value?.k_selection ?? null)

/** 候选点折线坐标：轮廓系数归一到 [0,1]，等距铺开。 */
const kSelectionLine = computed(() => {
  const candidateList = (kSelection.value?.candidates ?? []).filter(
    (c) => c.silhouette !== null && c.silhouette !== undefined,
  )
  const width = 240
  const height = 60
  const pad = 6
  if (candidateList.length === 0) return { width, height, points: '' }
  const step =
    candidateList.length > 1 ? (width - pad * 2) / (candidateList.length - 1) : 0
  const points = candidateList
    .map((c, index) => {
      const value = Math.max(0, Math.min(1, c.silhouette as number))
      const x = pad + index * step
      const y = height - pad - value * (height - pad * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  return { width, height, points }
})

function behaviorText(behavior: Record<string, unknown> | undefined | null): string {
  if (!behavior) return ''
  const parts: string[] = []
  const activity = formatNumber(behavior.activity as never, 2)
  const avgRating = formatNumber(behavior.avg_rating as never, 2)
  const ratingStd = formatNumber(behavior.rating_std as never, 2)
  const recency = formatNumber(behavior.recency as never, 2)
  if (activity !== '') parts.push(`活跃度 ${activity}`)
  if (avgRating !== '') parts.push(`均分 ${avgRating}`)
  if (ratingStd !== '') parts.push(`评分标准差 ${ratingStd}`)
  if (recency !== '') parts.push(`新近度 ${recency}`)
  return parts.join(' · ')
}
</script>

<template>
  <section v-if="m" class="ml-card" data-testid="ml-clustering-panel">
    <h4 class="ml-card__title">聚类结果</h4>

    <ul v-if="summary.length" class="ml-metrics">
      <li
        v-for="row in summary"
        :key="row.key"
        class="ml-metrics__item"
        :data-testid="`ml-cluster-summary-${row.key}`"
      >
        <span class="ml-metrics__label">{{ row.label }}</span>
        <span class="ml-metrics__value">{{ row.value }}</span>
      </li>
    </ul>

    <table v-if="clusters.length" class="ml-table">
      <thead>
        <tr>
          <th>群体</th>
          <th>规模</th>
          <th>标签</th>
          <th>主要类型</th>
          <th>行为特征</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="cluster in clusters"
          :key="cluster.cluster_id ?? cluster.label ?? ''"
          :data-testid="`ml-cluster-${cluster.cluster_id}`"
        >
          <td>{{ cluster.cluster_id }}</td>
          <td>{{ cluster.size }}</td>
          <td>{{ cluster.label }}</td>
          <td>{{ (cluster.top_genres ?? []).join('、') }}</td>
          <td>{{ behaviorText(cluster.behavior) }}</td>
        </tr>
      </tbody>
    </table>

    <template v-if="kSelection">
      <span class="ml-card__subtitle">簇数选择（k 与轮廓系数）</span>
      <div class="ml-kselect" data-testid="ml-k-selection">
        <svg
          v-if="kSelectionLine.points"
          class="ml-kselect__chart"
          :viewBox="`0 0 ${kSelectionLine.width} ${kSelectionLine.height}`"
          role="img"
          aria-label="候选簇数与轮廓系数折线"
        >
          <polyline class="ml-kselect__line" :points="kSelectionLine.points" />
        </svg>
        <ul class="ml-list">
          <li
            v-for="item in kSelection.candidates ?? []"
            :key="item.k ?? ''"
            :data-testid="`ml-k-selection-candidate-${item.k}`"
          >
            k={{ item.k }} · 轮廓系数 {{ formatNumber(item.silhouette, 4) }}
          </li>
        </ul>
      </div>
      <p v-if="kSelection.method" class="ml-card__note">
        选择方法：{{ kSelection.method }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.ml-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ml-card__title {
  margin: 0;
  font-size: var(--fs-md);
  font-weight: 600;
}
.ml-card__subtitle {
  margin-top: 4px;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
  font-weight: 600;
}
.ml-card__note {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.ml-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.ml-metrics__item {
  display: flex;
  gap: 6px;
  font-size: var(--fs-md);
}
.ml-metrics__label {
  color: var(--n-text-color-3, #999);
}
.ml-metrics__value {
  font-variant-numeric: tabular-nums;
}
.ml-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-md);
}
.ml-table th,
.ml-table td {
  padding: 4px 8px;
  border-bottom: 1px solid var(--n-border-color, #efeff5);
  text-align: left;
}
.ml-table th {
  color: var(--n-text-color-3, #999);
  font-weight: 600;
}
.ml-kselect {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ml-kselect__chart {
  flex: 0 0 auto;
  width: 240px;
  height: 60px;
}
.ml-kselect__line {
  fill: none;
  stroke: #4a7bd0;
  stroke-width: 2;
}
.ml-list {
  margin: 0;
  padding-left: 18px;
  font-size: var(--fs-md);
}
</style>
