// 大模型供应商档案：长期保存在后端，同名保存覆盖旧档案。
// 仅做「路径 + 方法 + 类型」薄封装，统一经 http.ts 解包与错误处理。

import { getJson, postJson, requestJson, type HttpOptions } from './http'
import type {
  LlmProviderDeleteResponse,
  LlmProviderInput,
  LlmProviderListResponse,
  LlmProviderSaveResponse,
} from './types'

export function listProviders(
  options: HttpOptions = {},
): Promise<LlmProviderListResponse> {
  return getJson<LlmProviderListResponse>('/api/llm/providers', options)
}

export function saveProvider(
  payload: LlmProviderInput,
  options: HttpOptions = {},
): Promise<LlmProviderSaveResponse> {
  return postJson<LlmProviderSaveResponse>(
    '/api/llm/providers',
    payload,
    options,
  )
}

export function deleteProvider(
  name: string,
  options: HttpOptions = {},
): Promise<LlmProviderDeleteResponse> {
  return requestJson<LlmProviderDeleteResponse>(
    `/api/llm/providers/${encodeURIComponent(name)}`,
    { ...options, method: 'DELETE' },
  )
}
