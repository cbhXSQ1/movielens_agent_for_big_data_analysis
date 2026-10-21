// FB.5 / FB.6 · 资源与文件端点封装（§3.11，直通 REST，不经 LLM）。
// 方案上传用原生 FormData：字段 `file` + `name?`；经 Agent 转发到远端能力服务落盘。
// 非 2xx 与结构化错误码由 http.ts 统一转成 ApiError（UNSUPPORTED_FILE / UPLOAD_TOO_LARGE /
// FILE_NOT_FOUND / FORBIDDEN_PATH / SCHEME_EXISTS 等，见 ERROR_CODE_TEXT）。

import { buildUrl, getJson, postJson, requestForm, type HttpOptions, type QueryParams } from './http'
import type {
  FilePreviewResponse,
  PublishedListResponse,
  RawListResponse,
  SchemeContentResponse,
  SchemeDeleteResponse,
  SchemeListResponse,
  SchemeUploadResponse,
  TaskFilesResponse,
  ValidationResult,
} from './types'

// ---------------------------------------------------------------------------
// §3.11.1 原始数据
// ---------------------------------------------------------------------------

export function listRawData(
  options: HttpOptions = {},
): Promise<RawListResponse> {
  return getJson<RawListResponse>('/api/data/raw', options)
}

// ---------------------------------------------------------------------------
// §3.11.2 清洗后数据（已发布）
// ---------------------------------------------------------------------------

export function listPublishedData(
  options: HttpOptions = {},
): Promise<PublishedListResponse> {
  return getJson<PublishedListResponse>('/api/data/published', options)
}

// ---------------------------------------------------------------------------
// §3.11.3 方案文件
// ---------------------------------------------------------------------------

export function listSchemes(
  options: HttpOptions = {},
): Promise<SchemeListResponse> {
  return getJson<SchemeListResponse>('/api/schemes', options)
}

/** 取方案完整 JSON 内容（供「评分依据」渲染）。文件不存在 → FILE_NOT_FOUND。 */
export function getSchemeContent(
  name: string,
  options: HttpOptions = {},
): Promise<SchemeContentResponse> {
  return getJson<SchemeContentResponse>('/api/schemes/content', {
    ...options,
    query: { name, ...(options.query ?? {}) },
  })
}

/**
 * §3.11.3 上传方案（multipart，单个 `.json`），经 Agent 转发到远端能力服务落盘。
 * 校验不过时返回 `ok=false` 但仍带 `saved`，调用方据 `validation` 展示明细；
 * 回执另带 `scheme`/`overwritten`/`size_bytes`（同名未允许覆盖 → SCHEME_EXISTS）。
 */
export function uploadScheme(
  file: File,
  name?: string,
  options: Omit<HttpOptions, 'method' | 'body' | 'headers'> = {},
): Promise<SchemeUploadResponse> {
  const form = new FormData()
  form.append('file', file, name || file.name)
  if (name) form.append('name', name)
  return requestForm<SchemeUploadResponse>('/api/schemes/upload', form, options)
}

/**
 * §3.11.3 删除自定义方案（JSON body `{name}`），经 Agent 转发到远端能力服务。
 * 默认方案永不可删（USAGE）；有任务在跑 → TASK_ALREADY_RUNNING（带 occupier）；
 * 不存在 → FILE_NOT_FOUND（带 existing）；越界 → FORBIDDEN_PATH。
 */
export function deleteScheme(
  name: string,
  options: Omit<HttpOptions, 'method' | 'body'> = {},
): Promise<SchemeDeleteResponse> {
  return postJson<SchemeDeleteResponse>('/api/schemes/delete', { name }, options)
}

/** §3.11.3 校验配置（复用既有 /api/validate）。 */
export function validateConfig(
  rules?: string,
  scoring?: string,
  options: HttpOptions = {},
): Promise<ValidationResult> {
  const body: Record<string, string> = {}
  if (rules) body.rules = rules
  if (scoring) body.scoring = scoring
  return postJson<ValidationResult>('/api/validate', body, options)
}

// ---------------------------------------------------------------------------
// §3.11.4 产出文件
// ---------------------------------------------------------------------------

export function listTaskFiles(
  taskId: string,
  options: HttpOptions = {},
): Promise<TaskFilesResponse> {
  return getJson<TaskFilesResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/files`,
    options,
  )
}

/** 预览产出文件（仅 json/md/text 返回内容，其余 previewable:false）。 */
export function previewFile(
  taskId: string,
  path: string,
  options: HttpOptions = {},
): Promise<FilePreviewResponse> {
  return getJson<FilePreviewResponse>('/api/files/preview', {
    ...options,
    query: { task_id: taskId, path, ...(options.query ?? {}) },
  })
}

function fileQuery(taskId: string, path: string): QueryParams {
  return { task_id: taskId, path }
}

/** 下载地址（`Content-Disposition: attachment`）；视图层可直接挂到 <a download>。 */
export function fileDownloadUrl(taskId: string, path: string): string {
  return buildUrl('/api/files/download', fileQuery(taskId, path))
}
