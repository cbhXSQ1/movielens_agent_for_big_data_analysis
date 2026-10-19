<script setup lang="ts">
// 新前端 · 五维评分完整视图（中栏标签）。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 data-view="score"
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import ScoreBars from '@/components/ScoreBars.vue'
import {
  SCORE_DIMS,
  COMPOSITE_BEFORE,
  COMPOSITE_AFTER,
  DATA_ROWS,
  fmtInt,
  fmtChange,
} from '@/components/scoreData'
import { useTasksStore } from '@/stores/tasks'

const tasks = useTasksStore()
const { currentResult } = storeToRefs(tasks)

const before = computed(() => COMPOSITE_BEFORE)
const after = computed(() => COMPOSITE_AFTER)
const delta = computed(() => +(after.value - before.value).toFixed(2))

const dataVersion = computed(() => {
  const blocks = currentResult.value?.blocks as { data_version?: string } | undefined
  return blocks?.data_version || 'ml1m-clean-v1'
})
</script>

<template>
  <section class="view active" data-view="score" data-testid="view-score">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>五维评分对比</h2>
          <div class="panel-sub">同一份评分方案 · 综合分提升</div>
        </div>
        <div class="actions">
          <button class="btn sm" data-toast="已复制评分摘要">复制摘要</button>
        </div>
      </div>

      <div class="card">
        <div class="score-composite">
          <span class="before">{{ before.toFixed(2) }}</span>
          <span style="color: var(--text-4)">→</span>
          <span class="after">{{ after.toFixed(2) }}</span>
          <span class="delta">综合分 +{{ delta.toFixed(2) }}</span>
        </div>
        <ScoreBars :dims="SCORE_DIMS" />
      </div>

      <div class="card">
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
            <tr v-for="r in DATA_ROWS" :key="r.table">
              <td>{{ r.table }}</td>
              <td>{{ fmtInt(r.before) }}</td>
              <td>{{ fmtInt(r.after) }}</td>
              <td>{{ fmtChange(r.change) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="notice">
          <b>隔离 ≠ 修复。</b>本次隔离 100,830 行、去重 49,510 行、修复 13,503 行，三项独立统计；分数提升有一部分来自分母变化。
        </div>
      </div>

      <div class="card">
        <h3>版本与时间边界</h3>
        <table class="data">
          <tbody>
            <tr><td>数据版本</td><td>{{ dataVersion }}</td></tr>
            <tr><td>清洗规则</td><td>cleaning_rules 1.0.0</td></tr>
            <tr><td>评分方案</td><td>scoring_scheme 1.0.0</td></tr>
            <tr><td>训练期截止 T1</td><td>2001-12-31 23:59:59 UTC</td></tr>
            <tr><td>验证期截止 T2</td><td>2002-06-30 23:59:59 UTC</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
