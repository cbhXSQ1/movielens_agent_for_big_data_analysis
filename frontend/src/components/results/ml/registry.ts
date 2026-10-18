// FE-ML-1 · 机器学习结果视图注册表（按 task_type 多态分发）。
// 每个视图是独立、可插拔组件；新增任务类型只需在此登记，宿主无需改动。
// `mlViewFor` 是工厂：给 task_type 返回组件（未知则 null，由宿主兜底）。

import type { Component } from 'vue'
import ClassificationCard from './ClassificationCard.vue'
import ClusteringPanel from './ClusteringPanel.vue'
import DimReductionPanel from './DimReductionPanel.vue'

/** task_type → 结果视图组件。 */
export const ML_VIEW_REGISTRY: Record<string, Component> = {
  classification: ClassificationCard,
  clustering: ClusteringPanel,
  dimensionality_reduction: DimReductionPanel,
}

/** 按任务类型取结果视图；未知类型返回 null。 */
export function mlViewFor(taskType: string | null | undefined): Component | null {
  if (!taskType) return null
  return ML_VIEW_REGISTRY[taskType] ?? null
}
