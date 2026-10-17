import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { LLM_STORAGE_KEY, useSettingsStore } from '@/stores/settings'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('settings · LLM 配置（本地保存）', () => {
  it('默认未启用，toOverride 为 undefined', () => {
    const settings = useSettingsStore()
    expect(settings.hasOverride).toBe(false)
    expect(settings.toOverride()).toBeUndefined()
  })

  it('配齐后 hasOverride 为真且 toOverride 返回配置（含 mode）', () => {
    const settings = useSettingsStore()
    settings.update({
      enabled: true,
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

  it('缺字段则不算配齐', () => {
    const settings = useSettingsStore()
    settings.update({ enabled: true, api_base: 'http://x', model: 'm' })
    expect(settings.hasOverride).toBe(false)
    expect(settings.toOverride()).toBeUndefined()
  })

  it('更新后持久化到 localStorage，load 可回读', () => {
    const settings = useSettingsStore()
    settings.update({ enabled: true, api_key: 'k', model: 'm' })
    const raw = localStorage.getItem(LLM_STORAGE_KEY)
    expect(raw).toBeTruthy()
    expect(JSON.parse(raw as string).api_key).toBe('k')

    settings.reset()
    expect(localStorage.getItem(LLM_STORAGE_KEY)).toContain('"enabled":false')
    expect(settings.llm.enabled).toBe(false)
  })
})
