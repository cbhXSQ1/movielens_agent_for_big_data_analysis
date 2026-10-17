// 阶段 E · FE.1：把 tasks store 的 `latestResult`（§3.9 result_payload）绑定成一
// 组「视图可直接消费」的计算属性。结果标签下的六个视图共用此 composable，
// 保证绑定单一事实源（store），组件自身不发起请求、不各自解析 blocks。

import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'
import {
  normalizeDataVolume,
  normalizeLimitations,
  normalizeQuarantineSamples,
  normalizeReport,
  normalizeScoreComparison,
} from '@/components/results/blocks'

export function useResultBlocks() {
  const { latestResult } = storeToRefs(useTasksStore())

  const blocks = computed(() => latestResult.value?.blocks ?? null)
  const task = computed(() => latestResult.value?.task ?? null)
  const taskId = computed(() => latestResult.value?.task?.agent_task_id ?? null)

  return {
    /** 当前任务结果载荷（无则为 null）。 */
    result: latestResult,
    /** 当前任务视图（无则为 null）。 */
    task,
    /** 当前任务的双编号之 Agent 编号（文件预览用）。 */
    taskId,
    /** 原始 blocks（就绪来源）。 */
    blocks,
    /** 规范化后的评分对比；无则为 null。 */
    scoreComparison: computed(() =>
      normalizeScoreComparison(blocks.value?.score_comparison),
    ),
    /** 规范化后的数据量变化；无则为 null。 */
    dataVolume: computed(() => normalizeDataVolume(blocks.value?.data_volume)),
    /** 规范化后的隔离样例；无则为 []。 */
    quarantineSamples: computed(() =>
      normalizeQuarantineSamples(blocks.value?.quarantine_samples),
    ),
    /** 规范化后的局限与依据；无则为 []。 */
    limitations: computed(() => normalizeLimitations(blocks.value?.limitations)),
    /** 规范化后的报告 Markdown 全文；无则为 ''。 */
    report: computed(() => normalizeReport(blocks.value?.report)),
  }
}
