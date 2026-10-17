// 阶段 D · 结构化结果块 → 中栏结果标签 的映射。
// 说明：中栏结果标签已动态化（默认只有「对话」，其余为产物标签，见 §3.12），
// 本映射保留给任务进度详情（TaskDetail）陈列「结果包含哪些数据块」参考用。

import type { ResultBlocks } from '@/api/types'
import type { ResultTabKey } from '@/components/shellConfig'

export interface ResultBlockMeta {
  /** `result_payload.blocks` 里的键名。 */
  key: string
  /** 给人看的块名。 */
  label: string
  /** 点击后要切到的中栏标签 key（动态化后为参考值）。 */
  tab: ResultTabKey
}

/** 可跳转结果块的顺序与文案。 */
export const RESULT_BLOCK_META: readonly ResultBlockMeta[] = [
  { key: 'score_comparison', label: '五维评分对比', tab: 'score' },
  { key: 'data_volume', label: '数据量变化', tab: 'evidence' },
  { key: 'quarantine_samples', label: '隔离样例', tab: 'evidence' },
  { key: 'report', label: '评估报告', tab: 'report' },
  { key: 'limitations', label: '依据与局限', tab: 'evidence' },
]

/** 从结果块集合中挑出「有值」的可跳转块。 */
export function presentResultBlocks(
  blocks: ResultBlocks | null | undefined,
): ResultBlockMeta[] {
  if (!blocks) return []
  return RESULT_BLOCK_META.filter(
    (meta) => blocks[meta.key] !== undefined && blocks[meta.key] !== null,
  )
}
