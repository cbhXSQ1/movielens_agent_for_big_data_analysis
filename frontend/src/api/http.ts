// FB.2 · REST 客户端：基于 fetch 的 JSON/文本/表单请求、超时、统一解包与错误码→中文。
// 只依赖 @/api/config 的 apiUrl；不硬编码主机名，不新增依赖。
//
// 约定：后端「业务失败」一律 HTTP 200 + `{ok:false, error:{code,message}}`（§3.10），
// 而 HTTP 4xx/5xx 只出现在请求体不合法 / 未捕获异常（如 USAGE / INTERNAL_ERROR）。
// 两种形态都在此统一成 ApiError，让上层只需 catch 一种类型。

import { apiUrl } from './config'
import type { ErrorEnvelope } from './types'

// ---------------------------------------------------------------------------
// 错误码 → 中文说明（§3.10 / §5.4 / §3.11.5 及前端网络层自有码）
// ---------------------------------------------------------------------------

export const ERROR_CODE_TEXT: Record<string, string> = {
  // 通用 / 用法
  USAGE: '参数或用法不合法',
  BAD_REQUEST: '请求不合法',
  INTERNAL_ERROR: '服务端内部错误',
  NOT_FOUND: '资源不存在',

  // 前端网络层自有码
  NETWORK_ERROR: '网络不可达',
  TIMEOUT: '请求超时',
  // 前端本地取消（调用方主动 abort）：与远端 ABORTED 语义无关。
  REQUEST_CANCELLED: '请求已取消',
  // 远端失败信封里的 ABORTED：任务已被中止，是终态、没有结果可取。
  ABORTED: '任务已中止',
  PARSE_ERROR: '响应不是合法 JSON',

  // 对话 / 节点 / 任务（§3.2–§3.9）
  CONVERSATION_NOT_FOUND: '对话不存在',
  NODE_NOT_FOUND: '节点不存在或不属于该对话',
  TASK_NOT_FOUND: '任务不存在',
  TASK_NOT_FINISHED: '任务尚未产出结果',
  TASK_FAILED: '任务失败',
  VERSION_CONFLICT: '数据版本冲突',
  DEPENDENCY_MISSING: '前置依赖缺失',
  TASK_ALREADY_RUNNING: '已有任务正在运行',

  // 工具 / 策略（§3.3）
  UNKNOWN_TOOL: '未注册的工具名',
  POLICY_REJECTED: '被策略拒绝',

  // 大模型层
  NOT_CONFIGURED: '大模型未配置（enabled / api_base / api_key / model）',
  LLM_UNSUPPORTED: '当前后端未启用大模型层',
  LLM_DISABLED: '大模型已被关闭',
  LLM_NOT_CONFIGURED: '缺少 api_base / api_key / model',
  LLM_UNAVAILABLE: '大模型不可用',
  LLM_UNREACHABLE: '大模型不可达',

  // 运行时 / MCP（§5.4）
  RUNTIME_UNAVAILABLE: '运行时不可用',
  MCP_UNAVAILABLE: 'MCP 客户端未接入或不可达',
  CONFIG_INVALID: '配置不合法',

  // driver 传输层（§5.4）
  DRIVER_NO_OUTPUT: 'driver 无输出',
  DRIVER_BAD_OUTPUT: 'driver 输出非法',
  DRIVER_UNREACHABLE: '无法连接执行端',
  DRIVER_TIMEOUT: '执行端响应超时',

  // 资源与文件（§3.11.5）
  UNSUPPORTED_FILE: '不支持的文件类型（原始数据仅 .dat/.zip，方案仅 .json）',
  FILE_NOT_FOUND: '文件不存在',
  FORBIDDEN_PATH: '路径越界',
  UPLOAD_TOO_LARGE: '上传体积超过上限',
  SCHEME_EXISTS: '同名方案已存在',

  // 远端能力服务扩展码（迭代一重构 §5）
  CONTENT_TOO_LARGE: '文件太大，无法在界面预览',
  NOT_PREVIEWABLE: '该文件不支持在线预览',
  PRODUCT_INVALID: '产物内容异常',
  CANCEL_FAILED: '中止未生效',
  UNAUTHORIZED: '连接凭据不正确',
  FORBIDDEN_HOST: '访问地址不被允许',
  WAIT_LIMIT_REACHED: '当前等待名额已满',

  // 迭代二（机器学习）契约 §5：MODEL_NOT_FOUND 为加性新增；
  // 其余 ML 相关码（DEPENDENCY_MISSING / TASK_ALREADY_RUNNING / USAGE /
  // TASK_FAILED / WAIT_LIMIT_REACHED / ABORTED / UNAUTHORIZED / FORBIDDEN_HOST）沿用上方定义。
  MODEL_NOT_FOUND: '模型编号不存在',
}

