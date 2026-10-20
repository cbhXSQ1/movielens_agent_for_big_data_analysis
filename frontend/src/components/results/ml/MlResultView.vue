<script setup lang="ts">
// FE-ML-1 · 结果三视图的宿主。
// 读取结果里的 `task_type`（顶层字段、任务视图字段或 blocks 里的同名键），
// 取不到时按存在的板块键推断；再经注册表 `mlViewFor` 动态渲染对应组件。
// 未知类型给柔和兜底；无任何机器学习板块时整块不渲染。
// `result` 缺省时回落到 tasks store 的 latestResult（与结果标签共用单一事实源）。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'
import { isRecord } from '@/components/results/blocks'
import type { ResultPayload } from '@/api/types'
import { mlViewFor } from './registry'
import { useResultBlocks } from '@/composables/useResultBlocks'

const props = defineProps<{
  /** 结果载荷；缺省时用 store 的 latestResult。 */
  result?: ResultPayload | null
  /** 显式指定任务类型（可选，优先于结果里的推断）。 */
  taskType?: string | null
}>()

const { latestResult } = storeToRefs(useTasksStore())
const { result, blocks } = useResultBlocks(
  () => props.result ?? latestResult.value ?? null,
)

const taskType = computed<string | null>(() => {
  if (props.taskType) return props.taskType
  const payload = result.value
  if (payload) {
    const top = (payload as unknown as Record<string, unknown>).task_type
    if (typeof top === 'string' && top !== '') return top
    const taskRaw = payload.task as unknown as Record<string, unknown> | null
    const fromTask = taskRaw?.task_type
    if (typeof fromTask === 'string' && fromTask !== '') return fromTask
  }
  const b = blocks.value
  if (b) {
    const fromBlock = b.task_type
    if (typeof fromBlock === 'string' && fromBlock !== '') return fromBlock
    if (isRecord(b.classification)) return 'classification'
    if (isRecord(b.clustering)) return 'clustering'
    if (isRecord(b.dimensionality_reduction)) return 'dimensionality_reduction'
  }
  return null
})

const view = computed(() => mlViewFor(taskType.value))

/** 传给视图的原始指标板块（按 task_type 取；未知则为 null）。 */
const metrics = computed<unknown>(() => {
  const b = blocks.value
  if (!b) return null
  switch (taskType.value) {
    case 'classification':
      return b.classification ?? null
    case 'clustering':
      return b.clustering ?? null
    case 'dimensionality_reduction':
      return b.dimensionality_reduction ?? null
    default:
      return null
  }
})
</script>

<template>
  <div v-if="taskType" class="ml-result-view" data-testid="ml-result-view">
    <component :is="view" v-if="view" :metrics="metrics" />
    <p v-else class="ml-result-view__fallback" data-testid="ml-result-view-fallback">
      暂不支持展示该类型的机器学习结果（{{ taskType }}）。
    </p>
  </div>
</template>

<style scoped>
.ml-result-view {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ml-result-view__fallback {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
</style>
