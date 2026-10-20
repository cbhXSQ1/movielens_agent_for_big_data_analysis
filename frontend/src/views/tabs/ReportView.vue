<script setup lang="ts">
// 评估报告（真实数据）：展示结果里的 report Markdown；可复制、可按真实任务编号下载。
// 无报告时如实说明，不再内置示例报告。
import { computed } from 'vue'
import { useResultBlocks } from '@/composables/useResultBlocks'
import { fileDownloadUrl } from '@/api/resources'

const { result, report } = useResultBlocks()

const hasReport = computed(() => report.value.length > 0)
const hadoopTaskId = computed(() => result.value?.task?.hadoop_task_id ?? '')

const downloadHref = computed(() =>
  hadoopTaskId.value ? fileDownloadUrl(hadoopTaskId.value, 'report.md') : '',
)

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
            v-if="hasReport && downloadHref"
            class="btn sm"
            :href="downloadHref"
            download="report.md"
            data-testid="report-download"
          >
            下载 .md
          </a>
        </div>
      </div>

      <p v-if="!result" class="panel-sub" data-testid="report-empty">
        暂无结果。先在对话里发起一次清洗 / 评分任务。
      </p>
      <p v-else-if="!hasReport" class="panel-sub" data-testid="report-missing">
        该结果没有提供评估报告全文。
      </p>
      <div v-else class="card md">
        <pre style="white-space: pre-wrap">{{ report }}</pre>
      </div>
    </div>
  </section>
</template>
