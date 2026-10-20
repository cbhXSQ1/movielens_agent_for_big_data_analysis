<script setup lang="ts">
// 阶段 H（fe-progress）· FH.1–FH.3：左栏④任务进度面板。
// - FH.1 列表/详情：列当前对话的任务；双编号（Agent + Hadoop）、名称、概括、中文状态、
//   阶段、更新时间；点开看详情。
// - FH.2 进度条：由 task_status 事件实时驱动（store.upsert → taskList 变化 → 重渲染）；
//   progress_percent / 状态 / 预计时长实时反映，无预计时长如实不显示。
// - FH.3 刷新补拉：连接就绪、切换对话或手动刷新时用 listTasks 补拉恢复；终态后由详情取结果。
// 组件不直接 fetch，一律经 useTasksStore（上下文来自 conversations / connection store）。
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin } from 'naive-ui'
import { useConnectionStore } from '@/stores/connection'
import { useConversationsStore } from '@/stores/conversations'
import { useTasksStore } from '@/stores/tasks'
import TaskListItem from '@/components/progress/TaskListItem.vue'
import TaskDetail from '@/components/progress/TaskDetail.vue'

const connection = useConnectionStore()
const conversations = useConversationsStore()
const tasks = useTasksStore()
const { taskList } = storeToRefs(tasks)

const selectedId = ref<string | null>(null)

const selectedTask = computed(() =>
  selectedId.value ? tasks.get(selectedId.value) : null,
)

/** FH.3 补拉：拉当前对话全部任务并 upsert（刷新/重连恢复）。 */
function refresh(): void {
  const id = conversations.currentId
  if (id) void tasks.refresh(id)
}

// 连接就绪或切换对话后自动补拉（页面刷新 / 重连恢复）。
const refreshKey = computed(
  () => `${connection.connected}|${conversations.currentId ?? ''}`,
)
watch(
  refreshKey,
  () => {
    if (connection.connected && conversations.currentId) refresh()
  },
  { immediate: true },
)

/** 点任务卡：开合详情，并同步「当前任务」选中态（结果标签随之切换，清单③）。 */
function select(id: string): void {
  const next = selectedId.value === id ? null : id
  selectedId.value = next
  tasks.select(next)
}
</script>

<template>
  <div class="tasks-panel" data-testid="panel-progress">
    <header class="tasks-panel__head">
      <h3 class="tasks-panel__title">任务进度</h3>
      <n-button
        size="tiny"
        quaternary
        :disabled="!conversations.currentId"
        data-testid="tasks-refresh"
        @click="refresh"
      >
        刷新
      </n-button>
    </header>

    <p
      v-if="!conversations.currentId"
      class="tasks-panel__hint"
      data-testid="tasks-no-conversation"
    >
      未选择对话。
    </p>

    <template v-else>
      <n-spin :show="tasks.loading">
        <n-empty
          v-if="!tasks.loading && !taskList.length"
          description="本对话暂无长任务"
          data-testid="tasks-empty"
        />
        <ul v-else class="tasks-panel__list" data-testid="tasks-list">
          <task-list-item
            v-for="task in taskList"
            :key="task.agent_task_id"
            :task="task"
            :active="task.agent_task_id === selectedId"
            @select="select(task.agent_task_id)"
          />
        </ul>
      </n-spin>

      <p v-if="tasks.lastError" class="tasks-panel__error" data-testid="tasks-error">
        {{ tasks.lastError }}
      </p>

      <task-detail
        v-if="selectedTask"
        :task="selectedTask"
        :conversation-id="conversations.currentId"
      />
    </template>
  </div>
</template>

<style scoped>
.tasks-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 16px;
  box-sizing: border-box;
}
.tasks-panel__head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
}
.tasks-panel__title {
  margin: 0;
  font-size: var(--ml-text-lg, 16px);
  font-weight: 600;
}
.tasks-panel__hint {
  margin: 0;
  color: var(--n-text-color-3, #8a94a6);
  font-size: var(--ml-text-sm, 13px);
}
.tasks-panel__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.tasks-panel__error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--ml-text-sm, 13px);
}
</style>
