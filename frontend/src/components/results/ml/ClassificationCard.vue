<script setup lang="ts">
// FE-ML-1 · 分类结果卡（契约 §3.2）。
// 比率类指标（F1/AUC/PR-AUC/P/R/阈值/正类比例）以**百分比**显示（×100，两位）；
// 混淆矩阵、特征重要性（含说明）、划分规模、防泄漏小字如实呈现。
// 只展示后端给出的项，缺失项不造数。
import { computed } from 'vue'
import { formatPercent } from '@/utils/format'
import { formatInt, normalizeClassificationMetrics, orDash } from './mlMetrics'

const props = defineProps<{ metrics: unknown }>()

const m = computed(() => normalizeClassificationMetrics(props.metrics))

const ratios = computed(() => {
  const data = m.value
  if (!data) return []
  return [
    { key: 'f1', label: 'F1', value: formatPercent(data.f1) },
    { key: 'auc', label: 'AUC', value: formatPercent(data.auc) },
    { key: 'pr_auc', label: 'PR-AUC', value: formatPercent(data.pr_auc) },
    { key: 'precision', label: '精确率', value: formatPercent(data.precision) },
    { key: 'recall', label: '召回率', value: formatPercent(data.recall) },
    { key: 'threshold', label: '判定阈值', value: formatPercent(data.threshold) },
    { key: 'positive_rate', label: '正类比例', value: formatPercent(data.positive_rate) },
  ].filter((row) => row.value !== '')
})

const confusion = computed(() => {
  const cm = m.value?.confusion_matrix
  if (!cm) return []
  return [
    { key: 'tp', label: '真正例 tp', value: formatInt(cm.tp) },
    { key: 'fp', label: '假正例 fp', value: formatInt(cm.fp) },
    { key: 'tn', label: '真负例 tn', value: formatInt(cm.tn) },
    { key: 'fn', label: '假负例 fn', value: formatInt(cm.fn) },
  ].filter((row) => row.value !== '')
})

const importance = computed(() => m.value?.feature_importance ?? [])
const importanceNote = computed(() => m.value?.feature_importance_note ?? '')

const split = computed(() => {
  const s = m.value?.split_sizes
  if (!s) return null
  return [
    { key: 'train', label: '训练', value: formatInt(s.train) },
    { key: 'valid', label: '验证', value: formatInt(s.valid) },
    { key: 'test', label: '测试', value: formatInt(s.test) },
  ].filter((row) => row.value !== '')
})

const leakage = computed(() => {
  const l = m.value?.leakage
  if (!l) return ''
  const parts: string[] = []
  if (l.policy) parts.push(`防泄漏做法：${l.policy}`)
  if (l.verified_on) parts.push(`核验方式：${l.verified_on}`)
  return parts.join('；')
})
</script>

<template>
  <section v-if="m" class="ml-card" data-testid="ml-classification-card">
    <h4 class="ml-card__title">分类结果</h4>

    <ul v-if="ratios.length" class="ml-metrics">
      <li
        v-for="row in ratios"
        :key="row.key"
        class="ml-metrics__item"
        :data-testid="`ml-metric-${row.key}`"
      >
        <span class="ml-metrics__label">{{ row.label }}</span>
        <span class="ml-metrics__value">{{ row.value }}</span>
      </li>
    </ul>

    <template v-if="confusion.length">
      <span class="ml-card__subtitle">混淆矩阵</span>
      <ul class="ml-metrics" data-testid="ml-confusion">
        <li
          v-for="row in confusion"
          :key="row.key"
          class="ml-metrics__item"
          :data-testid="`ml-confusion-${row.key}`"
        >
          <span class="ml-metrics__label">{{ row.label }}</span>
          <span class="ml-metrics__value">{{ orDash(row.value) }}</span>
        </li>
      </ul>
    </template>

    <template v-if="importance.length">
      <span class="ml-card__subtitle">特征重要性</span>
      <ul class="ml-list" data-testid="ml-feature-importance">
        <li
          v-for="item in importance"
          :key="item.feature"
          :data-testid="`ml-feature-${item.feature}`"
        >
          {{ item.feature }}：{{ item.importance.toFixed(4) }}
        </li>
      </ul>
    </template>
    <p
      v-if="importanceNote"
      class="ml-card__note"
      data-testid="ml-feature-importance-note"
    >
      {{ importanceNote }}
    </p>

    <p v-if="split" class="ml-card__line" data-testid="ml-split-sizes">
      数据划分：
      <span v-for="row in split" :key="row.key">
        {{ row.label }} {{ row.value }}
      </span>
    </p>

    <p v-if="leakage" class="ml-card__note" data-testid="ml-leakage">
      {{ leakage }}
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
.ml-card__line {
  margin: 0;
  font-size: var(--fs-md);
}
.ml-card__line span {
  margin-right: 12px;
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
.ml-list {
  margin: 0;
  padding-left: 18px;
  font-size: var(--fs-md);
}
</style>
