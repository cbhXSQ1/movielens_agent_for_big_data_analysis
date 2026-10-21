<script setup lang="ts">
// 新前端 · 五维分段彩条（实色前值 + 渐变增量）。
// 供对话内结果摘要卡与「五维评分」完整视图共用，避免两套画法。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .dim / .bar
export interface ScoreDim {
  name: string
  /** 维度色名：blue/purple/mint/pink。 */
  color: 'blue' | 'purple' | 'mint' | 'pink'
  /** 清洗前值（0-100）。 */
  before: number
  /** 清洗后值（0-100）。 */
  after: number
}

const props = withDefaults(
  defineProps<{
    dims: ScoreDim[]
    /** 紧凑模式（摘要卡用窄列；完整视图用宽列）。 */
    compact?: boolean
  }>(),
  { compact: false },
)

function delta(d: ScoreDim): number {
  return Math.max(0, +(d.after - d.before).toFixed(2))
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

function title(d: ScoreDim): string {
  return `${fmt(d.before)} → ${fmt(d.after)}（+${fmt(delta(d))}）`
}
</script>

<template>
  <div class="bar-legend">
    <span><i class="lg-base"></i>清洗前</span>
    <span><i class="lg-grow"></i>提升</span>
  </div>
  <div
    v-for="d in props.dims"
    :key="d.name"
    :class="['dim-row', `c-${d.color}`, { dim: props.compact }]"
  >
    <span :class="props.compact ? 'name' : 'dim-name'">{{ d.name }}</span>
    <div :class="props.compact ? 'bar' : 'bar-track'" :title="title(d)">
      <i class="base" :style="{ width: `${d.before}%` }"></i>
      <i class="grow" :style="{ width: `${delta(d)}%` }"></i>
    </div>
    <span :class="props.compact ? 'val' : 'dim-val'">
      {{ fmt(d.before) }} → {{ fmt(d.after) }}
    </span>
    <span :class="props.compact ? 'delta' : 'dim-delta'">+{{ fmt(delta(d)) }}</span>
  </div>
</template>
