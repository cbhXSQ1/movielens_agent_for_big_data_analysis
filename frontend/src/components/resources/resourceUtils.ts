// 阶段 G · 资源面板共用的纯展示格式化与错误转述（无副作用、不依赖 store）。
// 时间戳来自 §3.11 的 epoch 秒（如 updated_at / published_at），统一按秒换算。
import { errorCodeText, isApiError } from '@/api/http'

/** epoch 秒 → `YYYY-MM-DD HH:mm`；缺失/非法返回占位符。 */
export function formatTimestamp(ts: number | null | undefined): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts) || ts <= 0) return '—'
  const date = new Date(ts * 1000)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 优先按错误码给出中文的码（§3.11.5 / §3.10）。 */
const CODE_FIRST = new Set([
  'UNSUPPORTED_FILE',
  'UPLOAD_TOO_LARGE',
  'FILE_NOT_FOUND',
  'FORBIDDEN_PATH',
  'NETWORK_ERROR',
  'TIMEOUT',
])

/** 把未知异常转成可展示的中文文案（ApiError 已知码优先用码表）。 */
export function describeError(err: unknown): string {
  if (isApiError(err)) {
    if (CODE_FIRST.has(err.code)) return errorCodeText(err.code)
    return err.message || errorCodeText(err.code)
  }
  if (err instanceof Error) return err.message
  return '操作失败'
}
