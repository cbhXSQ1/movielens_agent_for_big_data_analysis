// §3.3 发消息 · 思考档位（reasoning_effort）透传到请求体顶层。
// 直接桩掉 fetch，检查真正 POST 出去的 JSON body（最贴近契约的一层）。
import { describe, it, expect, vi, afterEach } from 'vitest'
import { sendByMode, sendLlm } from '@/api/endpoints'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

interface FetchCall {
  mock: { calls: [string, RequestInit][] }
}

function bodyOf(fetchMock: FetchCall): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0]
  return JSON.parse(String(init.body)) as Record<string, unknown>
}

function stubFetch(): FetchCall {
  const fetchMock = vi.fn(
    async (_url: string, _init: RequestInit) =>
      jsonResponse({ ok: true, engine: 'llm', conversation_id: 'c1' }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock as unknown as FetchCall
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('§3.3 发消息 · 思考档位透传', () => {
  it('llm 模式带 reasoning_effort → 请求体顶层含该字段', async () => {
    const fetchMock = stubFetch()
    await sendByMode('c1', 'llm', { text: 'hi', reasoning_effort: 'high' })

    const body = bodyOf(fetchMock)
    expect(body.mode).toBe('llm')
    expect(body.text).toBe('hi')
    expect(body.reasoning_effort).toBe('high')
  })

  it('llm 模式缺省（默认档）→ 请求体不带 reasoning_effort', async () => {
    const fetchMock = stubFetch()
    await sendByMode('c1', 'llm', { text: 'hi' })

    expect(bodyOf(fetchMock)).not.toHaveProperty('reasoning_effort')
  })

  it('offline 模式即便传了 reasoning_effort 也不透传', async () => {
    const fetchMock = stubFetch()
    await sendByMode('c1', 'offline', {
      tool: 'get_samples',
      reasoning_effort: 'high',
    })

    const body = bodyOf(fetchMock)
    expect(body.mode).toBe('offline')
    expect(body.tool).toBe('get_samples')
    expect(body).not.toHaveProperty('reasoning_effort')
  })

  it('sendLlm 的 reasoningEffort 参数进请求体顶层', async () => {
    const fetchMock = stubFetch()
    await sendLlm('c1', 'hi', {}, 'medium')

    expect(bodyOf(fetchMock).reasoning_effort).toBe('medium')
  })

  it('sendLlm 不传 reasoningEffort 时不带该字段', async () => {
    const fetchMock = stubFetch()
    await sendLlm('c1', 'hi')

    expect(bodyOf(fetchMock)).not.toHaveProperty('reasoning_effort')
  })
})
