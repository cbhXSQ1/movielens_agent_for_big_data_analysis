<script setup lang="ts">
// metrics.json（真实数据）：展示当前任务结果的 blocks 原文（只读），
// 并给出当前任务真实 metrics 产物的下载入口；无产物时不摆下载按钮。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import { fileDownloadUrl } from '@/api/resources'
import type { ArtifactRef } from '@/api/types'

const tasks = useTasksStore()
const artifacts = useArtifactsStore()
const conversations = useConversationsStore()
const { currentResult, current } = storeToRefs(tasks)

const hasBlocks = computed(() => {
  const blocks = currentResult.value?.blocks
  if (!blocks) return false
  return Object.keys(blocks).length > 0
})

const jsonText = computed(() => {
  if (!hasBlocks.value) return ''
  try {
    return JSON.stringify(currentResult.value?.blocks, null, 2)
  } catch {
    return ''
  }
})

const hasData = computed(() => jsonText.value.length > 0)

const metricsArtifact = computed<ArtifactRef | null>(() => {
  const cid = conversations.currentId
  const task = current.value
  if (!cid || !task) return null
  const taskIds = [task.hadoop_task_id, task.agent_task_id].filter(
    (id): id is string => Boolean(id),
  )
  return (
    artifacts.list(cid).find((item) => {
      if (!item.task_id || !taskIds.includes(item.task_id)) return false
      return (
        typeof item.path === 'string' &&
        item.path.toLowerCase().includes('metrics')
      )
    }) ?? null
  )
})

const downloadHref = computed(() => {
  const artifact = metricsArtifact.value
  if (!artifact?.task_id || !artifact.path) return ''
  return fileDownloadUrl(artifact.task_id, artifact.path)
})

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
          <div class="panel-sub">当前任务结果的结构化板块（只读原文）</div>
        </div>
        <div class="actions">
          <button v-if="hasData" class="btn sm" data-testid="metrics-copy" @click="copyJson">
            复制 JSON
          </button>
          <a
            v-if="downloadHref"
            class="btn sm"
            :href="downloadHref"
            :download="metricsArtifact?.path ?? 'metrics.json'"
            data-testid="metrics-download"
          >
            下载
          </a>
        </div>
      </div>

      <p v-if="!hasData" class="panel-sub" data-testid="metrics-empty">
        {{ currentResult ? '该结果没有指标板块。' : '暂无结果。' }}
      </p>
      <div v-else class="card">
        <pre class="json">{{ jsonText }}</pre>
      </div>
    </div>
  </section>
</template>
