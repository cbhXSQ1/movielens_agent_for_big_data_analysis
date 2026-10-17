// 阶段 E · FE.1：`result_payload.blocks` 的解析与规范化。
//
// 数据层（`ResultBlocks`）把这些块的类型故意留成 `unknown`——形状以 Agent 为准，
// 且各版本可能微调。结果视图一律先经这里的**防御式规范化**，把 unknown 收敛成
// 视图可直接消费的强类型结构；缺失/形状不符时返回 null / 空数组，**绝不造数字**。
//
// 形状依据：《Agent_接口文档.md》§3.9 与 driver `result` 实测（§4.5）：
//   score_comparison → { before, after, delta, metrics? }
//   data_volume      → { input, output, quarantine:{total,by_rule}, dedupe, fix }
//   quarantine_samples → [{ line_no, raw_line, rule_id, stage, reason }]
//   report           → Markdown 全文（字符串）
//   limitations      → string[]

export type UnknownRecord = Record<string, unknown>

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  return null
}

function round2(value: number): number {
  return Math.round(value * 100) / 100
}

/** 从任意值抽出「键 → 数字」的映射，非数字项丢弃。 */
export function numericMap(value: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (!isRecord(value)) return out
  for (const [key, raw] of Object.entries(value)) {
    const n = toFiniteNumber(raw)
    if (n !== null) out[key] = n
  }
  return out
}

// ---------------------------------------------------------------------------
// 评分对比（score_comparison）
// ---------------------------------------------------------------------------

export interface ScoreRow {
  key: string
  label: string
  before: number
  after: number
  delta: number
}

export interface ScoreComparison {
  before: Record<string, number>
  after: Record<string, number>
  delta: Record<string, number>
  rows: ScoreRow[]
}

const DIMENSION_ORDER: readonly string[] = [
  'Accurate',
  'Complete',
  'Unique',
  'Up-to-date',
  'Consistent',
  'composite',
]

const DIMENSION_LABELS: Record<string, string> = {
  Accurate: '准确性',
  Complete: '完整性',
  Unique: '唯一性',
  'Up-to-date': '时效性',
  Consistent: '一致性',
  composite: '综合得分',
}

export function dimensionLabel(key: string): string {
  return DIMENSION_LABELS[key] ?? key
}

function orderedKeys(keys: Iterable<string>): string[] {
  const all = [...new Set(keys)]
  const known = DIMENSION_ORDER.filter((k) => all.includes(k))
  const extra = all.filter((k) => !DIMENSION_ORDER.includes(k))
  return [...known, ...extra]
}

/** 规范化 score_comparison；before/after 全空时视为无数据（返回 null）。 */
export function normalizeScoreComparison(raw: unknown): ScoreComparison | null {
  if (!isRecord(raw)) return null
  const before = numericMap(raw.before)
  const after = numericMap(raw.after)
  if (Object.keys(before).length === 0 && Object.keys(after).length === 0) {
    return null
  }
  const rawDelta = numericMap(raw.delta)
  const hasRawDelta = Object.keys(rawDelta).length > 0
  const delta: Record<string, number> = {}
  for (const key of orderedKeys([...Object.keys(before), ...Object.keys(after)])) {
    if (hasRawDelta && key in rawDelta) {
      delta[key] = rawDelta[key]
    } else if (key in before && key in after) {
      delta[key] = round2(after[key] - before[key])
    }
  }
  const rows: ScoreRow[] = orderedKeys([
    ...Object.keys(before),
    ...Object.keys(after),
  ]).map((key) => ({
    key,
    label: dimensionLabel(key),
    before: before[key] ?? 0,
    after: after[key] ?? 0,
    delta: delta[key] ?? 0,
  }))
  return { before, after, delta, rows }
}

// ---------------------------------------------------------------------------
// 数据量变化（data_volume）
// ---------------------------------------------------------------------------

export interface NamedCount {
  key: string
  label: string
  value: number
}

