// FE-ML-2 · 迭代二模型库端点封装（契约 §3.5 / §3.7 / §4.1，直通 REST，不经 LLM）。
// 列模型 / 取模型经 Agent 转发到远端能力服务；产物下载走 Agent 中继
// `/api/models/download?model_id=&path=`（同一口令、流式，与既有两段下载同构）。
// 非 2xx 与结构化错误码（MODEL_NOT_FOUND / FILE_NOT_FOUND / FORBIDDEN_PATH …）
// 由 http.ts 统一转成 ApiError。

import {
  buildUrl,
  getJson,
  requestText,
  type HttpOptions,
  type QueryParams,
} from './http'

/** 产物文件格式（契约 §3.5）：决定能否在线预览。 */
export type MlArtifactKind =
  | 'joblib'
  | 'json'
  | 'csv'
  | 'jsonl'
  | 'md'
  | 'other'
  | string

/** 产物用途（契约 §3.5 / §4.2）：前端按此分组展示。 */
export type MlArtifactRole =
  | 'model'
  | 'profile'
  | 'labels'
  | 'coords'
  | 'meta'
  | string

/** 产物条目（契约 §3.5）：`kind` 是格式、`role` 是用途。 */
export interface MlArtifact {
  id: string
  title: string
  kind: MlArtifactKind
  role: MlArtifactRole
  path: string
  size: number
  [key: string]: unknown
}

/**
 * 模型摘要（契约 §3.7，`list_ml_models` 的每项）。
 * `metrics_summary` 键随任务类型：分类给 `f1`/`auc`，聚类给 `k`/`silhouette`，
 * 降维给 `n_components`/`reconstruction_error`。
 */
export interface MlModelSummary {
  model_id: string
  task_type: string
  algorithm: string
  data_version: string
  created_at: string
  metrics_summary: Record<string, unknown>
  [key: string]: unknown
}

/** 模型详情（契约 §3.7，`get_ml_model`）：字段与 §3.1 对齐。 */
export interface MlModelDetail extends MlModelSummary {
  metrics?: Record<string, unknown>
  artifacts?: MlArtifact[]
  params?: Record<string, unknown>
  time_boundaries?: Record<string, unknown>
  [key: string]: unknown
}

export interface ModelListResponse {
  ok?: boolean
  models: MlModelSummary[]
  [key: string]: unknown
}

export interface ModelDetailResponse {
  ok?: boolean
  model: MlModelDetail
  [key: string]: unknown
}

/** §3.7 列模型（`GET /api/models`）。 */
export function listModels(
  options: HttpOptions = {},
): Promise<ModelListResponse> {
  return getJson<ModelListResponse>('/api/models', options)
}

/** §3.7 取模型（`GET /api/models/{id}`），编号做 URL 编码。 */
export function getModel(
  id: string,
  options: HttpOptions = {},
): Promise<ModelDetailResponse> {
  return getJson<ModelDetailResponse>(
    `/api/models/${encodeURIComponent(id)}`,
    options,
  )
}

function modelQuery(modelId: string, path: string): QueryParams {
  return { model_id: modelId, path }
}

/**
 * 模型产物下载地址（契约 §4.1，Agent 中继）。
 * 视图层可直接挂到 `<a download>`；口令由 Agent 侧注入，不进地址。
 */
export function modelDownloadUrl(modelId: string, path: string): string {
  return buildUrl('/api/models/download', modelQuery(modelId, path))
}

/**
 * 预览模型产物：用同一模型下载地址取文本（仅 `json`/`csv`/`jsonl`/`md` 等文本格式；
 * `joblib` 等二进制由调用方拦下，不做在线打开）。
 */
export function previewModelArtifact(
  modelId: string,
  path: string,
  options: HttpOptions = {},
): Promise<string> {
  return requestText('/api/models/download', {
    ...options,
    query: { ...modelQuery(modelId, path), ...(options.query ?? {}) },
  })
}
