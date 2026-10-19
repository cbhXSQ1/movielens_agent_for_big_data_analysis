<script setup lang="ts">
// 新前端 · metrics.json 视图（中栏标签）。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 data-view="metrics"
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'

const tasks = useTasksStore()
const { currentResult } = storeToRefs(tasks)

const jsonText = computed(() => {
  const blocks = currentResult.value?.blocks
  if (!blocks) return FALLBACK
  try {
    return JSON.stringify(blocks, null, 2)
  } catch {
    return FALLBACK
  }
})

const FALLBACK = `{
  "dimensions": {
    "Accurate":   { "before": 97.71, "after": 99.79 },
    "Complete":   { "before": 98.82, "after": 99.99 },
    "Unique":     { "before": 90.90, "after": 100.00 },
    "Up-to-date": { "before": 98.22, "after": 100.00 },
    "Consistent": { "before": 89.65, "after": 100.00 },
    "composite":  { "before": 95.07, "after": 99.95 }
  },
  "metrics": {
    "A1_评分值域合法率": 0.9998,
    "A2_时间戳可解析率": 0.9999,
    "A3_用户属性编码合法率": 0.9986,
    "A4_用户ID引用合法率": 0.9993,
    "A5_电影ID引用合法率": 0.9991,
    "C1_必填字段完整率": 1.0000,
    "C2_三表关联完整率": 0.9999,
    "C3_用户属性完整率": 0.9999,
    "U1_评分记录唯一率": 1.0000,
    "U2_用户记录唯一率": 1.0000,
    "U3_电影记录唯一率": 1.0000,
    "F1_时间戳范围合规率": 1.0000,
    "F2_时间边界覆盖率": 1.0000,
    "S1_跨表引用一致率": 1.0000,
    "S2_类型枚举一致率": 1.0000,
    "S3_数值格式一致率": 1.0000,
    "S4_评分与画像一致率": 1.0000,
    "S5_发布版本一致率": 1.0000
  },
  "counts": {
    "input":  { "ratings_lines": 1150241, "users_lines": 6946, "movies_lines": 4465 },
    "output": { "ratings": 1000209, "users": 6040, "movies": 3883 },
    "quarantine": { "total": 100830 },
    "dedupe": { "ratings": 49510, "users": 726, "movies": 454 },
    "fix": { "R4_ms": 13503, "P1_text": 48, "M2_strip": 47, "U3_zip_plus4": 73 }
  }
}`
</script>

<template>
  <section class="view active" data-view="metrics" data-testid="view-metrics">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>metrics.json</h2>
          <div class="panel-sub">18 项指标 · 清洗前 / 后 / 综合</div>
        </div>
        <div class="actions">
          <button class="btn sm" data-toast="已复制 JSON">复制 JSON</button>
          <button class="btn sm" data-toast="演示环境不产生真实下载">下载</button>
        </div>
      </div>
      <div class="card">
        <pre class="json">{{ jsonText }}</pre>
      </div>
    </div>
  </section>
</template>
