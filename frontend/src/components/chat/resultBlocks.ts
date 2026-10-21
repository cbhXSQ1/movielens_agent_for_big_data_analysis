// 阶段 D · 结构化结果块 → 中栏结果标签 的映射。
// 说明：中栏结果标签已动态化（默认只有「对话」，其余为产物标签，见 §3.12），
// 本映射保留给任务进度详情（TaskDetail）陈列「结果包含哪些数据块」参考用。
// 六块与远端 `get_task_result` 的 blocks 一一对应；其中「任务状态」由进度页直接
// 展示、无独立产物。其余五块各挂一类产物：点块即打开该产物的标签（动态标签模型），
// 找不到对应产物时只展示、不导航（不再落到失效的固定 tab 键）。

import type { ArtifactRef, ResultBlocks } from '@/api/types'

export interface ResultBlockMeta {
  /** `result_payload.blocks` 里的键名。 */
  key: string
  /** 给人看的块名。 */
  label: string
  /**
   * 该块对应产物的类别（远端 `artifacts[].category`）。
   * 为 `null` 表示该块无独立产物（如「任务状态」），只展示、不导航。
   */
  artifactCategory: string | null
}

/** 结果六块的顺序与文案。 */
export const RESULT_BLOCK_META: readonly ResultBlockMeta[] = [
  { key: 'score_comparison', label: '五维评分对比', artifactCategory: '评分' },
  { key: 'data_volume', label: '数据量变化', artifactCategory: '证据' },
  { key: 'quarantine_samples', label: '隔离样例', artifactCategory: '证据' },
  { key: 'report', label: '评估报告', artifactCategory: '报告' },
  { key: 'limitations', label: '依据与局限', artifactCategory: '依据' },
  { key: 'task_status', label: '任务状态', artifactCategory: null },
]

/** 从结果块集合中挑出「有值」的块。 */
export function presentResultBlocks(
  blocks: ResultBlocks | null | undefined,
): ResultBlockMeta[] {
  if (!blocks) return []
  return RESULT_BLOCK_META.filter(
    (meta) => blocks[meta.key] !== undefined && blocks[meta.key] !== null,
  )
}

/**
 * 在产物列表里找该块对应的产物（按 `artifactCategory` 取第一个匹配项）。
 * 该块无独立产物、或列表里没有同类产物时返回 `null`（调用方据此只展示、不导航）。
 */
export function artifactForBlock(
  meta: ResultBlockMeta,
  artifacts: readonly ArtifactRef[] | null | undefined,
): ArtifactRef | null {
  if (!meta.artifactCategory) return null
  return (
    (artifacts ?? []).find((a) => a.category === meta.artifactCategory) ?? null
  )
}