/** 把错误码翻成中文；未知码给可读兜底（保留原码便于排查）。 */
export function errorCodeText(code: string | null | undefined): string {
  if (!code) return '未知错误'
  const hit = ERROR_CODE_TEXT[code]
  if (hit) return hit
  if (code.startsWith('HTTP_')) return `请求失败（HTTP ${code.slice(5)}）`
  return `未知错误（${code}）`
}

// ---------------------------------------------------------------------------
// ApiError
// ---------------------------------------------------------------------------

export interface ApiErrorOptions {
  detail?: Record<string, unknown>
  httpStatus?: number
  cause?: unknown
}

/** 数据层统一错误：带结构化错误码与中文说明。 */
export class ApiError extends Error {
  readonly code: string
  readonly detail?: Record<string, unknown>
  readonly httpStatus?: number

  constructor(code: string, message?: string, options: ApiErrorOptions = {}) {
    super(message ?? errorCodeText(code))
    this.name = 'ApiError'
    this.code = code
    this.detail = options.detail
    this.httpStatus = options.httpStatus
    if (options.cause !== undefined) this.cause = options.cause
  }

  /** 取错误信封（与原后端形状一致），便于直接展示或回传。 */
  toEnvelope(): ErrorEnvelope {
    return { code: this.code, message: this.message, ...(this.detail ?? {}) }
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

// ---------------------------------------------------------------------------
// 请求参数
// ---------------------------------------------------------------------------

export type QueryValue = string | number | boolean | null | undefined

export interface QueryParams {
  [key: string]: QueryValue
}

export interface HttpOptions {
  method?: string
  query?: QueryParams
  body?: unknown
  headers?: Record<string, string>
  /** 毫秒；<=0 或缺省表示默认超时（DEFAULT_TIMEOUT_MS）。 */
  timeoutMs?: number
  signal?: AbortSignal
}

export const DEFAULT_TIMEOUT_MS = 30_000

const ENVELOPE_LIKE = (value: unknown): value is { ok?: boolean; error?: unknown } =>
  typeof value === 'object' && value !== null

export function buildUrl(path: string, query?: QueryParams): string {
  const base = apiUrl(path)
  if (!query) return base
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue
    search.append(key, String(value))
  }
  const qs = search.toString()
  return qs ? `${base}?${qs}` : base
}

// ---------------------------------------------------------------------------
// 底层 fetch（含超时与外部取消）
// ---------------------------------------------------------------------------

async function doFetch(
  url: string,
  init: RequestInit,
  timeoutMs: number | undefined,
  externalSignal: AbortSignal | undefined,
): Promise<Response> {
  const controller = new AbortController()
  let timedOut = false

  const onAbort = () => controller.abort()
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort()
    else externalSignal.addEventListener('abort', onAbort, { once: true })
  }

