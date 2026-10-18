import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listModels: vi.fn(),
  getModel: vi.fn(),
}))

vi.mock('@/api/models', () => api)

import { useModelsStore } from '@/stores/models'

beforeEach(() => {
  setActivePinia(createPinia())
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('FE-ML-2 · 模型 store', () => {
  it('loadModels 填充列表、清列表错误、复位加载态', async () => {
    api.listModels.mockResolvedValue({
      ok: true,
      models: [{ model_id: 'ml-1', task_type: 'classification' }],
    })
    const store = useModelsStore()
    await store.loadModels()

    expect(store.models).toHaveLength(1)
    expect(store.models[0].model_id).toBe('ml-1')
    expect(store.errors.list).toBeNull()
    expect(store.loading.list).toBe(false)
  })

  it('loadModels 失败记列表错误且不抛', async () => {
    api.listModels.mockRejectedValue(new Error('模型列表拉取失败'))
    const store = useModelsStore()

    await expect(store.loadModels()).resolves.toBeUndefined()
    expect(store.errors.list).toBe('模型列表拉取失败')
    expect(store.loading.list).toBe(false)
  })

  it('loadModel 填充 current，失败置空并记详情错误（与列表错误隔离）', async () => {
    api.getModel.mockResolvedValue({
      ok: true,
      model: { model_id: 'ml-1', artifacts: [] },
    })
    const store = useModelsStore()
    await store.loadModel('ml-1')
    expect(store.current?.model_id).toBe('ml-1')
    expect(store.errors.detail).toBeNull()

    api.getModel.mockRejectedValue(new Error('模型详情拉取失败'))
    await store.loadModel('ml-2')
    expect(store.current).toBeNull()
    expect(store.errors.detail).toBe('模型详情拉取失败')
    expect(store.errors.list).toBeNull()
  })

  it('clear 复位列表、详情与两处错误', async () => {
    api.listModels.mockResolvedValue({ ok: true, models: [{ model_id: 'ml-1' }] })
    api.getModel.mockResolvedValue({ ok: true, model: { model_id: 'ml-1' } })
    const store = useModelsStore()
    await store.loadModels()
    await store.loadModel('ml-1')

    store.clear()

    expect(store.models).toEqual([])
    expect(store.current).toBeNull()
    expect(store.errors.list).toBeNull()
    expect(store.errors.detail).toBeNull()
  })
})
