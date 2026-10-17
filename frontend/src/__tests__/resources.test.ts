import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  fileDownloadUrl,
  getSchemeContent,
  listTaskFiles,
  previewFile,
  uploadScheme,
} from '@/api/resources'
import { API_BASE } from '@/api/config'
import { ApiError, errorCodeText } from '@/api/http'
import { describeError } from '@/components/resources/resourceUtils'

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

describe('resources · 上传构造 FormData', () => {
  it('方案：file 字段 + 可选 name', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        jsonResponse({
          ok: true,
          saved: { name: 'scoring_scheme.v1.json', path: 'config/x' },
          validation: { ok: true },
        }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const file = new File(['{}'], 'scoring_scheme.v1.json')
    await uploadScheme(file, 'scoring_scheme.v1.json')

    const [, init] = fetchMock.mock.calls[0]
    const form = init.body as FormData
    expect((form.get('file') as File).name).toBe('scoring_scheme.v1.json')
    expect(form.get('name')).toBe('scoring_scheme.v1.json')
  })

  it('上传非 2xx/错误码 → 抛 ApiError 码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { ok: false, error: { code: 'UPLOAD_TOO_LARGE', message: '太大' } },
          200,
        ),
      ),
    )
    await expect(
      uploadScheme(new File(['x'], 'big.json'), 'big'),
    ).rejects.toMatchObject({ code: 'UPLOAD_TOO_LARGE' })
  })
})

describe('resources · 查询与文件', () => {
  it('previewFile 带 task_id / path 查询参数', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        jsonResponse({ ok: true, path: 'a.json', previewable: true, content: '{}' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await previewFile('t1', 'cleaned/a.json')
    const [url] = fetchMock.mock.calls[0]
    const u = new URL(String(url))
    expect(u.pathname).toBe('/api/files/preview')
    expect(u.searchParams.get('task_id')).toBe('t1')
    expect(u.searchParams.get('path')).toBe('cleaned/a.json')
  })

  it('listTaskFiles 拼接任务路径', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        jsonResponse({ ok: true, task_id: 't1', files: [] }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await listTaskFiles('t1')
    const [url] = fetchMock.mock.calls[0]
    expect(String(url)).toBe(`${API_BASE}/api/tasks/t1/files`)
  })

  it('getSchemeContent 编码 name 参数', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init: RequestInit) =>
        jsonResponse({ ok: true, name: 's.json', path: 'config/s.json', content: {} }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await getSchemeContent('scoring scheme.v1.json')
    const [url] = fetchMock.mock.calls[0]
    const u = new URL(String(url))
    expect(u.searchParams.get('name')).toBe('scoring scheme.v1.json')
  })

  it('fileDownloadUrl 给出下载地址（不硬编码主机名）', () => {
    const url = new URL(fileDownloadUrl('t1', 'scores.json'))
    expect(url.origin + url.pathname).toBe(`${API_BASE}/api/files/download`)
    expect(url.searchParams.get('task_id')).toBe('t1')
    expect(url.searchParams.get('path')).toBe('scores.json')
  })
})

// ---------------------------------------------------------------------------
// FT.3 · 上传形态与错误码路径（假后端，§3.11.1 / §3.11.3 / §3.11.5）
// ---------------------------------------------------------------------------

describe('FT.3 · 上传形态与错误码路径', () => {
  it('UPLOAD_TOO_LARGE：超限被如实拒绝', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { ok: false, error: { code: 'UPLOAD_TOO_LARGE', message: '太大' } },
          200,
        ),
      ),
    )
    await expect(
      uploadScheme(new File(['x'], 'big.json'), 'big'),
    ).rejects.toMatchObject({ code: 'UPLOAD_TOO_LARGE' })
  })

  it('FORBIDDEN_PATH：预览越界路径 → 抛该码（HTTP 200 + 错误信封）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { ok: false, error: { code: 'FORBIDDEN_PATH', message: '路径越界' } },
          200,
        ),
      ),
    )
    await expect(
      previewFile('t1', '../../etc/passwd'),
    ).rejects.toMatchObject({ code: 'FORBIDDEN_PATH' })
  })

  it('FILE_NOT_FOUND：取不存在的方案内容 → 抛该码（非 2xx + 错误信封）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { ok: false, error: { code: 'FILE_NOT_FOUND', message: '文件不存在' } },
          404,
        ),
      ),
    )
    await expect(getSchemeContent('missing.json')).rejects.toMatchObject({
      code: 'FILE_NOT_FOUND',
    })
  })

  it('方案上传非 .json → UNSUPPORTED_FILE', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          { ok: false, error: { code: 'UNSUPPORTED_FILE', message: '需 .json' } },
          200,
        ),
      ),
    )
    await expect(
      uploadScheme(new File(['x'], 'notes.txt'), 'notes.txt'),
    ).rejects.toMatchObject({ code: 'UNSUPPORTED_FILE' })
  })
})

describe('FT.3 · 资源错误码 → 中文转述', () => {
  it('四个资源码优先按码表给中文（不被英文 message 覆盖）', () => {
    const codes = [
      'UNSUPPORTED_FILE',
      'UPLOAD_TOO_LARGE',
      'FORBIDDEN_PATH',
      'FILE_NOT_FOUND',
    ]
    for (const code of codes) {
      expect(describeError(new ApiError(code, 'english message'))).toBe(
        errorCodeText(code),
      )
    }
  })

  it('未知码回落到 message', () => {
    expect(describeError(new ApiError('SOME_NEW', '自定义说明'))).toBe('自定义说明')
  })
})
