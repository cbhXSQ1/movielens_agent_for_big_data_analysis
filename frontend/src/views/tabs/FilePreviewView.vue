<script setup lang="ts">
// 阶段 C · 中栏「文件预览」标签（资源标签 kind='file'）。
// 经 resources store 预览产出文件（组件不直接 fetch），整栏铺满中栏：
//   - md → Markdown 渲染；json → 美化 JSON；text → 纯文本；不可预览 → 提示改用下载。
// 下载地址走 resources.downloadUrl（Agent 中继，task_id 为 Hadoop 任务号）。
import { computed, ref, watch } from 'vue'
import { NButton, NSpin, NTag } from 'naive-ui'
import MarkdownView from '@/components/results/MarkdownView.vue'
import { useResourcesStore } from '@/stores/resources'

const props = defineProps<{ taskId: string; path: string }>()

const resources = useResourcesStore()

const loading = ref(false)
const error = ref('')
const previewable = ref(false)
const kind = ref('')
const truncated = ref(false)
const content = ref('')

const downloadUrl = computed(() =>
  props.taskId && props.path ? resources.downloadUrl(props.taskId, props.path) : '#',
)

const previewText = computed(() => {
  if (!previewable.value) return ''
  if (kind.value === 'json') {
    try {
      return JSON.stringify(JSON.parse(content.value), null, 2)
    } catch {
      return content.value
    }
  }
  return content.value
})

watch(
  [() => props.taskId, () => props.path],
  async ([taskId, path]) => {
    loading.value = false
    error.value = ''
    previewable.value = false
    kind.value = ''
    truncated.value = false
    content.value = ''
    if (!taskId || !path) {
      error.value = '缺少任务编号或路径，无法取内容'
      return
    }
    loading.value = true
    try {
      const res = await resources.preview(taskId, path)
      previewable.value = res.previewable !== false && typeof res.content === 'string'
      kind.value = String(res.kind ?? '')
      truncated.value = res.truncated === true
      content.value = typeof res.content === 'string' ? res.content : ''
    } catch (err) {
      error.value = err instanceof Error ? err.message : '预览失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <section class="file-preview-view" data-testid="file-preview-view">
    <header class="file-preview-view__head">
      <h3 class="file-preview-view__title" data-testid="file-preview-title">
        {{ path }}
      </h3>
      <n-tag v-if="kind" size="small" :bordered="false">{{ kind }}</n-tag>
      <n-tag
        v-if="truncated"
        size="small"
        type="warning"
        :bordered="false"
        data-testid="file-preview-truncated"
      >
        已截断
      </n-tag>
      <a
        class="file-preview-view__download"
        :href="downloadUrl"
        :download="path"
        data-testid="file-preview-download"
      >
        <n-button size="tiny" secondary>下载</n-button>
      </a>
    </header>

    <n-spin v-if="loading" data-testid="file-preview-loading" />

    <p
      v-else-if="error"
      class="file-preview-view__error"
      data-testid="file-preview-error"
    >
      {{ error }}
    </p>

    <p
      v-else-if="!previewable"
      class="file-preview-view__hint"
      data-testid="file-preview-unavailable"
    >
      该文件类型不支持在线预览。
    </p>

    <div
      v-else-if="kind === 'md'"
      data-testid="file-preview-content"
    >
      <MarkdownView :content="previewText" />
    </div>

    <pre
      v-else
      class="file-preview-view__code"
      data-testid="file-preview-content"
    >{{ previewText }}</pre>
  </section>
</template>

<style scoped>
.file-preview-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 16px;
  box-sizing: border-box;
  overflow: auto;
}
.file-preview-view__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.file-preview-view__title {
  margin: 0;
  font-size: var(--fs-lg);
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.file-preview-view__download {
  margin-left: auto;
  text-decoration: none;
}
.file-preview-view__code {
  margin: 0;
  padding: 12px;
  overflow: auto;
  border-radius: 8px;
  background: var(--n-code-color, #f6f7f9);
  font-size: var(--fs-md);
  white-space: pre-wrap;
  word-break: break-all;
}
.file-preview-view__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
}
.file-preview-view__error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--fs-md);
}
</style>
