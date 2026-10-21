import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  ApiError,
  buildUrl,
  errorCodeText,
  isApiError,
  postJson,
  requestJson,
} from '@/api/http'
import { API_BASE } from '@/api/config'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('http · 错误码→中文', () => {
  it('已知码给中文说明', () => {
    expect(errorCodeText('CONVERSATION_NOT_FOUND')).toContain('对话')
    expect(errorCodeText('NODE_NOT_FOUND')).toContain('节点')
    expect(errorCodeText('UNKNOWN_TOOL')).toContain('工具')
    expect(errorCodeText('POLICY_REJECTED')).toContain('策略')
    expect(errorCodeText('UPLOAD_TOO_LARGE')).toContain('上限')
    expect(errorCodeText('UNSUPPORTED_FILE')).toContain('.dat/.zip')
    expect(errorCodeText('FORBIDDEN_PATH')).toContain('路径越界')
    expect(errorCodeText('TASK_ALREADY_RUNNING')).toContain('运行')
    expect(errorCodeText('DRIVER_TIMEOUT')).toContain('超时')
    expect(errorCodeText('MCP_UNAVAILABLE')).toContain('MCP')
    expect(errorCodeText('RUNTIME_UNAVAILABLE')).toContain('运行时')
    expect(errorCodeText('LLM_NOT_CONFIGURED')).toContain('api_base')
  })

  it('本地取消码与远端中止码语义分离', () => {
    expect(errorCodeText('REQUEST_CANCELLED')).toContain('已取消')
    expect(errorCodeText('ABORTED')).toContain('任务已中止')
    expect(errorCodeText('REQUEST_CANCELLED')).not.toContain('任务已中止')
  })

  it('远端新增错误码有自然中文说明', () => {
    expect(errorCodeText('ABORTED')).toContain('已中止')
    expect(errorCodeText('FILE_NOT_FOUND')).toContain('不存在')
    expect(errorCodeText('FORBIDDEN_PATH')).toContain('路径越界')
    expect(errorCodeText('CONTENT_TOO_LARGE')).toContain('太大')
    expect(errorCodeText('NOT_PREVIEWABLE')).toContain('预览')
    expect(errorCodeText('PRODUCT_INVALID')).toContain('产物')
    expect(errorCodeText('CANCEL_FAILED')).toContain('中止')
    expect(errorCodeText('UNAUTHORIZED')).toContain('凭据')
    expect(errorCodeText('FORBIDDEN_HOST')).toContain('地址')
    expect(errorCodeText('WAIT_LIMIT_REACHED')).toContain('等待')
    expect(errorCodeText('INTERNAL_ERROR')).toContain('内部错误')
    expect(errorCodeText('DRIVER_UNREACHABLE')).toContain('执行端')
    expect(errorCodeText('DRIVER_TIMEOUT')).toContain('超时')
    expect(errorCodeText('TASK_NOT_FINISHED')).toContain('尚未')
  })

  it('方案重名码给出自然中文（同名已存在）', () => {
    const text = errorCodeText('SCHEME_EXISTS')
    expect(text).toContain('同名方案已存在')
  })

  it('未知码保留原码兜底', () => {
    expect(errorCodeText('SOME_NEW_CODE')).toContain('SOME_NEW_CODE')
    expect(errorCodeText('HTTP_503')).toContain('503')
    expect(errorCodeText(undefined)).toBe('未知错误')
  })
})

describe('http · buildUrl', () => {
  it('用 apiUrl 拼接，不硬编码主机名', () => {
    expect(buildUrl('/api/tools')).toBe(`${API_BASE}/api/tools`)
  })

  it('query 正确编码且跳过空值', () => {
    const url = new URL(
      buildUrl('/api/files/preview', {
        task_id: 't 1',
        path: 'cleaned/a b.json',
        skip: undefined,
        gone: null,
      }),
    )
    expect(url.pathname).toBe('/api/files/preview')
    expect(url.searchParams.get('task_id')).toBe('t 1')
    expect(url.searchParams.get('path')).toBe('cleaned/a b.json')
    expect(url.searchParams.has('skip')).toBe(false)
    expect(url.searchParams.has('gone')).toBe(false)
  })
})

describe('http · 解包与错误', () => {
  it('成功响应原样返回', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ ok: true, conversations: [] })),
    )
    const res = await requestJson<{ ok: boolean; conversations: unknown[] }>(
      '/api/conversations',
    )
    expect(res.ok).toBe(true)
    expect(res.conversations).toEqual([])
  })

  it('HTTP 200 + ok:false + error → 抛 ApiError（含码与中文）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          ok: false,
          error: { code: 'USAGE', message: 'mode 必须是 offline 或 llm' },
        }),
      ),
    )
    let caught: unknown
    try {
      await requestJson('/api/conversations/c1/messages')
    } catch (e) {
      caught = e
    }
    expect(isApiError(caught)).toBe(true)
    expect((caught as ApiError).code).toBe('USAGE')
    expect((caught as ApiError).message).toBe('mode 必须是 offline 或 llm')
  })

  it('ok:false 但无 error（失败任务结果）不抛错', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ ok: false, task: {}, blocks: {} })),
    )
    const res = await requestJson<{ ok: boolean }>('/x')
    expect(res.ok).toBe(false)
  })

  it('非 2xx 且带错误信封 → 取信封里的码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { ok: false, error: { code: 'INTERNAL_ERROR', message: 'boom' } },
          500,
        ),
      ),
    )
    await expect(requestJson('/x')).rejects.toMatchObject({
      code: 'INTERNAL_ERROR',
    })
  })

  it('fetch 抛错 → NETWORK_ERROR', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('boom')
      }),
    )
    await expect(requestJson('/x')).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
    })
  })

  it('超时 → TIMEOUT', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          }),
      ),
    )
    await expect(requestJson('/x', { timeoutMs: 10 })).rejects.toMatchObject({
      code: 'TIMEOUT',
    })
  })

  it('外部 signal 取消 → REQUEST_CANCELLED（不是远端 ABORTED）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          }),
      ),
    )
    const controller = new AbortController()
    const pending = requestJson('/x', { signal: controller.signal })
    controller.abort()
    await expect(pending).rejects.toMatchObject({
      code: 'REQUEST_CANCELLED',
    })
  })

  it('postJson 发 JSON 体并带 Content-Type', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) => jsonResponse({ ok: true }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await postJson('/api/conversations', { title: '新对话' })
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ title: '新对话' }))
    expect(
      (init.headers as Record<string, string>)['Content-Type'],
    ).toContain('application/json')
  })
})
