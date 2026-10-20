<script setup lang="ts">
// 评估报告（真实数据）：展示当前任务结果里的 report Markdown；可复制、可按真实产物下载。
// 报告块只有引用（无全文）时，若产物里有 report.md，则给出下载入口。
import { computed } from 'vue'
import { useTasksStore } from '@/stores/tasks'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import { useResultBlocks } from '@/composables/useResultBlocks'
import { fileDownloadUrl } from '@/api/resources'
import MarkdownView from '@/components/results/MarkdownView.vue'
import type { ArtifactRef } from '@/api/types'

const tasks = useTasksStore()
const artifacts = useArtifactsStore()
const conversations = useConversationsStore()

const { result, report } = useResultBlocks(() => tasks.currentResult)

const hasReport = computed(() => report.value.length > 0)

const reportArtifact = computed<ArtifactRef | null>(() => {
  const cid = conversations.currentId
  if (!cid) return null
  return (
    artifacts
      .list(cid)
      .find(
        (item) =>
          typeof item.path === 'string' &&
          item.path.toLowerCase().endsWith('report.md'),
      ) ?? null
  )
})

const downloadHref = computed(() => {
  const artifact = reportArtifact.value
  if (artifact?.task_id && artifact.path) {
    return fileDownloadUrl(artifact.task_id, artifact.path)
  }
  const hadoopTaskId = result.value?.task?.hadoop_task_id
  if (hadoopTaskId && hasReport.value) {
    return fileDownloadUrl(hadoopTaskId, 'report.md')
  }
  return ''
})

const hasDownload = computed(() => downloadHref.value.length > 0)

async function copyReport(): Promise<void> {
  try {
    await navigator.clipboard.writeText(report.value)
  } catch {
    /* 剪贴板不可用时静默 */
  }
}
</script>

<template>
  <section class="view active" data-view="report" data-testid="view-report">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>评估报告.md</h2>
          <div class="panel-sub">report.md · 由任务结果提供</div>
        </div>
        <div class="actions">
          <button v-if="hasReport" class="btn sm" data-testid="report-copy" @click="copyReport">
            复制
          </button>
          <a
            v-if="hasDownload"
            class="btn sm"
            :href="downloadHref"
            :download="reportArtifact?.path ?? 'report.md'"
            data-testid="report-download"
          >
            下载 .md
          </a>
        </div>
      </div>

      <p v-if="!result" class="panel-sub" data-testid="report-empty">
        暂无结果。先在对话里发起一次清洗 / 评分任务。
      </p>
      <template v-else-if="!hasReport">
        <p class="panel-sub" data-testid="report-missing">
          {{ reportArtifact ? '该结果的报告以文件形式提供，可下载查看。' : '该结果没有提供评估报告全文。' }}
        </p>
      </template>
      <div v-else class="card md">
        <MarkdownView :content="report" />
      </div>
    </div>
  </section>
</template>
