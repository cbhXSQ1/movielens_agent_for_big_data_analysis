// 产物 store（§3.12）单测：去重追加 / 恢复重载 / 清空。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const endpoints = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
}))
vi.mock('@/api/endpoints', () => endpoints)

import { useArtifactsStore } from '@/stores/artifacts'
import type { ArtifactRef } from '@/api/types'

function art(id: string, over: Partial<ArtifactRef> = {}): ArtifactRef {
  return {
    id,
    title: id,
    category: '其它',
    kind: 'other',
    source: 'task_file',
    task_id: null,
    path: null,
    scheme_name: null,
    ...over,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  endpoints.listArtifacts.mockReset()
})

describe('FT.2 · 产物 store 去重追加', () => {
  it('add 按 id 去重，保持到达顺序', () => {
    const store = useArtifactsStore()
    store.add('c1', art('a'))
    store.add('c1', art('b'))
    store.add('c1', art('a'))
    expect(store.list('c1').map((x) => x.id)).toEqual(['a', 'b'])
  })

  it('list / find 按对话隔离', () => {
    const store = useArtifactsStore()
    store.add('c1', art('a', { title: '报告' }))
    expect(store.list('c2')).toEqual([])
    expect(store.find('c1', 'a')?.title).toBe('报告')
    expect(store.find('c1', 'nope')).toBeNull()
  })

  it('addMany 批量去重追加', () => {
    const store = useArtifactsStore()
    store.addMany('c1', [art('a'), art('b'), art('a')])
    expect(store.list('c1').map((x) => x.id)).toEqual(['a', 'b'])
  })
})

describe('FT.2 · 产物 store 恢复与清空', () => {
  it('load 先清空再用 listArtifacts 恢复', async () => {
    const store = useArtifactsStore()
    store.add('c1', art('stale'))
    endpoints.listArtifacts.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      artifacts: [art('a'), art('b')],
    })

    const list = await store.load('c1')
    expect(endpoints.listArtifacts).toHaveBeenCalledWith('c1')
    expect(list.map((x) => x.id)).toEqual(['a', 'b'])
    expect(store.list('c1').some((x) => x.id === 'stale')).toBe(false)
  })

  it('load 失败时如实记录错误并返回空', async () => {
    const store = useArtifactsStore()
    endpoints.listArtifacts.mockRejectedValue(new Error('boom'))
    const list = await store.load('c1')
    expect(list).toEqual([])
    expect(store.lastError).toContain('boom')
  })

  it('clear 可清空指定对话或全部', () => {
    const store = useArtifactsStore()
    store.add('c1', art('a'))
    store.add('c2', art('b'))
    store.clear('c1')
    expect(store.list('c1')).toEqual([])
    expect(store.list('c2')).toHaveLength(1)
    store.clear()
    expect(store.list('c2')).toEqual([])
  })
})

describe('FT.2 · 产物标签开合状态（与产物列表分离、按对话隔离）', () => {
  it('openTab 幂等、closeTab 只移除标签不动产物', () => {
    const store = useArtifactsStore()
    store.add('c1', art('a'))

    expect(store.openIds('c1')).toEqual([])
    store.openTab('c1', 'a')
    store.openTab('c1', 'a')
    expect(store.openIds('c1')).toEqual(['a'])
    expect(store.isOpen('c1', 'a')).toBe(true)

    store.closeTab('c1', 'a')
    expect(store.openIds('c1')).toEqual([])
    expect(store.isOpen('c1', 'a')).toBe(false)
    // 关标签不删产物
    expect(store.list('c1').map((x) => x.id)).toEqual(['a'])
  })

  it('已开集合按对话隔离', () => {
    const store = useArtifactsStore()
    store.openTab('c1', 'a')
    store.openTab('c2', 'b')
    expect(store.openIds('c1')).toEqual(['a'])
    expect(store.openIds('c2')).toEqual(['b'])
    store.closeTab('c1', 'a')
    expect(store.openIds('c1')).toEqual([])
    expect(store.openIds('c2')).toEqual(['b'])
  })

  it('load 重载产物列表不清空已开集合', async () => {
    const store = useArtifactsStore()
    store.openTab('c1', 'a')
    endpoints.listArtifacts.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      artifacts: [art('a'), art('b')],
    })
    await store.load('c1')
    expect(store.openIds('c1')).toEqual(['a'])
  })

  it('clear 同时清空产物与已开集合', () => {
    const store = useArtifactsStore()
    store.add('c1', art('a'))
    store.openTab('c1', 'a')
    store.clear('c1')
    expect(store.openIds('c1')).toEqual([])

    store.add('c2', art('b'))
    store.openTab('c2', 'b')
    store.clear()
    expect(store.openIds('c2')).toEqual([])
  })
})