  const effectiveTimeout =
    timeoutMs === undefined ? DEFAULT_TIMEOUT_MS : timeoutMs
  let timer: ReturnType<typeof setTimeout> | undefined
  if (effectiveTimeout > 0) {
    timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, effectiveTimeout)
  }

  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } catch (err) {
    if (timedOut) {
      throw new ApiError('TIMEOUT', errorCodeText('TIMEOUT'), { cause: err })
    }
    if (controller.signal.aborted) {
      throw new ApiError(
        'REQUEST_CANCELLED',
        errorCodeText('REQUEST_CANCELLED'),
        { cause: err },
      )
    }
    throw new ApiError('NETWORK_ERROR', errorCodeText('NETWORK_ERROR'), {
      cause: err,
    })
  } finally {
    if (timer) clearTimeout(timer)
    if (externalSignal) externalSignal.removeEventListener('abort', onAbort)
  }
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let text = ''
  try {
    text = await res.text()
  } catch {
    /* 读不到就按状态码兜底 */
  }
  if (text) {
    try {
      const parsed: unknown = JSON.parse(text)
      if (ENVELOPE_LIKE(parsed)) {
        const err = (parsed as { error?: unknown }).error
        if (ENVELOPE_LIKE(err) && typeof (err as ErrorEnvelope).code === 'string') {
          const envelope = err as ErrorEnvelope
          return new ApiError(envelope.code, envelope.message, {
            detail: envelope,
            httpStatus: res.status,
          })
        }
      }
    } catch {
      /* 非 JSON，走状态码兜底 */
    }
  }
  return new ApiError(`HTTP_${res.status}`, text || undefined, {
    httpStatus: res.status,
  })
}

function assertOk(body: unknown, res: Response): void {
  if (!ENVELOPE_LIKE(body)) return
  const err = (body as { error?: unknown }).error
  if (ENVELOPE_LIKE(err) && typeof (err as ErrorEnvelope).code === 'string') {
    const envelope = err as ErrorEnvelope
    throw new ApiError(envelope.code, envelope.message, {
      detail: envelope,
      httpStatus: res.status,
    })
  }
}

// ---------------------------------------------------------------------------
// 高层请求
// ---------------------------------------------------------------------------

/** 发 JSON 请求并把响应解析为信封类型；失败抛 ApiError。 */
export async function requestJson<T extends object>(
  path: string,
  options: HttpOptions = {},
): Promise<T> {
  const url = buildUrl(path, options.query)
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers ?? {}),
  }
  const init: RequestInit = { method: options.method ?? 'GET', headers }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json; charset=utf-8'
    init.body = JSON.stringify(options.body)
  }

  const res = await doFetch(url, init, options.timeoutMs, options.signal)
  if (!res.ok) throw await errorFromResponse(res)

  let body: unknown
  try {
    body = await res.json()
  } catch (err) {
    throw new ApiError('PARSE_ERROR', errorCodeText('PARSE_ERROR'), {
      httpStatus: res.status,
      cause: err,
    })
  }
  assertOk(body, res)
  return body as T
}

export function getJson<T extends object>(
  path: string,
  options: Omit<HttpOptions, 'method' | 'body'> = {},
): Promise<T> {
  return requestJson<T>(path, { ...options, method: 'GET' })
}

export function postJson<T extends object>(
  path: string,
  body?: unknown,
  options: Omit<HttpOptions, 'method' | 'body'> = {},
): Promise<T> {
  return requestJson<T>(path, { ...options, method: 'POST', body })
}

/** 发请求并把响应当纯文本读（如 §3.11 driver 的 report md）；失败抛 ApiError。 */
export async function requestText(
  path: string,
  options: HttpOptions = {},
): Promise<string> {
  const url = buildUrl(path, options.query)
  const init: RequestInit = {
    method: options.method ?? 'GET',
    headers: { Accept: 'text/plain, */*', ...(options.headers ?? {}) },
  }
  const res = await doFetch(url, init, options.timeoutMs, options.signal)
  if (!res.ok) throw await errorFromResponse(res)
  return res.text()
}

/**
 * 发 multipart/form-data 请求（§3.11 上传）。
 * 不设置 Content-Type —— 交给浏览器补齐 boundary。
 */
export async function requestForm<T extends object>(
  path: string,
  form: FormData,
  options: Omit<HttpOptions, 'method' | 'body' | 'headers'> = {},
): Promise<T> {
  const url = buildUrl(path, options.query)
  const init: RequestInit = {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: form,
  }
  const res = await doFetch(url, init, options.timeoutMs, options.signal)
  if (!res.ok) throw await errorFromResponse(res)

  let body: unknown
  try {
    body = await res.json()
  } catch (err) {
    throw new ApiError('PARSE_ERROR', errorCodeText('PARSE_ERROR'), {
      httpStatus: res.status,
      cause: err,
    })
  }
  assertOk(body, res)
  return body as T
}
