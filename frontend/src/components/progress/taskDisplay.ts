// 阶段 H（fe-progress）· 任务进度共用的纯展示映射（无副作用、不依赖 store）。
// 依据《Agent_接口文档.md》§3.8：任务视图键名为 agent_task_id / hadoop_task_id /
// name / summary / status / status_cn / stage / progress_percent / estimated_seconds /
// updated_at。状态可枚举（排队 / 运行 / 成功 / 失败 / 已中止）。

import type { TaskStatus, TaskView } from '@/api/types'

/** 状态标签/进度条配色（NTag / NProgress 共有子集）。 */
export type TaskTagType = 'default' | 'info' | 'success' | 'warning' | 'error'

const STATUS_LABELS: Record<string, string> = {
  queued: '排队',
  running: '运行',
  succeeded: '成功',
  failed: '失败',
  aborted: '已中止',
}

const STATUS_TAG_TYPES: Record<string, TaskTagType> = {
  queued: 'default',
  running: 'info',
  succeeded: 'success',
  failed: 'error',
  aborted: 'warning',
}

/** 终态：成功 / 失败 / 已中止（此后可经 getTaskResult 取结果）。 */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'succeeded',
  'failed',
  'aborted',
])

/** 状态中文：优先用后端给的 status_cn，缺失时按枚举兜底。 */
export function statusLabel(task: Pick<TaskView, 'status' | 'status_cn'>): string {
  if (task.status_cn) return task.status_cn
  return STATUS_LABELS[task.status] ?? task.status ?? '未知'
}

/** 状态配色。 */
export function statusTagType(status: TaskStatus): TaskTagType {
  return STATUS_TAG_TYPES[status] ?? 'default'
}

/** 是否终态。 */
export function isTerminal(status: TaskStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

/** 非终态的进行中状态（排队 / 运行）：任务仍在跑，进度还会继续。 */
export function isActiveTask(status: TaskStatus): boolean {
  return status === 'queued' || status === 'running'
}

/** 进行中的提示文案；非进行中返回 null（不展示）。 */
export function activeHint(status: TaskStatus): string | null {
  return isActiveTask(status) ? '仍在运行，可继续等待' : null
}

/** 已中止的提示文案；非中止返回 null（不展示）。进度停在中断处、不强制到 100%。 */
export function abortedHint(status: TaskStatus): string | null {
  return status === 'aborted' ? '任务已中止，进度停在中断处' : null
}

/** 是否给出预计时长；无（null/undefined/非正）则如实不显示。 */
export function hasEstimate(task: Pick<TaskView, 'estimated_seconds'>): boolean {
  const seconds = task.estimated_seconds
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0
}

/** 进度百分比收敛到 [0, 100] 的整数。 */
export function clampPercent(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, Math.round(value)))
}

/** 秒数 → 可读时长；非正/非法返回占位符。 */
export function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) {
    return '—'
  }
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  if (hours > 0) return `${hours} 小时 ${minutes} 分`
  if (minutes > 0) return `${minutes} 分 ${rest} 秒`
  return `${rest} 秒`
}
