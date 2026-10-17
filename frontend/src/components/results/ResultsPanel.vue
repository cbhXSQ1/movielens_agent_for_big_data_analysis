<script setup lang="ts">
// 阶段 E · FE.1：结果标签的容器外壳。
// 统一每个结果视图的标题、当前任务徽标与空态；内容经默认插槽注入。
// `empty` 为真时显示空态插槽（缺省用 n-empty 的如实说明，不造数字）。
import { computed } from 'vue'
import { NEmpty, NTag } from 'naive-ui'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'
import { statusLabel } from '@/components/progress/taskDisplay'

const props = defineProps<{
  title: string
  testid: string
  empty?: boolean
  emptyHint?: string
}>()

const { latestResult } = storeToRefs(useTasksStore())
const task = computed(() => latestResult.value?.task ?? null)

const taskTitle = computed(() => {
  const t = task.value
  if (!t) return ''
  return t.name || t.agent_task_id
})

const statusText = computed(() => (task.value ? statusLabel(task.value) : ''))

const showEmpty = computed(() => props.empty === true)
</script>

<template>
  <section class="results-panel" :data-testid="testid">
    <header class="results-panel__head">
      <h3 class="results-panel__title">{{ title }}</h3>
      <n-tag
        v-if="task"
        size="small"
        :bordered="false"
        data-testid="results-task"
      >
        {{ taskTitle }}
        <template v-if="statusText"> · {{ statusText }}</template>
      </n-tag>
    </header>

    <div class="results-panel__body">
      <slot v-if="!showEmpty" />
      <slot v-else name="empty">
        <div class="results-panel__empty" :data-testid="`${testid}-empty`">
          <n-empty :description="emptyHint ?? '暂无数据'" />
        </div>
      </slot>
    </div>
  </section>
</template>

<style scoped>
.results-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 16px;
  box-sizing: border-box;
}
.results-panel__head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}
.results-panel__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.results-panel__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
.results-panel__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}
</style>
