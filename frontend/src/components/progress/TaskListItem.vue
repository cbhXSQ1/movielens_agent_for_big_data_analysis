<script setup lang="ts">
// FH.1 / FH.2 · 单个任务行：双编号（Agent + Hadoop）、名称、概括、中文状态、阶段、
// 进度条、预计时长、更新时间。进度条由 store 中的任务视图实时驱动（task_status 事件 →
// tasks.applyStatus → taskList 变化 → 本组件重渲染）；无预计时长如实不显示。
import { computed } from 'vue'
import { NProgress, NTag } from 'naive-ui'
import type { TaskView } from '@/api/types'
import { formatTimestamp } from '@/components/resources/resourceUtils'
import {
  abortedHint,
  activeHint,
  clampPercent,
  formatDuration,
  hasEstimate,
  statusLabel,
  statusTagType,
} from './taskDisplay'

const props = defineProps<{
  task: TaskView
  active?: boolean
}>()

const emit = defineEmits<{ (e: 'select'): void }>()

const label = computed(() => statusLabel(props.task))
const tagType = computed(() => statusTagType(props.task.status))
const percent = computed(() => clampPercent(props.task.progress_percent))
const showEstimate = computed(() => hasEstimate(props.task))
const estimate = computed(() => formatDuration(props.task.estimated_seconds))
const hadoopId = computed(() => props.task.hadoop_task_id || '—')
const runningHint = computed(() => activeHint(props.task.status))
const stoppedHint = computed(() => abortedHint(props.task.status))
</script>

<template>
  <li
    class="task-item"
    :class="{ 'task-item--active': active }"
    role="button"
    tabindex="0"
    :aria-pressed="Boolean(active)"
    :data-testid="`task-item-${task.agent_task_id}`"
    @click="emit('select')"
    @keydown.enter="emit('select')"
    @keydown.space.prevent="emit('select')"
  >
    <div class="task-item__top">
      <span class="task-item__name" :data-testid="`task-name-${task.agent_task_id}`">
        {{ task.name || task.agent_task_id }}
      </span>
      <n-tag
        size="small"
        :type="tagType"
        :bordered="false"
        :data-testid="`task-status-${task.agent_task_id}`"
      >
        {{ label }}
      </n-tag>
    </div>

    <p class="task-item__summary" :data-testid="`task-summary-${task.agent_task_id}`">
      {{ task.summary || '—' }}
    </p>

    <dl class="task-item__ids">
      <div>
        <dt>Agent</dt>
        <dd :data-testid="`task-agent-id-${task.agent_task_id}`">{{ task.agent_task_id }}</dd>
      </div>
      <div>
        <dt>Hadoop</dt>
        <dd :data-testid="`task-hadoop-id-${task.agent_task_id}`">{{ hadoopId }}</dd>
      </div>
    </dl>

    <n-progress
      type="line"
      :percentage="percent"
      :show-indicator="false"
      :height="6"
      :status="tagType"
    />

    <div class="task-item__meta">
      <span :data-testid="`task-percent-${task.agent_task_id}`">{{ percent }}%</span>
      <span v-if="task.stage" :data-testid="`task-stage-${task.agent_task_id}`">
        {{ task.stage }}
      </span>
      <span v-if="showEstimate" :data-testid="`task-estimate-${task.agent_task_id}`">
        约 {{ estimate }}
      </span>
      <span :data-testid="`task-updated-${task.agent_task_id}`">
        {{ formatTimestamp(task.updated_at) }}
      </span>
    </div>

    <p
      v-if="runningHint"
      class="task-item__hint"
      :data-testid="`task-running-hint-${task.agent_task_id}`"
    >
      {{ runningHint }}
    </p>
    <p
      v-else-if="stoppedHint"
      class="task-item__hint task-item__hint--stopped"
      :data-testid="`task-aborted-hint-${task.agent_task_id}`"
    >
      {{ stoppedHint }}
    </p>
  </li>
</template>

<style scoped>
.task-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s;
}
.task-item:hover {
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.task-item--active {
  border-color: var(--n-primary-color, #2f6fed);
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.1));
}
.task-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.task-item__name {
  overflow: hidden;
  font-size: var(--fs-md);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-item__summary {
  margin: 0;
  color: var(--n-text-color-2, #555);
  font-size: var(--fs-md);
  line-height: 1.5;
}
.task-item__ids {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 0;
}
.task-item__ids div {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}
.task-item__ids dt {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-xs);
}
.task-item__ids dd {
  overflow: hidden;
  max-width: 180px;
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.task-item__hint {
  margin: 0;
  color: var(--n-text-color-2, #555);
  font-size: var(--fs-sm);
}
.task-item__hint--stopped {
  color: var(--n-warning-color, #f0a020);
}
</style>
