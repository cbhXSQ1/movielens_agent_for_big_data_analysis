// 阶段 C · 中栏「资源标签」store 单测：开合 / 去重 / 列举 / 清空 / 持久化恢复。
import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'
import {
  fileTabKey,
  modelTabKey,
  schemeTabKey,
  type ShellTab,
} from '@/components/centerTabs'

function tab(over: Partial<ShellTab> = {}): ShellTab {
  return {
    key: 'resource:model:m1',
    label: 'm1',
    kind: 'model',
    payload: { modelId: 'm1' },
    ...over,
  }
}

function freshStore() {
  setActivePinia(createPinia())
  return useCenterTabsStore()
}

beforeEach(() => {
  localStorage.clear()
})

describe('centerTabs · key 构造（稳定、带前缀、互不冲突）', () => {
  it('三类 key 均带 resource: 前缀，且同一输入稳定', () => {
    expect(modelTabKey('m1')).toBe('resource:model:m1')
    expect(schemeTabKey('s.json')).toBe('resource:scheme:s.json')
    expect(fileTabKey('job_1', 'a/b.csv')).toBe(
      'resource:file:job_1:a%2Fb.csv',
    )
    // 与产物标签 id 的 `scheme:xxx` / `task:t1:xxx` 形态不撞车
    expect(schemeTabKey('s.json')).not.toBe('scheme:s.json')
    // 同一输入稳定
    expect(modelTabKey('m1')).toBe(modelTabKey('m1'))
    // 不同模型不同 key
    expect(modelTabKey('a')).not.toBe(modelTabKey('b'))
  })
})

describe('centerTabs · 开 / 关 / 去重 / 列举', () => {
  it('open 登记一条；重复 open 同一 key 去重（保留位置、刷新内容）', () => {
    const store = freshStore()
    store.open(tab({ label: '旧名' }))
    store.open(tab({ label: '新名' }))
    expect(store.list()).toHaveLength(1)
    expect(store.list()[0].label).toBe('新名')
    expect(store.isOpen('resource:model:m1')).toBe(true)
  })

  it('多个不同 key 顺序登记', () => {
    const store = freshStore()
    store.open(tab())
    store.open(tab({ key: 'resource:scheme:s.json', label: 's.json', kind: 'scheme', payload: { name: 's.json' } }))
    expect(store.list().map((t) => t.key)).toEqual([
      'resource:model:m1',
      'resource:scheme:s.json',
    ])
  })

  it('close 移除指定 key；不存在时幂等', () => {
    const store = freshStore()
    store.open(tab())
    store.open(tab({ key: 'resource:scheme:s.json', label: 's.json', kind: 'scheme', payload: { name: 's.json' } }))
    store.close('resource:model:m1')
    expect(store.list().map((t) => t.key)).toEqual(['resource:scheme:s.json'])
    expect(store.isOpen('resource:model:m1')).toBe(false)
    store.close('nope')
    expect(store.list()).toHaveLength(1)
  })

  it('clear 清空全部', () => {
    const store = freshStore()
    store.open(tab())
    store.open(tab({ key: 'resource:scheme:s.json', label: 's.json', kind: 'scheme', payload: { name: 's.json' } }))
    store.clear()
    expect(store.list()).toEqual([])
  })
})

describe('centerTabs · 持久化与恢复', () => {
  it('open 写入 localStorage；新 store 恢复标签', () => {
    const store = freshStore()
    store.open(tab())

    const raw = localStorage.getItem(SHELL_STORAGE_KEYS.centerTabs)
    expect(raw).toBeTruthy()
    expect(JSON.parse(String(raw))).toHaveLength(1)

    const restored = freshStore()
    expect(restored.list().map((t) => t.key)).toEqual(['resource:model:m1'])
    expect(restored.isOpen('resource:model:m1')).toBe(true)
  })

  it('close / clear 同样落盘', () => {
    const store = freshStore()
    store.open(tab())
    store.close('resource:model:m1')
    expect(
      JSON.parse(String(localStorage.getItem(SHELL_STORAGE_KEYS.centerTabs))),
    ).toEqual([])

    store.open(tab())
    store.clear()
    expect(
      JSON.parse(String(localStorage.getItem(SHELL_STORAGE_KEYS.centerTabs))),
    ).toEqual([])
  })

  it('损坏 / 非法存储内容被容忍（丢弃坏条目，不抛）', () => {
    localStorage.setItem(SHELL_STORAGE_KEYS.centerTabs, '{ not json')
    expect(freshStore().list()).toEqual([])

    localStorage.setItem(
      SHELL_STORAGE_KEYS.centerTabs,
      JSON.stringify([{ key: '', label: 'x', kind: 'model', payload: {} }, tab()]),
    )
    expect(freshStore().list().map((t) => t.key)).toEqual(['resource:model:m1'])
  })
})
