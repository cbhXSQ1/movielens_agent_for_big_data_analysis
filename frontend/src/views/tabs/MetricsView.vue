<script setup lang="ts">
// metrics.json（真实数据）：直接展示最近一次任务结果的 blocks 原文（只读）。
// 无结果时如实说明；不再内置示例 JSON。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'

const tasks = useTasksStore()
const { currentResult } = storeToRefs(tasks)

const jsonText = computed(() => {
  const blocks = currentResult.value?.blocks
  if (!blocks) return ''
  try {
    return JSON.stringify(blocks, null, 2)
  } catch {
    return ''
  }
})

const hasData = computed(() => jsonText.value.length > 0)

async function copyJson(): Promise<void> {
  try {
    await navigator.clipboard.writeText(jsonText.value)
  } catch {
    /* 剪贴板不可用时静默 */
  }
}
</script>

<template>
  <section class="view active" data-view="metrics" data-testid="view-metrics">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>metrics.json</h2>
          <div class="panel-sub">最近一次任务结果的结构化板块（只读原文）</div>
        </div>
        <div class="actions">
          <button v-if="hasData" class="btn sm" data-testid="metrics-copy" @click="copyJson">
            复制 JSON
          </button>
        </div>
      </div>

      <p v-if="!currentResult" class="panel-sub" data-testid="metrics-empty">
        暂无结果。先在对话里发起一次清洗 / 评分任务。
      </p>
      <div v-else class="card">
        <pre class="json">{{ jsonText }}</pre>
      </div>
    </div>
  </section>
</template>
