<script setup lang="ts">
// FG.5 · 产出文件段：选任务后列出该任务产出文件（§3.11.4），支持下载与预览。
// 任务来源：tasks store（由 SSE/补拉填充）；「预览」把文件作为中栏资源标签打开
// （内容与可否预览由中栏 FilePreviewView 经 resources.preview 判定）；下载地址走 resources.downloadUrl。
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { useConversationsStore } from '@/stores/conversations'
import { useResourcesStore } from '@/stores/resources'
import { useTasksStore } from '@/stores/tasks'
import { formatBytes } from '@/components/results/format'
import {
  categoryTagType,
  deriveCategoryFromPath,
} from '@/components/results/artifactCategory'
import { fileTab } from '@/components/centerTabViews'
import { statusLabel, statusTagType } from '@/components/progress/taskDisplay'
import type { TaskFile, TaskView } from '@/api/types'

const resources = useResourcesStore()
const tasks = useTasksStore()
const conversations = useConversationsStore()
const centerTabs = useCenterTabsStore()
const { taskList } = storeToRefs(tasks)

// 选中态用 agent_task_id 标识任务行；但所有产出文件接口要的是 **Hadoop 任务号**
// （后端按 `var/tasks/<hadoop_task_id>/` 定位目录），故实际调用一律走 hadoop_task_id。
const selected = ref('')

const selectedTask = computed<TaskView | null>(
  () => taskList.value.find((t) => t.agent_task_id === selected.value) ?? null,
)
const hadoopTaskId = computed(() => selectedTask.value?.hadoop_task_id ?? '')
const hasHadoopId = computed(() => hadoopTaskId.value !== '')

const files = computed(() =>
  hasHadoopId.value ? resources.filesOf(hadoopTaskId.value) : [],
)
const filesLoading = computed(() => resources.loading.files)

function select(task: TaskView): void {
  selected.value = task.agent_task_id
  const hid = task.hadoop_task_id
  if (hid) void resources.loadTaskFiles(hid)
}

function refreshTasks(): void {
  const id = conversations.currentId
  if (id) void tasks.refresh(id)
}

/** 「预览」：把该文件作为中栏资源标签打开（taskId 用 Hadoop 任务号）。 */
function openFile(file: TaskFile): void {
  const taskId = hadoopTaskId.value
  if (!taskId) return
  centerTabs.open(fileTab(taskId, file.path))
}

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
        <span class="files-task__top">
          <span class="files-task__name">{{ task.name || task.agent_task_id }}</span>
          <n-tag
            size="small"
            :type="statusTagType(task.status)"
            :bordered="false"
            :data-testid="`files-task-status-${task.agent_task_id}`"
          >
            {{ statusLabel(task) }}
          </n-tag>
        </span>
        <span class="files-task__ids">
          <span :data-testid="`files-task-agent-${task.agent_task_id}`">
            LT {{ task.agent_task_id }}
          </span>
          <span :data-testid="`files-task-hadoop-${task.agent_task_id}`">
            Hadoop {{ task.hadoop_task_id || '—' }}
          </span>
        </span>
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
            class="resource-card resource-card--stack"
            :data-testid="`file-row-${file.path}`"
          >
            <!-- 第一行：路径 + 大小 -->
            <div class="resource-card__row">
              <span class="resource-card__name" :title="file.path">{{ file.path }}</span>
              <span class="resource-card__size">{{ formatBytes(file.size) }}</span>
            </div>
            <!-- 第二行：类别 / 类型徽标 + 预览 / 下载 -->
            <div class="resource-card__meta">
              <n-tag
                size="small"
                :bordered="false"
                :type="categoryTagType(deriveCategoryFromPath(file.path))"
                :data-testid="`file-category-${file.path}`"
              >
                {{ deriveCategoryFromPath(file.path) }}
              </n-tag>
              <n-tag size="small" :bordered="false">{{ file.kind }}</n-tag>
              <div class="resource-card__actions">
                <n-button
                  size="tiny"
                  secondary
                  :data-testid="`file-preview-btn-${file.path}`"
                  @click="openFile(file)"
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
              </div>
            </div>
          </li>
        </ul>
      </n-spin>

      <p v-if="resources.errors.files" class="resource-error" data-testid="files-error">
        {{ resources.errors.files }}
      </p>
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
  font-size: var(--fs-lg);
}
.resource-section__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
}
.files-tasks {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.files-task {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-size: var(--fs-md);
  cursor: pointer;
}
.files-task:hover {
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.files-task--active {
  border-color: var(--n-primary-color, #2f6fed);
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.1));
}
.files-task__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.files-task__name {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.files-task__ids {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  color: var(--n-text-color-3, #999);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-xs);
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
.resource-card--stack {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}
.resource-card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.resource-card__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.resource-card__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.resource-card__name {
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__size {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.files-download {
  font-size: var(--fs-md);
  text-decoration: none;
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--fs-md);
}
</style>
