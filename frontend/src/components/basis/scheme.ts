// 阶段 I · FI.2：方案 JSON（评分方案 / 清洗规则方案）的解析与规范化。
// 内容来自 Agent（§3.11.3 `GET /api/schemes/content?name=…`），形状以方案文件本身为准
// （见 `config/scoring_scheme.v1.json` 与 `config/cleaning_rules.v1.json`）。
// 一律先经这里防御式收敛为强类型：字段缺失/形状不符时给空值或空数组，
// 视图据此**如实标注**，绝不臆造内容。

import { dimensionLabel, isRecord, numericMap } from '@/components/results/blocks'

function toStr(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function toNum(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function toBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

function strArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is string => typeof item === 'string' && item.trim() !== '',
  )
}

export interface WeightEntry {
  key: string
  label: string
  value: number
}

function weightEntries(raw: unknown): WeightEntry[] {
  const map = numericMap(raw)
  return Object.keys(map).map((key) => ({
    key,
    label: dimensionLabel(key),
    value: map[key],
  }))
}

// ---------------------------------------------------------------------------
// 评分方案：维度 / 指标
// ---------------------------------------------------------------------------

export interface SchemeMetric {
  id: string
  name: string
  enabled: boolean | null
  measure: string
  weight: number | null
  description: string
  limitations: string
  unverifiable: string[]
}

export interface SchemeDimension {
  id: string
  nameZh: string
  definition: string
  weight: number | null
  metrics: SchemeMetric[]
}

function normalizeMetrics(raw: unknown): SchemeMetric[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((m) => ({
    id: toStr(m.id),
    name: toStr(m.name),
    enabled: toBool(m.enabled),
    measure: toStr(m.measure),
    weight: toNum(m.weight),
    description: toStr(m.description),
    limitations: toStr(m.limitations),
    unverifiable: strArray(m.unverifiable),
  }))
}

function normalizeDimensions(raw: unknown): SchemeDimension[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((d) => ({
    id: toStr(d.id),
    nameZh: toStr(d.name_zh),
    definition: toStr(d.definition),
    weight: toNum(d.weight),
    metrics: normalizeMetrics(d.metrics),
  }))
}

// ---------------------------------------------------------------------------
// 清洗规则方案：规则 / 流水线 / 处理约定
// ---------------------------------------------------------------------------

export interface SchemeRule {
  id: string
  name: string
  stage: string
  table: string
  enabled: boolean | null
  severity: string
  action: string
  policyValue: string
  description: string
}

export interface PipelineStage {
  stage: string
  rules: string[]
}

export interface ProcessingConvention {
  id: string
  description: string
}

function normalizeRules(raw: unknown): SchemeRule[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((r) => {
    const policy = isRecord(r.policy) ? r.policy : {}
    return {
      id: toStr(r.id),
      name: toStr(r.name),
      stage: toStr(r.stage),
      table: toStr(r.table),
      enabled: toBool(r.enabled),
      severity: toStr(r.severity),
      action: toStr(r.action),
      policyValue: toStr(policy.value),
      description: toStr(r.description),
    }
  })
}

function normalizePipeline(raw: unknown): PipelineStage[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((p) => ({
    stage: toStr(p.stage),
    rules: strArray(p.rules),
  }))
}

function normalizeConventions(raw: unknown): ProcessingConvention[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((c) => ({
    id: toStr(c.id),
    description: toStr(c.description),
  }))
}

// ---------------------------------------------------------------------------
// 汇总视图模型
// ---------------------------------------------------------------------------

export type SchemeMode = 'scoring' | 'cleaning' | 'other'

export interface SchemeComposite {
  enabled: boolean | null
  weights: WeightEntry[]
  description: string
  editRules: string
}

export interface SchemeComparability {
  validScope: string
  crossVersion: boolean | null
  sameFormulas: boolean | null
  quarantineNote: string
}

export interface SchemeScoreScale {
  min: number | null
  max: number | null
  rounding: number | null
}

export interface SchemeView {
  mode: SchemeMode
  configType: string
  schemeId: string
  version: string
  status: string
  created: string
  description: string
  operatorLibrary: string
  engineVersion: string
  engineNotes: string
  dimensions: SchemeDimension[]
  composite: SchemeComposite | null
  comparability: SchemeComparability | null
  scoreScale: SchemeScoreScale | null
  rules: SchemeRule[]
  pipeline: PipelineStage[]
  conventions: ProcessingConvention[]
}

/** 把 Agent 返回的方案内容收敛为强类型；非对象返回 null。 */
export function normalizeScheme(raw: unknown): SchemeView | null {
  if (!isRecord(raw)) return null

  const configType = toStr(raw.config_type)
  const dimensions = normalizeDimensions(raw.dimensions)
  const rules = normalizeRules(raw.rules)
  const isScoring = configType === 'scoring_scheme' || dimensions.length > 0
  const isCleaning = configType === 'cleaning_rules' || rules.length > 0
  const mode: SchemeMode = isScoring ? 'scoring' : isCleaning ? 'cleaning' : 'other'

  const engine = isRecord(raw.engine_compat) ? raw.engine_compat : {}
  const compositeRaw = isRecord(raw.composite) ? raw.composite : null
  const comparabilityRaw = isRecord(raw.comparability) ? raw.comparability : null
  const scaleRaw = isRecord(raw.score_scale) ? raw.score_scale : null

  return {
    mode,
    configType,
    schemeId: toStr(raw.scheme_id),
    version: toStr(raw.version),
    status: toStr(raw.status),
    created: toStr(raw.created),
    description: toStr(raw.description),
    operatorLibrary: toStr(engine.operator_library),
    engineVersion: toStr(engine.scoring_engine ?? engine.action_engine),
    engineNotes: toStr(engine.notes),
    dimensions,
    composite: compositeRaw
      ? {
          enabled: toBool(compositeRaw.enabled),
          weights: weightEntries(compositeRaw.weights),
          description: toStr(compositeRaw.description),
          editRules: toStr(compositeRaw.edit_rules),
        }
      : null,
    comparability: comparabilityRaw
      ? {
          validScope: toStr(comparabilityRaw.valid_scope),
          crossVersion: toBool(comparabilityRaw.cross_version_comparison),
          sameFormulas: toBool(comparabilityRaw.before_after_same_formulas),
          quarantineNote: toStr(comparabilityRaw.quarantine_effect_note),
        }
      : null,
    scoreScale: scaleRaw
      ? {
          min: toNum(scaleRaw.min),
          max: toNum(scaleRaw.max),
          rounding: toNum(scaleRaw.rounding),
        }
      : null,
    rules,
    pipeline: normalizePipeline(raw.pipeline),
    conventions: normalizeConventions(raw.processing_conventions),
  }
}

/** 布尔三态 → 中文展示（null 表示方案未声明）。 */
export function formatBool(value: boolean | null): string {
  if (value === null) return '未声明'
  return value ? '是' : '否'
}
