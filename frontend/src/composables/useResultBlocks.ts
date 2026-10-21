// 阶段 E · FE.1：把 tasks store 的 `latestResult`（§3.9 result_payload）绑定成一
// 组「视图可直接消费」的计算属性。结果标签下的六个视图共用此 composable，
// 保证绑定单一事实源（store），组件自身不发起请求、不各自解析 blocks。
//
// FE-5：另暴露 `versions`（四类：data/rule/scoring/policy）与 `timeBoundaries`
// （T1 训练期 / T2 验证期），**直接取自结果板块、原值透传**，不做任何推断或换算；
// 板块缺席时为 null，由视图自行隐藏。组件可传入 `source` 绑定某次具体任务的结果
// （如任务详情按 agent_task_id 取到的载荷），缺省仍绑定 store.latestResult。

import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { storeToRefs } from 'pinia'
import { useTasksStore } from '@/stores/tasks'
import type { ResultPayload } from '@/api/types'
import {
  isRecord,
  normalizeDataVolume,
  normalizeLimitations,
  normalizeQuarantineSamples,
  normalizeReport,
  normalizeScoreComparison,
} from '@/components/results/blocks'

/** 一行版本展示（原样取自后端，仅加中文标签）。 */
export interface VersionRow {
  key: string
  label: string
  value: string
}

/** 一行时间边界展示（原样取自后端，仅加中文标签）。 */
export interface TimeBoundaryRow {
  key: string
  label: string
  value: string
}

/** 四类版本的展示标签；出现未知键时回落为该键原样。 */
const VERSION_LABELS: Record<string, string> = {
  data: '数据版本',
  rule: '规则版本',
  scoring: '评分方案版本',
  policy: '策略版本',
}

/** 两个时间边界的展示标签；出现未知键时回落为该键原样。 */
const TIME_BOUNDARY_LABELS: Record<string, string> = {
  T1: '训练期截止（T1）',
  T2: '验证期截止（T2）',
}

/** 把后端值转成展示文本；字符串原样，带 version 的对象取其 version，其余 JSON。 */
function displayText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  if (isRecord(value)) {
    if (typeof value.version === 'string') return value.version
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

/** 把 `versions` 拆成展示行：后端给哪些键就显示哪些，顺序照后端。 */
export function versionRows(raw: unknown): VersionRow[] {
  if (!isRecord(raw)) return []
  return Object.entries(raw).map(([key, value]) => ({
    key,
    label: VERSION_LABELS[key] ?? key,
    value: displayText(value),
  }))
}

/** 把 `time_boundaries` 拆成展示行：T1、T2 在前，其余键照后端顺序。 */
export function timeBoundaryRows(raw: unknown): TimeBoundaryRow[] {
  if (!isRecord(raw)) return []
  const preferred = ['T1', 'T2'].filter((key) => key in raw)
  const rest = Object.keys(raw).filter((key) => !preferred.includes(key))
  return [...preferred, ...rest].map((key) => ({
    key,
    label: TIME_BOUNDARY_LABELS[key] ?? key,
    value: displayText(raw[key]),
  }))
}

export function useResultBlocks(
  source?: MaybeRefOrGetter<ResultPayload | null | undefined>,
) {
  const { latestResult } = storeToRefs(useTasksStore())

  const result = computed<ResultPayload | null>(
    () => (source === undefined ? latestResult.value : toValue(source)) ?? null,
  )
  const blocks = computed(() => result.value?.blocks ?? null)
  const task = computed(() => result.value?.task ?? null)
  const taskId = computed(() => result.value?.task?.agent_task_id ?? null)

  /** 四类版本原值；板块缺席（或形状不符）时为 null，不造空壳。 */
  const versions = computed<Record<string, unknown> | null>(() => {
    const raw = blocks.value?.versions
    return isRecord(raw) ? raw : null
  })

  /** 两个时间边界原值；板块缺席（或形状不符）时为 null，不造空壳。 */
  const timeBoundaries = computed<Record<string, unknown> | null>(() => {
    const raw = blocks.value?.time_boundaries
    return isRecord(raw) ? raw : null
  })

  // FE-ML-1 · 迭代二三块 ML 结果：**原值透传**，不做规范化（形状以契约 §3.2–§3.4 为准）。
  const classification = computed<unknown>(() => blocks.value?.classification ?? null)
  const clustering = computed<unknown>(() => blocks.value?.clustering ?? null)
  const dimensionalityReduction = computed<unknown>(
    () => blocks.value?.dimensionality_reduction ?? null,
  )

  return {
    /** 当前任务结果载荷（无则为 null）。 */
    result,
    /** 当前任务视图（无则为 null）。 */
    task,
    /** 当前任务的双编号之 Agent 编号（文件预览用）。 */
    taskId,
    /** 原始 blocks（就绪来源）。 */
    blocks,
    /** 四类版本原值；无则为 null。 */
    versions,
    /** 时间边界（T1/T2）原值；无则为 null。 */
    timeBoundaries,
    /** 分类结果板块原值（契约 §3.2）；无则为 null。 */
    classification,
    /** 聚类结果板块原值（契约 §3.3）；无则为 null。 */
    clustering,
    /** 降维结果板块原值（契约 §3.4）；无则为 null。 */
    dimensionalityReduction,
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
