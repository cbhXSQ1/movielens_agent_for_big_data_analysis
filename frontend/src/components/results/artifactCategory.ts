// 产物类别（§3.12）的展示映射与判定：徽标配色、日志类识别、按路径归类。
// 纯函数、无依赖，供产物标签（ArtifactView）与产出文件区（TaskFilesSection）共用。
import type { ArtifactCategory } from '@/api/types'

/** Naive UI `n-tag` 支持的配色。 */
export type CategoryTagType =
  | 'default'
  | 'info'
  | 'success'
  | 'warning'
  | 'error'

/** 类别 → 徽标配色：数据=info、报告=success、日志=default、指标/元数据=warning、隔离=error、其它=default。 */
const CATEGORY_TAG_TYPE: Record<ArtifactCategory, CategoryTagType> = {
  数据: 'info',
  隔离: 'error',
  报告: 'success',
  指标: 'warning',
  元数据: 'warning',
  日志: 'default',
  其它: 'default',
}

/** 未知/缺失类别一律按 `default`，保证「category 宽松接收」也有稳定配色。 */
export function categoryTagType(category: string | null | undefined): CategoryTagType {
  if (!category) return 'default'
  return CATEGORY_TAG_TYPE[category as ArtifactCategory] ?? 'default'
}

/** basename 是否 `driver.log` 这类日志文件名（以 `.log` 结尾）。 */
export function isLogFileName(path: string | null | undefined): boolean {
  const name = String(path ?? '').split(/[\\/]/).pop() ?? ''
  return /\.log$/i.test(name)
}

/** 路径是否落在 `logs/` 段下（`logs/x`、`a/logs/x` 都算）。 */
export function isLogPath(path: string | null | undefined): boolean {
  const p = String(path ?? '')
  if (!p) return false
  return /(^|[\\/])logs[\\/]/i.test(p)
}

/** 产物是否为日志类：类别为「日志」、路径在 `logs/` 下、或文件名形如 `driver.log`。 */
export function isLogArtifact(artifact: {
  category?: string | null
  path?: string | null
  title?: string | null
}): boolean {
  if (artifact.category === '日志') return true
  if (isLogPath(artifact.path)) return true
  return isLogFileName(artifact.path) || isLogFileName(artifact.title)
}

/** 按路径粗分产物类别（产出文件行只给 path/kind，没有远端 category）。 */
export function deriveCategoryFromPath(
  path: string | null | undefined,
): ArtifactCategory {
  const p = String(path ?? '').toLowerCase()
  if (!p) return '其它'
  if (isLogPath(p) || isLogFileName(p)) return '日志'
  if (p.includes('quarantine') || p.includes('isolat')) return '隔离'
  if (p.includes('report')) return '报告'
  if (p.includes('metric') || p.includes('score')) return '指标'
  if (p.includes('meta') || p.includes('schema')) return '元数据'
  return '其它'
}
