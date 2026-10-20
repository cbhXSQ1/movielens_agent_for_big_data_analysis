<script setup lang="ts">
// 阶段 E · FE.2：评分前后对比的纯 SVG 分组条形图（不引入任何图表库）。
// 每个维度一行，清洗前 / 清洗后两条横条并列；满分按 100 归一。
import { computed } from 'vue'

interface ScoreBarRow {
  label: string
  before: number
  after: number
}

const props = defineProps<{
  rows: ScoreBarRow[]
  max?: number
}>()

const LABEL_W = 104
const VALUE_W = 48
const TRACK_W = 360
const ROW_H = 46
const BAR_H = 13
const GAP = 4
const TOP = 20
const BOTTOM = 24
const WIDTH = LABEL_W + TRACK_W + VALUE_W

const max = computed(() => {
  const m = props.max ?? 100
  return Number.isFinite(m) && m > 0 ? m : 100
})

const height = computed(() => TOP + props.rows.length * ROW_H + BOTTOM)

function widthOf(value: number): number {
  const ratio = Number.isFinite(value) ? value / max.value : 0
  return Math.max(0, Math.min(1, ratio)) * TRACK_W
}

const bars = computed(() =>
  props.rows.map((row, index) => {
    const y = TOP + index * ROW_H
    const beforeW = widthOf(row.before)
    const afterW = widthOf(row.after)
    return {
      label: row.label,
      labelY: y + BAR_H + GAP / 2 + 4,
      beforeY: y,
      afterY: y + BAR_H + GAP,
      beforeW,
      afterW,
      beforeText: row.before.toFixed(2),
      afterText: row.after.toFixed(2),
    }
  }),
)
</script>

<template>
  <svg
    class="score-bar-chart"
    :viewBox="`0 0 ${WIDTH} ${height}`"
    role="img"
    aria-label="清洗前后评分对比"
    data-testid="score-bar-chart"
  >
    <g v-for="bar in bars" :key="bar.label">
      <text
        class="score-bar-chart__label"
        :x="LABEL_W - 8"
        :y="bar.labelY"
        text-anchor="end"
      >
        {{ bar.label }}
      </text>

      <rect
        class="score-bar-chart__track"
        :x="LABEL_W"
        :y="bar.beforeY"
        :width="TRACK_W"
        :height="BAR_H"
        rx="3"
      />
      <rect
        class="score-bar-chart__track"
        :x="LABEL_W"
        :y="bar.afterY"
        :width="TRACK_W"
        :height="BAR_H"
        rx="3"
      />

      <rect
        class="score-bar-chart__bar score-bar-chart__bar--before"
        :x="LABEL_W"
        :y="bar.beforeY"
        :width="bar.beforeW"
        :height="BAR_H"
        rx="3"
      />
      <rect
        class="score-bar-chart__bar score-bar-chart__bar--after"
        :x="LABEL_W"
        :y="bar.afterY"
        :width="bar.afterW"
        :height="BAR_H"
        rx="3"
      />

      <text
        class="score-bar-chart__value"
        :x="LABEL_W + TRACK_W + 8"
        :y="bar.beforeY + BAR_H - 2"
      >
        {{ bar.beforeText }}
      </text>
      <text
        class="score-bar-chart__value"
        :x="LABEL_W + TRACK_W + 8"
        :y="bar.afterY + BAR_H - 2"
      >
        {{ bar.afterText }}
      </text>
    </g>

    <g class="score-bar-chart__legend" :transform="`translate(${LABEL_W}, ${height - 8})`">
      <rect class="score-bar-chart__bar--before" x="0" y="-9" width="12" height="9" rx="2" />
      <text x="18" y="-1">清洗前</text>
      <rect class="score-bar-chart__bar--after" x="72" y="-9" width="12" height="9" rx="2" />
      <text x="90" y="-1">清洗后</text>
    </g>
  </svg>
</template>

<style scoped>
.score-bar-chart {
  width: 100%;
  height: auto;
}
.score-bar-chart__label {
  font-size: var(--fs-md);
  fill: var(--n-text-color-2, #666);
}
.score-bar-chart__value {
  font-size: var(--fs-sm);
  fill: var(--n-text-color-3, #999);
}
.score-bar-chart__track {
  fill: var(--n-border-color, #efeff5);
}
.score-bar-chart__bar--before {
  fill: #8ba7d6;
}
.score-bar-chart__bar--after {
  fill: #4a7bd0;
}
.score-bar-chart__legend text {
  font-size: var(--fs-sm);
  fill: var(--n-text-color-2, #666);
}
</style>
