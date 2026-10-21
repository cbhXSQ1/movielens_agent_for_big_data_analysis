import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  deleteProvider,
  listProviders,
  saveProvider,
} from '@/api/llmProviders'
import { API_BASE } from '@/api/config'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

const saved = {
  name: '工作机',
  api_base: 'http://llm.local/v1',
  api_key: 'sk-test',
  model: 'qwen',
  mode: 'llm',
  updated_at: 1730000000,
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('LLM 供应商档案端点', () => {
  it('listProviders GET /api/llm/providers', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      jsonResponse({ ok: true, providers: [saved] }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const res = await listProviders()

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(`${API_BASE}/api/llm/providers`)
    expect((init as RequestInit).method).toBe('GET')
    expect(res.providers).toEqual([saved])
  })

  it('saveProvider POST 名称与连接字段', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      jsonResponse({ ok: true, provider: saved }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const payload = {
      name: '工作机',
      api_base: 'http://llm.local/v1',
      api_key: 'sk-test',
      model: 'qwen',
      mode: 'llm',
    }
    const res = await saveProvider(payload)

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(`${API_BASE}/api/llm/providers`)
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).body).toBe(JSON.stringify(payload))
    expect(
      ((init as RequestInit).headers as Record<string, string>)['Content-Type'],
    ).toContain('application/json')
    expect(res.provider.name).toBe('工作机')
  })

  it('deleteProvider DELETE 且名称做 URL 编码', async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      jsonResponse({ ok: true, deleted: true }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await deleteProvider('工作 机')

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(
      `${API_BASE}/api/llm/providers/${encodeURIComponent('工作 机')}`,
    )
    expect((init as RequestInit).method).toBe('DELETE')
  })
})
