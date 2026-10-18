// FE-ML-1 · 三类机器学习结果指标的防御式规范化与展示格式化。
// 数据来自 `result_payload.blocks` 的**原值**（unknown，形状以 Agent/远端为准），
// 这里把 unknown 收敛成契约 §3.2–§3.4 的强类型；缺失/形状不符时给 null / 空数组，
// **绝不造数字**。比率类指标一律 0–1 小数，百分比换算由这里统一负责（×100）。

import { isRecord } from '@/components/results/blocks'
import type {
  MlAgreement,
  MlClassificationMetrics,
  MlCluster,
  MlClusterBehavior,
  MlClusteringMetrics,
  MlComparison,
  MlComparisonSide,
  MlConfusionMatrix,
  MlDimReductionMetrics,
  MlFeatureImportance,
  MlKSelection,
  MlKSelectionCandidate,
  MlLeakage,
  MlResource,
  MlSplitSizes,
} from '@/api/types'

/** 数字或可解析为数字的字符串 → 有限数；否则 null。 */
export function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  return null
}

function toStr(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function toStrArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}

/** 0–1 小数 → 百分比文本（×100，保留两位）；缺失时返回空字符串。 */
export function formatPercent(value: unknown, digits = 2): string {
  const n = toNumber(value)
  if (n === null) return ''
  return `${(n * 100).toFixed(digits)}%`
}

/** 小数 → 定点文本；缺失时返回空字符串。 */
export function formatNumber(value: unknown, digits = 2): string {
  const n = toNumber(value)
  if (n === null) return ''
  return n.toFixed(digits)
}

/** 整数（截断） → 文本；缺失时返回空字符串。 */
export function formatInt(value: unknown): string {
  const n = toNumber(value)
  if (n === null) return ''
  return String(Math.trunc(n))
}

/** 展示兜底：空值与空字符串统一显示为短横。 */
export function orDash(text: string): string {
  return text === '' ? '—' : text
}

// ---------------------------------------------------------------------------
// 分类（§3.2）
// ---------------------------------------------------------------------------

function normalizeFeatureImportance(raw: unknown): MlFeatureImportance[] {
  if (!Array.isArray(raw)) return []
  const out: MlFeatureImportance[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const importance = toNumber(item.importance)
    if (importance === null) continue
    out.push({ feature: toStr(item.feature), importance })
  }
  return out
}

/** 规范化分类指标；形状不符时返回 null。 */
export function normalizeClassificationMetrics(
  raw: unknown,
): MlClassificationMetrics | null {
  if (!isRecord(raw)) return null
  const cm: Record<string, unknown> = isRecord(raw.confusion_matrix)
    ? raw.confusion_matrix
    : {}
  const split: Record<string, unknown> = isRecord(raw.split_sizes)
    ? raw.split_sizes
    : {}
  const confusion: MlConfusionMatrix = {
    tp: toNumber(cm.tp),
    fp: toNumber(cm.fp),
    tn: toNumber(cm.tn),
    fn: toNumber(cm.fn),
  }
  const splitSizes: MlSplitSizes = {
    train: toNumber(split.train),
    valid: toNumber(split.valid),
    test: toNumber(split.test),
  }
  let leakage: MlLeakage | null = null
  if (isRecord(raw.leakage)) {
    leakage = {
      policy: toStr(raw.leakage.policy),
      verified_on: toStr(raw.leakage.verified_on),
    }
  }
  return {
    f1: toNumber(raw.f1),
    auc: toNumber(raw.auc),
    pr_auc: toNumber(raw.pr_auc),
    precision: toNumber(raw.precision),
    recall: toNumber(raw.recall),
    threshold: toNumber(raw.threshold),
    positive_rate: toNumber(raw.positive_rate),
    confusion_matrix: confusion,
    feature_importance: normalizeFeatureImportance(raw.feature_importance),
    feature_importance_note: toStr(raw.feature_importance_note),
    split_sizes: splitSizes,
    leakage,
  }
}

// ---------------------------------------------------------------------------
// 聚类（§3.3）
// ---------------------------------------------------------------------------

function normalizeBehavior(raw: unknown): MlClusterBehavior {
  const b: Record<string, unknown> = isRecord(raw) ? raw : {}
  return {
    activity: toNumber(b.activity),
    avg_rating: toNumber(b.avg_rating),
    rating_std: toNumber(b.rating_std),
    recency: toNumber(b.recency),
  }
}

function normalizeClusters(raw: unknown): MlCluster[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter(isRecord)
    .map((item) => ({
      cluster_id: toNumber(item.cluster_id),
      size: toNumber(item.size),
      label: toStr(item.label),
      top_genres: toStrArray(item.top_genres),
      behavior: normalizeBehavior(item.behavior),
    }))
}

function normalizeKSelection(raw: unknown): MlKSelection | null {
  if (!isRecord(raw)) return null
  const candidatesRaw = Array.isArray(raw.candidates) ? raw.candidates : []
  const candidates: MlKSelectionCandidate[] = candidatesRaw
    .filter(isRecord)
    .map((item) => ({ k: toNumber(item.k), silhouette: toNumber(item.silhouette) }))
  return { method: toStr(raw.method), candidates }
}

/** 规范化聚类指标；形状不符时返回 null。 */
export function normalizeClusteringMetrics(
  raw: unknown,
): MlClusteringMetrics | null {
  if (!isRecord(raw)) return null
  return {
    k: toNumber(raw.k),
    silhouette: toNumber(raw.silhouette),
    davies_bouldin: toNumber(raw.davies_bouldin),
    clusters: normalizeClusters(raw.clusters),
    k_selection: normalizeKSelection(raw.k_selection),
  }
}

// ---------------------------------------------------------------------------
// 降维（§3.4）
// ---------------------------------------------------------------------------

function normalizeComparisonSide(raw: unknown): MlComparisonSide {
  const s: Record<string, unknown> = isRecord(raw) ? raw : {}
  return {
    silhouette: toNumber(s.silhouette),
    davies_bouldin: toNumber(s.davies_bouldin),
    k: toNumber(s.k),
  }
}

function normalizeAgreement(raw: unknown): MlAgreement {
  const a: Record<string, unknown> = isRecord(raw) ? raw : {}
  return { ari: toNumber(a.ari), nmi: toNumber(a.nmi) }
}

function normalizeComparison(raw: unknown): MlComparison | null {
  if (!isRecord(raw)) return null
  return {
    before: normalizeComparisonSide(raw.before),
    after: normalizeComparisonSide(raw.after),
    agreement: normalizeAgreement(raw.agreement),
  }
}

function normalizeResource(raw: unknown): MlResource | null {
  if (!isRecord(raw)) return null
  return { fit_seconds: toNumber(raw.fit_seconds), mem_mb: toNumber(raw.mem_mb) }
}

/** 规范化降维指标；`explained_variance_ratio` 保持 null / 数字数组原语义。 */
export function normalizeDimReductionMetrics(
  raw: unknown,
): MlDimReductionMetrics | null {
  if (!isRecord(raw)) return null
  let explained: number[] | null = null
  if (Array.isArray(raw.explained_variance_ratio)) {
    explained = raw.explained_variance_ratio
      .map((item) => toNumber(item))
      .filter((n): n is number => n !== null)
  }
  return {
    n_components: toNumber(raw.n_components),
    explained_variance_ratio: explained,
    reconstruction_error: toNumber(raw.reconstruction_error),
    resource: normalizeResource(raw.resource),
    comparison: normalizeComparison(raw.comparison),
  }
}
