import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { LLM_STORAGE_KEY, useSettingsStore } from '@/stores/settings'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('settings · LLM 配置（本地保存）', () => {
  it('默认空配置，toOverride 为 undefined', () => {
    const settings = useSettingsStore()
    expect(settings.hasOverride).toBe(false)
    expect(settings.toOverride()).toBeUndefined()
  })

  it('三个供应商字段配齐即算 override，不依赖 enabled', () => {
    const settings = useSettingsStore()
    settings.update({
      api_base: ' http://llm.local ',
      api_key: 'sk-1',
      model: 'gpt-x',
      mode: 'llm',
    })
    expect(settings.hasOverride).toBe(true)
    expect(settings.toOverride()).toEqual({
      enabled: true,
      api_base: 'http://llm.local',
      api_key: 'sk-1',
      model: 'gpt-x',
      mode: 'llm',
    })
  })

  it('旧数据 enabled=false 但字段配齐时仍算 override', () => {
    const settings = useSettingsStore()
    settings.update({
      enabled: false,
      api_base: 'http://llm.local',
      api_key: 'sk-1',
      model: 'gpt-x',
    })
    expect(settings.hasOverride).toBe(true)
    expect(settings.toOverride()?.enabled).toBe(true)
  })

  it('缺字段则不算配齐', () => {
    const settings = useSettingsStore()
    settings.update({ enabled: true, api_base: 'http://x', model: 'm' })
    expect(settings.hasOverride).toBe(false)
    expect(settings.toOverride()).toBeUndefined()
  })

  it('更新后持久化到 localStorage，load 可回读', () => {
    const settings = useSettingsStore()
    settings.update({ api_key: 'k', model: 'm' })
    const raw = localStorage.getItem(LLM_STORAGE_KEY)
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw as string).api_key).toBe('k')

    settings.update({ api_base: 'http://x' })
    expect(JSON.parse(localStorage.getItem(LLM_STORAGE_KEY) as string).enabled).toBe(true)

    settings.reset()
    expect(localStorage.getItem(LLM_STORAGE_KEY)).toContain('"enabled":false')
    expect(settings.llm.enabled).toBe(false)
  })
})
