<script setup lang="ts">
// FE-ML-1 · 降维面板（契约 §3.4）。
// comparison（降维前/后同一聚类的 silhouette/davies_bouldin/k 与两套分组一致性 ari/nmi）、
// 解释方差比（NMF 为 null，如实说明"不适用"）、重构误差、资源占用；
// 并说明散点坐标来自 `role=coords` 产物（本轮产物只下载、不内联）。
import { computed } from 'vue'
import { formatInt, formatNumber, normalizeDimReductionMetrics } from './mlMetrics'

const props = defineProps<{ metrics: unknown }>()

const m = computed(() => normalizeDimReductionMetrics(props.metrics))

const comparison = computed(() => m.value?.comparison ?? null)

const explained = computed(() => m.value?.explained_variance_ratio ?? null)

const resource = computed(() => {
  const r = m.value?.resource
  if (!r) return []
  return [
    { key: 'fit_seconds', label: '训练耗时（秒）', value: formatNumber(r.fit_seconds, 2) },
    { key: 'mem_mb', label: '内存（MB）', value: formatNumber(r.mem_mb, 2) },
  ].filter((row) => row.value !== '')
})

const nComponents = computed(() => formatInt(m.value?.n_components))
const reconstruction = computed(() =>
  formatNumber(m.value?.reconstruction_error, 4),
)
</script>

<template>
  <section v-if="m" class="ml-card" data-testid="ml-dim-reduction-panel">
    <h4 class="ml-card__title">降维结果</h4>

    <p v-if="nComponents" class="ml-card__line">
      维度：{{ nComponents }}
    </p>

    <div v-if="comparison" class="ml-compare" data-testid="ml-comparison">
      <span class="ml-card__subtitle">聚类结构对比（降维前 / 后）</span>
      <ul class="ml-metrics">
        <li class="ml-metrics__item" data-testid="ml-comparison-before">
          <span class="ml-metrics__label">降维前</span>
          <span class="ml-metrics__value">
            轮廓系数 {{ formatNumber(comparison.before?.silhouette, 4) }} ·
            戴维斯-布尔丁 {{ formatNumber(comparison.before?.davies_bouldin, 4) }} ·
            k {{ formatInt(comparison.before?.k) }}
          </span>
        </li>
        <li class="ml-metrics__item" data-testid="ml-comparison-after">
          <span class="ml-metrics__label">降维后</span>
          <span class="ml-metrics__value">
            轮廓系数 {{ formatNumber(comparison.after?.silhouette, 4) }} ·
            戴维斯-布尔丁 {{ formatNumber(comparison.after?.davies_bouldin, 4) }} ·
            k {{ formatInt(comparison.after?.k) }}
          </span>
        </li>
        <li class="ml-metrics__item" data-testid="ml-comparison-agreement">
          <span class="ml-metrics__label">分组一致性</span>
          <span class="ml-metrics__value">
            调整兰德指数 {{ formatNumber(comparison.agreement?.ari, 4) }} ·
            归一化互信息 {{ formatNumber(comparison.agreement?.nmi, 4) }}
          </span>
        </li>
      </ul>
    </div>

    <p class="ml-card__line" data-testid="ml-explained-variance">
      <template v-if="explained && explained.length">
        解释方差比：{{ explained.map((v) => formatNumber(v, 2)).join('、') }}
      </template>
      <template v-else>解释方差比：不适用（NMF 不提供该项）</template>
    </p>

    <p v-if="reconstruction" class="ml-card__line" data-testid="ml-reconstruction-error">
      重构误差：{{ reconstruction }}
    </p>

    <ul v-if="resource.length" class="ml-metrics" data-testid="ml-resource">
      <li
        v-for="row in resource"
        :key="row.key"
        class="ml-metrics__item"
        :data-testid="`ml-resource-${row.key}`"
      >
        <span class="ml-metrics__label">{{ row.label }}</span>
        <span class="ml-metrics__value">{{ row.value }}</span>
      </li>
    </ul>

    <p class="ml-card__note" data-testid="ml-coords-note">
      用户降维坐标来自 `role=coords` 产物（本轮只提供下载，可在产物区取用）。
    </p>
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
  font-size: 14px;
  font-weight: 600;
}
.ml-card__subtitle {
  color: var(--n-text-color-3, #999);
  font-size: 12px;
  font-weight: 600;
}
.ml-card__note {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}
.ml-card__line {
  margin: 0;
  font-size: 13px;
}
.ml-compare {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ml-metrics {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.ml-metrics__item {
  display: flex;
  gap: 8px;
  font-size: 13px;
}
.ml-metrics__label {
  flex: 0 0 auto;
  min-width: 72px;
  color: var(--n-text-color-3, #999);
}
.ml-metrics__value {
  font-variant-numeric: tabular-nums;
}
</style>
