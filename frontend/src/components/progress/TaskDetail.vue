<script setup lang="ts">
// FH.1 详情 + FH.3 终态取结果。
// - 详情：双编号、名称、概括、中文状态、阶段、进度、预计时长、更新时间。
// - 取结果：终态后经 useTasksStore().loadResult → getTaskResult（§3.9），结果写入
//   tasks.latestResult，供阶段 E 结果标签消费；若有中栏跳转契约（useResultTabNav），
//   给出各数据块直达按钮，完成「任务 → 结果」的衔接。
import { computed, ref } from 'vue'
import { NButton, NTag } from 'naive-ui'
import type { TaskView } from '@/api/types'
import { useTasksStore } from '@/stores/tasks'
import { useResultTabNav } from '@/composables/useResultTabNav'
import { presentResultBlocks } from '@/components/chat/resultBlocks'
import { formatTimestamp } from '@/components/resources/resourceUtils'
import {
  clampPercent,
  formatDuration,
  hasEstimate,
  isTerminal,
  statusLabel,
  statusTagType,
} from './taskDisplay'

const props = defineProps<{
  task: TaskView
  conversationId: string | null
}>()

const tasks = useTasksStore()
const nav = useResultTabNav()

const terminal = computed(() => isTerminal(props.task.status))
const label = computed(() => statusLabel(props.task))
const tagType = computed(() => statusTagType(props.task.status))
const percent = computed(() => clampPercent(props.task.progress_percent))
const showEstimate = computed(() => hasEstimate(props.task))
const estimate = computed(() => formatDuration(props.task.estimated_seconds))

const result = computed(() => tasks.getResult(props.task.agent_task_id))
const loaded = computed(() => result.value !== null)
const blocks = computed(() => presentResultBlocks(result.value?.blocks))

const loading = ref(false)

async function loadResult(): Promise<void> {
  if (!props.conversationId) return
  loading.value = true
  try {
    await tasks.loadResult(props.conversationId, props.task.agent_task_id)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="task-detail" data-testid="task-detail">
    <div class="task-detail__head">
      <span class="task-detail__name" data-testid="detail-name">
        {{ task.name || task.agent_task_id }}
      </span>
      <n-tag
        size="small"
        :type="tagType"
        :bordered="false"
        data-testid="detail-status"
      >
        {{ label }}
      </n-tag>
    </div>

    <dl class="task-detail__fields">
      <div>
        <dt>Agent 编号</dt>
        <dd data-testid="detail-agent-id">{{ task.agent_task_id }}</dd>
      </div>
      <div>
        <dt>Hadoop 编号</dt>
        <dd data-testid="detail-hadoop-id">{{ task.hadoop_task_id || '—' }}</dd>
      </div>
      <div>
        <dt>概括</dt>
        <dd data-testid="detail-summary">{{ task.summary || '—' }}</dd>
      </div>
      <div>
        <dt>阶段</dt>
        <dd data-testid="detail-stage">{{ task.stage || '—' }}</dd>
      </div>
      <div>
        <dt>进度</dt>
        <dd data-testid="detail-percent">{{ percent }}%</dd>
      </div>
      <div v-if="showEstimate">
        <dt>预计时长</dt>
        <dd data-testid="detail-estimate">约 {{ estimate }}</dd>
      </div>
      <div>
        <dt>更新时间</dt>
        <dd data-testid="detail-updated">{{ formatTimestamp(task.updated_at) }}</dd>
      </div>
    </dl>

    <div class="task-detail__result">
      <template v-if="terminal">
        <n-button
          size="tiny"
          type="primary"
          :loading="loading"
          :disabled="!conversationId"
          data-testid="task-load-result"
          @click="loadResult"
        >
          取结果
        </n-button>

        <div v-if="loaded" class="task-detail__blocks" data-testid="task-result-blocks">
          <span class="task-detail__hint">结果已载入：</span>
          <template v-for="block in blocks" :key="block.key">
            <n-button
              v-if="nav"
              size="tiny"
              secondary
              :data-testid="`task-result-link-${block.key}`"
              @click="nav?.(block.tab)"
            >
              {{ block.label }}
            </n-button>
            <n-tag
              v-else
              size="small"
              :bordered="false"
              :data-testid="`task-result-link-${block.key}`"
            >
              {{ block.label }}
            </n-tag>
          </template>
          <span v-if="!blocks.length" class="task-detail__hint">
            该结果无可展示的数据块。
          </span>
        </div>
      </template>

      <p v-else class="task-detail__hint" data-testid="task-result-pending">
        任务未结束，暂无结果。
      </p>
    </div>
  </section>
</template>

<style scoped>
.task-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 4px;
  padding: 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
  background: var(--n-color-modal, #fafafc);
}
.task-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.task-detail__name {
  font-size: 14px;
  font-weight: 600;
}
.task-detail__fields {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
}
.task-detail__fields div {
  display: flex;
  gap: 8px;
  font-size: 13px;
}
.task-detail__fields dt {
  flex: 0 0 auto;
  width: 68px;
  color: var(--n-text-color-3, #999);
}
.task-detail__fields dd {
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  text-overflow: ellipsis;
  word-break: break-all;
}
.task-detail__result {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}
.task-detail__blocks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.task-detail__hint {
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}
</style>
