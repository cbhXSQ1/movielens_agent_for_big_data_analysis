<script setup lang="ts">
// FG.5 · 产出文件段：选任务后列出该任务产出文件（§3.11.4），支持下载与预览。
// 任务来源：tasks store（由 SSE/补拉填充）；预览走 resources.preview（json/md/text 有内容，
// 其余 previewable:false → 提示改用下载）；下载地址走 resources.downloadUrl。
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import { useConversationsStore } from '@/stores/conversations'
import { useResourcesStore } from '@/stores/resources'
import { useTasksStore } from '@/stores/tasks'
import { formatBytes } from '@/components/results/format'
import {
  categoryTagType,
  deriveCategoryFromPath,
} from '@/components/results/artifactCategory'
import MarkdownView from '@/components/results/MarkdownView.vue'
import type { TaskFile, TaskView } from '@/api/types'
import { describeError } from './resourceUtils'

const resources = useResourcesStore()
const tasks = useTasksStore()
const conversations = useConversationsStore()
const { taskList } = storeToRefs(tasks)

// 选中态用 agent_task_id 标识任务行；但所有产出文件接口要的是 **Hadoop 任务号**
// （后端按 `var/tasks/<hadoop_task_id>/` 定位目录），故实际调用一律走 hadoop_task_id。
const selected = ref('')

const selectedTask = computed<TaskView | null>(
  () => taskList.value.find((t) => t.agent_task_id === selected.value) ?? null,
)
const hadoopTaskId = computed(() => selectedTask.value?.hadoop_task_id ?? '')
const hasHadoopId = computed(() => hadoopTaskId.value !== '')

interface PreviewState {
  path: string
  loading: boolean
  error: string
  previewable: boolean
  kind: string
  content: string
  truncated: boolean
}
const preview = ref<PreviewState | null>(null)

const files = computed(() =>
  hasHadoopId.value ? resources.filesOf(hadoopTaskId.value) : [],
)
const filesLoading = computed(() => resources.loading.files)

function select(task: TaskView): void {
  selected.value = task.agent_task_id
  preview.value = null
  const hid = task.hadoop_task_id
  if (hid) void resources.loadTaskFiles(hid)
}

function refreshTasks(): void {
  const id = conversations.currentId
  if (id) void tasks.refresh(id)
}

async function openPreview(file: TaskFile): Promise<void> {
  const taskId = hadoopTaskId.value
  if (!taskId) return
  preview.value = {
    path: file.path,
    loading: true,
    error: '',
    previewable: true,
    kind: file.kind,
    content: '',
    truncated: false,
  }
  try {
    const res = await resources.preview(taskId, file.path)
    preview.value = {
      path: file.path,
      loading: false,
      error: '',
      previewable: res.previewable !== false && typeof res.content === 'string',
      kind: res.kind ?? file.kind,
      content: typeof res.content === 'string' ? res.content : '',
      truncated: res.truncated === true,
    }
  } catch (err) {
    preview.value = {
      path: file.path,
      loading: false,
      error: describeError(err),
      previewable: false,
      kind: file.kind,
      content: '',
      truncated: false,
    }
  }
}

const previewText = computed(() => {
  const p = preview.value
  if (!p || !p.previewable) return ''
  if (p.kind === 'json') {
    try {
      return JSON.stringify(JSON.parse(p.content), null, 2)
    } catch {
      return p.content
    }
  }
  return p.content
})

function downloadUrl(path: string): string {
  return hasHadoopId.value ? resources.downloadUrl(hadoopTaskId.value, path) : '#'
}
</script>

