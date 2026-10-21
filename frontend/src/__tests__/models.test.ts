// FE-ML-2 · 模型端点封装（契约 §3.5 / §3.7 / §4.1，直通 REST，不经 LLM）。
// listModels → GET /api/models；getModel → GET /api/models/{id}；
// modelDownloadUrl → /api/models/download?model_id=&path=（Agent 中转，同既有下载基址）。
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  getModel,
  listModels,
  modelDownloadUrl,
  previewModelArtifact,
} from '@/api/models'
import { API_BASE } from '@/api/config'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function textResponse(body: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('FE-ML-2 · 模型端点', () => {
  it('listModels GET /api/models 并取回 models', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      jsonResponse({
        ok: true,
        models: [
          {
            model_id: 'ml-20261018-103000-a1b2',
            task_type: 'classification',
            algorithm: 'random_forest',
            data_version: 'ml1m-clean-v1',
            created_at: '2026-10-18 10:30:00',
            metrics_summary: { f1: 0.78, auc: 0.82 },
          },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const res = await listModels()

    const [url, init] = fetchMock.mock.calls[0]
    expect(new URL(String(url)).pathname).toBe('/api/models')
    expect((init as RequestInit).method).toBe('GET')
    expect(res.models).toHaveLength(1)
    expect(res.models[0].model_id).toBe('ml-20261018-103000-a1b2')
  })

  it('getModel GET /api/models/{id} 并对编号做 URL 编码', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      jsonResponse({
        ok: true,
        model: { model_id: 'ml 1', artifacts: [], metrics: {} },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const res = await getModel('ml 1')

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(`${API_BASE}/api/models/ml%201`)
    expect((init as RequestInit).method).toBe('GET')
    expect(res.model.model_id).toBe('ml 1')
  })

  it('modelDownloadUrl 指向 Agent 中转 /api/models/download，带 model_id 与 path', () => {
    const url = new URL(modelDownloadUrl('ml-20261018-103000-a1b2', 'sub/coords.csv'))
    expect(url.origin + url.pathname).toBe(`${API_BASE}/api/models/download`)
    expect(url.searchParams.get('model_id')).toBe('ml-20261018-103000-a1b2')
    expect(url.searchParams.get('path')).toBe('sub/coords.csv')
  })

  it('previewModelArtifact 用同一模型下载地址取文本', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      textResponse('{"clusters": 4}'),
    )
    vi.stubGlobal('fetch', fetchMock)

    const text = await previewModelArtifact('ml-1', 'profile.json')

    const [url] = fetchMock.mock.calls[0]
    const u = new URL(String(url))
    expect(u.pathname).toBe('/api/models/download')
    expect(u.searchParams.get('model_id')).toBe('ml-1')
    expect(u.searchParams.get('path')).toBe('profile.json')
    expect(text).toBe('{"clusters": 4}')
  })
})
