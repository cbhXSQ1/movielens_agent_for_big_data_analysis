import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listRawData: vi.fn(),
  listPublishedData: vi.fn(),
  listSchemes: vi.fn(),
  uploadScheme: vi.fn(),
  deleteScheme: vi.fn(),
  listTaskFiles: vi.fn(),
  previewFile: vi.fn(),
  fileDownloadUrl: vi.fn(),
}))

vi.mock('@/api/resources', () => api)

import { useResourcesStore } from '@/stores/resources'

beforeEach(() => {
  setActivePinia(createPinia())
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('resources · 原始数据', () => {
  it('loadRaw 填充目录与数据集', async () => {
    api.listRawData.mockResolvedValue({
      ok: true,
      dir: '/raw',
      datasets: [
        { name: 'ml-1m', path: 'ml-1m', files: [], size: 1, updated_at: 0 },
      ],
    })
    const store = useResourcesStore()
    await store.loadRaw()
    expect(store.rawDir).toBe('/raw')
    expect(store.rawCount).toBe(1)
  })

  it('没有原始数据上传动作（远端不提供上传）', () => {
    const store = useResourcesStore()
    expect('uploadRaw' in store).toBe(false)
  })
})

describe('resources · 已发布 / 方案 / 文件', () => {
  it('loadPublished 填充已发布版本', async () => {
    api.listPublishedData.mockResolvedValue({
      ok: true,
      datasets: [
        {
          data_version: 'ml1m-clean-v1',
          source_task_id: 't',
          path: 'p',
          tables: [],
          published_at: 0,
        },
      ],
    })
    const store = useResourcesStore()
    await store.loadPublished()
    expect(store.publishedCount).toBe(1)
    expect(store.published[0].data_version).toBe('ml1m-clean-v1')
  })

  it('uploadScheme 后重新列方案（就地刷新）', async () => {
    api.uploadScheme.mockResolvedValue({
      ok: true,
      saved: { name: 'scoring_scheme.v1.json', path: 'config/x' },
      validation: { ok: true },
    })
    api.listSchemes.mockResolvedValue({
      ok: true,
      schemes: [{ name: 'scoring_scheme.v1.json' }],
    })
    const store = useResourcesStore()
    const res = await store.uploadScheme(
      new File(['{}'], 'scoring_scheme.v1.json'),
    )
    expect(res.validation).toEqual({ ok: true })
    expect(api.listSchemes).toHaveBeenCalledTimes(1)
    expect(store.schemeCount).toBe(1)
    expect(store.schemes[0].name).toBe('scoring_scheme.v1.json')
  })

  it('uploadScheme 透传 overwritten / size_bytes，仍就地重列', async () => {
    api.uploadScheme.mockResolvedValue({
      ok: true,
      saved: { name: 'scoring_scheme.v1.json', path: 'config/x' },
      validation: { ok: true },
      overwritten: true,
      size_bytes: 42,
    })
    api.listSchemes.mockResolvedValue({ ok: true, schemes: [] })

    const store = useResourcesStore()
    const res = await store.uploadScheme(
      new File(['{}'], 'scoring_scheme.v1.json'),
    )

    expect(res.overwritten).toBe(true)
    expect(res.size_bytes).toBe(42)
    expect(api.listSchemes).toHaveBeenCalledTimes(1)
  })

  it('deleteScheme 后重新列方案（就地刷新）', async () => {
    api.deleteScheme.mockResolvedValue({ ok: true, deleted: true })
    api.listSchemes.mockResolvedValue({
      ok: true,
      schemes: [{ name: 'kept.json' }],
    })
    const store = useResourcesStore()
    const res = await store.deleteScheme('my_custom.v1.json')

    expect(api.deleteScheme).toHaveBeenCalledWith('my_custom.v1.json')
    expect(res.deleted).toBe(true)
    expect(api.listSchemes).toHaveBeenCalledTimes(1)
    expect(store.schemeCount).toBe(1)
    expect(store.schemes[0].name).toBe('kept.json')
  })

  it('deleteScheme 失败时抛出，且不刷新列表', async () => {
    api.deleteScheme.mockRejectedValue(
      Object.assign(new Error('busy'), { code: 'TASK_ALREADY_RUNNING' }),
    )
    const store = useResourcesStore()

    await expect(store.deleteScheme('my_custom.v1.json')).rejects.toMatchObject({
      code: 'TASK_ALREADY_RUNNING',
    })
    expect(api.listSchemes).not.toHaveBeenCalled()
  })

  it('loadTaskFiles 缓存产出文件；preview / downloadUrl 透传', async () => {
    api.listTaskFiles.mockResolvedValue({
      ok: true,
      task_id: 't1',
      files: [{ path: 'scores.json', size: 1, kind: 'json' }],
    })
    api.previewFile.mockResolvedValue({
      ok: true,
      path: 'scores.json',
      previewable: true,
      content: '{}',
    })
    api.fileDownloadUrl.mockReturnValue('http://host/api/files/download')

    const store = useResourcesStore()
    await store.loadTaskFiles('t1')
    expect(store.filesOf('t1')).toHaveLength(1)

    const preview = await store.preview('t1', 'scores.json')
    expect(preview.content).toBe('{}')
    expect(store.downloadUrl('t1', 'scores.json')).toBe(
      'http://host/api/files/download',
    )
  })

  it('加载失败记对应域错误且不抛', async () => {
    api.listPublishedData.mockRejectedValue(new Error('已发布数据拉取失败'))
    const store = useResourcesStore()
    await expect(store.loadPublished()).resolves.toBeUndefined()
    expect(store.errors.published).toBe('已发布数据拉取失败')
  })

  it('错误按域隔离：某段失败不污染其它段', async () => {
    api.listRawData.mockRejectedValue(new Error('原始数据拉取失败'))
    api.listPublishedData.mockResolvedValue({ ok: true, datasets: [] })
    api.listSchemes.mockResolvedValue({ ok: true, schemes: [] })

    const store = useResourcesStore()
    await store.loadRaw()
    await store.loadPublished()
    await store.loadSchemes()

    expect(store.errors.raw).toBe('原始数据拉取失败')
    expect(store.errors.published).toBeNull()
    expect(store.errors.schemes).toBeNull()
    expect(store.errors.files).toBeNull()
  })
})