<template>
  <section class="resource-section" data-testid="resources-segment-files">
    <header class="resource-section__head">
      <h3 class="resource-section__title">产出文件</h3>
      <n-button size="tiny" quaternary data-testid="files-refresh-tasks" @click="refreshTasks">
        刷新任务
      </n-button>
    </header>

    <div class="files-tasks">
      <n-empty
        v-if="!taskList.length"
        description="暂无可选任务"
        data-testid="files-tasks-empty"
      />
      <button
        v-for="task in taskList"
        :key="task.agent_task_id"
        type="button"
        class="files-task"
        :class="{ 'files-task--active': task.agent_task_id === selected }"
        :data-testid="`files-task-${task.agent_task_id}`"
        @click="select(task)"
      >
        {{ task.name || task.agent_task_id }}
      </button>
    </div>

    <p
      v-if="selected && !hasHadoopId"
      class="resource-section__hint"
      data-testid="files-no-hadoop"
    >
      该任务尚无 Hadoop 任务号，暂无可查产物。
    </p>

    <template v-if="selected && hasHadoopId">
      <n-spin :show="filesLoading">
        <n-empty
          v-if="!filesLoading && !files.length"
          description="本任务暂无产出文件"
          data-testid="files-empty"
        />
        <ul v-else class="resource-list" data-testid="files-list">
          <li
            v-for="file in files"
            :key="file.path"
            class="resource-card resource-card--row"
            :data-testid="`file-row-${file.path}`"
          >
            <span class="resource-card__name" :title="file.path">{{ file.path }}</span>
            <n-tag
              size="small"
              :bordered="false"
              :type="categoryTagType(deriveCategoryFromPath(file.path))"
              :data-testid="`file-category-${file.path}`"
            >
              {{ deriveCategoryFromPath(file.path) }}
            </n-tag>
            <n-tag size="small" :bordered="false">{{ file.kind }}</n-tag>
            <span class="resource-card__size">{{ formatBytes(file.size) }}</span>
            <n-button
              size="tiny"
              secondary
              :data-testid="`file-preview-btn-${file.path}`"
              @click="openPreview(file)"
            >
              预览
            </n-button>
            <a
              class="files-download"
              :href="downloadUrl(file.path)"
              :download="file.path"
              :data-testid="`file-download-${file.path}`"
            >
              下载
            </a>
          </li>
        </ul>
      </n-spin>

      <p v-if="resources.errors.files" class="resource-error" data-testid="files-error">
        {{ resources.errors.files }}
      </p>

      <div v-if="preview" class="preview" data-testid="file-preview-panel">
        <div class="preview__head">
          <span class="resource-card__name">{{ preview.path }}</span>
          <n-tag v-if="preview.kind" size="small" :bordered="false">{{ preview.kind }}</n-tag>
          <n-tag v-if="preview.truncated" size="small" type="warning" :bordered="false">
            已截断
          </n-tag>
        </div>
        <p v-if="preview.loading" class="resource-section__hint">预览加载中…</p>
        <p v-else-if="preview.error" class="resource-error" data-testid="file-preview-error">
          {{ preview.error }}
        </p>
        <p
          v-else-if="!preview.previewable"
          class="resource-section__hint"
          data-testid="file-preview-unavailable"
        >
          该文件类型不支持就地预览，请使用「下载」获取。
        </p>
        <div v-else-if="preview.kind === 'md'" data-testid="file-preview-content">
          <MarkdownView :content="previewText" />
        </div>
        <pre v-else class="preview__code" data-testid="file-preview-content">{{ previewText }}</pre>
      </div>
    </template>
  </section>
</template>

<style scoped>
.resource-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
}
.resource-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.resource-section__title {
  margin: 0;
  font-size: 15px;
}
.resource-section__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: 13px;
}
.files-tasks {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.files-task {
  padding: 6px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.files-task:hover {
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.files-task--active {
  border-color: var(--n-primary-color, #2f6fed);
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.1));
}
.resource-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.resource-card {
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.resource-card--row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.resource-card__name {
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__size {
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}
.files-download {
  font-size: 13px;
  text-decoration: none;
}
.preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.preview__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.preview__code {
  max-height: 300px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: 13px;
}
</style>