export interface DataVolume {
  input: NamedCount[]
  output: NamedCount[]
  quarantineTotal: number | null
  quarantineByRule: NamedCount[]
  dedupe: NamedCount[]
  fix: NamedCount[]
}

const INPUT_LABELS: Record<string, string> = {
  ratings_lines: '评分行数',
  users_lines: '用户行数',
  movies_lines: '电影行数',
}

const OUTPUT_LABELS: Record<string, string> = {
  ratings: '评分记录',
  users: '用户记录',
  movies: '电影记录',
}

const GENERIC_LABELS: Record<string, string> = {
  ratings: '评分',
  users: '用户',
  movies: '电影',
  R4_ms: 'R4 毫秒补全',
  P1_text: 'P1 文本修复',
  M2_strip: 'M2 去空格',
  U3_zip_plus4: 'U3 邮编补位',
}

function countArray(raw: unknown, labels: Record<string, string>): NamedCount[] {
  const map = numericMap(raw)
  return Object.keys(map).map((key) => ({
    key,
    label: labels[key] ?? GENERIC_LABELS[key] ?? key,
    value: map[key],
  }))
}

/** 规范化 data_volume；完全空则返回 null。 */
export function normalizeDataVolume(raw: unknown): DataVolume | null {
  if (!isRecord(raw)) return null
  const input = countArray(raw.input, INPUT_LABELS)
  const output = countArray(raw.output, OUTPUT_LABELS)
  const quarantine = isRecord(raw.quarantine) ? raw.quarantine : {}
  const quarantineByRule = countArray(quarantine.by_rule, {})
  const dedupe = countArray(raw.dedupe, {})
  const fix = countArray(raw.fix, {})
  const quarantineTotal =
    toFiniteNumber(quarantine.total) ??
    (quarantineByRule.length
      ? quarantineByRule.reduce((sum, item) => sum + item.value, 0)
      : null)
  const empty =
    input.length === 0 &&
    output.length === 0 &&
    quarantineByRule.length === 0 &&
    quarantineTotal === null &&
    dedupe.length === 0 &&
    fix.length === 0
  if (empty) return null
  return { input, output, quarantineTotal, quarantineByRule, dedupe, fix }
}

// ---------------------------------------------------------------------------
// 隔离样例（quarantine_samples）
// ---------------------------------------------------------------------------

export interface QuarantineSample {
  lineNo: number | null
  ruleId: string
  stage: string
  reason: string
  rawLine: string
}

function toDisplayText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  return String(value)
}

/** 规范化 quarantine_samples；非数组或空数组返回 []。 */
export function normalizeQuarantineSamples(raw: unknown): QuarantineSample[] {
  if (!Array.isArray(raw)) return []
  return raw.filter(isRecord).map((item) => ({
    lineNo: toFiniteNumber(item.line_no),
    ruleId: toDisplayText(item.rule_id),
    stage: toDisplayText(item.stage),
    reason: toDisplayText(item.reason),
    rawLine: toDisplayText(item.raw_line),
  }))
}

// ---------------------------------------------------------------------------
// 依据与局限（limitations）
// ---------------------------------------------------------------------------

/** 规范化 limitations；兼容字符串数组与带 text/message 的对象项。 */
export function normalizeLimitations(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const out: string[] = []
  for (const item of raw) {
    if (typeof item === 'string' && item.trim() !== '') {
      out.push(item)
    } else if (isRecord(item)) {
      const text = toDisplayText(item.text ?? item.message ?? item.note)
      if (text.trim() !== '') out.push(text)
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// 评估报告（report）
// ---------------------------------------------------------------------------

/** 规范化 report 为 Markdown 全文；兼容字符串与 { markdown|md|text|content }。 */
export function normalizeReport(raw: unknown): string {
  if (typeof raw === 'string') return raw
  if (isRecord(raw)) {
    for (const key of ['markdown', 'md', 'text', 'content', 'report']) {
      const value = raw[key]
      if (typeof value === 'string' && value !== '') return value
    }
  }
  return ''
}
