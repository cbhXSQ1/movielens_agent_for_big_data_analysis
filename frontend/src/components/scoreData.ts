// 新前端 · 五维评分基线数据（与 HTML 原型一致）。
// 真实数据到达后由 tasks store 的 currentResult.blocks.score_comparison 覆盖；
// 此处仅作为视觉基线与空态兜底，生产环境由真实结果驱动。
import type { ScoreDim } from '@/components/ScoreBars.vue'

export const SCORE_DIMS: ScoreDim[] = [
  { name: '准确性', color: 'blue', before: 97.71, after: 99.79 },
  { name: '完整性', color: 'purple', before: 98.82, after: 99.99 },
  { name: '唯一性', color: 'mint', before: 90.9, after: 100 },
  { name: '时效性', color: 'blue', before: 98.22, after: 100 },
  { name: '一致性', color: 'pink', before: 89.65, after: 100 },
]

export const COMPOSITE_BEFORE = 95.07
export const COMPOSITE_AFTER = 99.95

export interface DataRow {
  table: string
  before: number
  after: number
  change: number
}

export const DATA_ROWS: DataRow[] = [
  { table: '评分 ratings', before: 1150241, after: 1000209, change: -150032 },
  { table: '用户 users', before: 6946, after: 6040, change: -906 },
  { table: '电影 movies', before: 4465, after: 3883, change: -582 },
]

export function fmtInt(n: number): string {
  return n.toLocaleString('en-US')
}

export function fmtChange(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return sign + fmtInt(Math.abs(n))
}
