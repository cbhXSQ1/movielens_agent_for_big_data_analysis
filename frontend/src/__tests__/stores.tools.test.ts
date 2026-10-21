import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({ fetchTools: vi.fn() }))
vi.mock('@/api/endpoints', () => api)

import { useToolsStore } from '@/stores/tools'
import type { ToolSpec } from '@/api/types'

const tools: ToolSpec[] = [
  {
    name: 'get_samples',
    description: '取样例',
    parameters: [
      { name: 'limit', type: 'number', required: false, default: 5 },
      { name: 'dataset', type: 'string', required: true },
    ],
    source: 'local',
    long_running: false,
    estimated_seconds: null,
  },
  {
    name: 'run_cleaning',
    description: '发起清洗',
    parameters: [],
    source: 'mcp',
    long_running: true,
    estimated_seconds: 300,
  },
]

beforeEach(() => {
  setActivePinia(createPinia())
  api.fetchTools.mockReset()
})

describe('tools · 快照与查询', () => {
  it('applySnapshot 整表替换并可按名取用', () => {
    const store = useToolsStore()
    store.applySnapshot(tools)
    expect(store.count).toBe(2)
    expect(store.sources).toEqual(['local', 'mcp'])
    expect(store.get('get_samples')?.description).toBe('取样例')
    expect(store.parametersOf('get_samples')).toHaveLength(2)
  })

  it('defaultsFor 收集带默认值的参数（离线表单预填）', () => {
    const store = useToolsStore()
    store.applySnapshot(tools)
    expect(store.defaultsFor('get_samples')).toEqual({ limit: 5 })
    expect(store.defaultsFor('unknown')).toEqual({})
  })

  it('load 走只读兜底并整表替换', async () => {
    api.fetchTools.mockResolvedValue({ ok: true, tools })
    const store = useToolsStore()
    await store.load()
    expect(api.fetchTools).toHaveBeenCalledTimes(1)
    expect(store.count).toBe(2)
  })

  it('load 失败记 lastError', async () => {
    api.fetchTools.mockRejectedValue(new Error('工具表拉取失败'))
    const store = useToolsStore()
    await store.load()
    expect(store.lastError).toBe('工具表拉取失败')
  })
})
