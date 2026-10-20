import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useSettingsStore } from '@/stores/settings'

const LEGACY_STORAGE_KEY = 'movielens.llm.config'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.stubEnv('VITE_LLM_API_BASE', '')
  vi.stubEnv('VITE_LLM_API_KEY', '')
  vi.stubEnv('VITE_LLM_MODEL', '')
  vi.stubEnv('VITE_LLM_MODE', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('settings · LLM 配置（frontend/.env.local）', () => {
  it('环境变量为空时默认空配置，toOverride 为 undefined', () => {
    const settings = useSettingsStore()
    expect(settings.llm).toMatchObject({
      api_base: '',
      api_key: '',
      model: '',
      mode: 'llm',
    })
    expect(settings.hasOverride).toBe(false)
    expect(settings.toOverride()).toBeUndefined()
  })

  it('默认配置来自 VITE_LLM_* 并去掉首尾空白', () => {
    vi.stubEnv('VITE_LLM_API_BASE', ' http://env.local/v1 ')
    vi.stubEnv('VITE_LLM_API_KEY', ' sk-env-test ')
    vi.stubEnv('VITE_LLM_MODEL', ' env-model ')
    vi.stubEnv('VITE_LLM_MODE', ' llm ')

    const settings = useSettingsStore()
    expect(settings.llm.api_base).toBe('http://env.local/v1')
    expect(settings.llm.api_key).toBe('sk-env-test')
    expect(settings.llm.model).toBe('env-model')
    expect(settings.llm.mode).toBe('llm')
    expect(settings.hasOverride).toBe(true)
    expect(settings.toOverride()).toEqual({
      enabled: true,
      api_base: 'http://env.local/v1',
      api_key: 'sk-env-test',
      model: 'env-model',
      mode: 'llm',
    })
  })

  it('mode 为空时回落到 llm', () => {
    vi.stubEnv('VITE_LLM_MODE', '  ')
    const settings = useSettingsStore()
    expect(settings.llm.mode).toBe('llm')
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

  it('更新与保存只在内存生效，不写浏览器存储', () => {
    const settings = useSettingsStore()
    settings.update({ api_base: 'http://x', api_key: 'k', model: 'm' })
    settings.save()

    expect(settings.hasOverride).toBe(true)
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeNull()
  })

  it('load 与 reset 重新应用环境默认值', () => {
    vi.stubEnv('VITE_LLM_API_BASE', 'http://env.local/v1')
    vi.stubEnv('VITE_LLM_API_KEY', 'sk-env-test')
    vi.stubEnv('VITE_LLM_MODEL', 'env-model')
    vi.stubEnv('VITE_LLM_MODE', 'manual')

    const settings = useSettingsStore()
    settings.update({ api_base: 'http://changed/v1', api_key: 'changed-key', model: 'changed' })

    settings.load()
    expect(settings.llm.api_base).toBe('http://env.local/v1')
    expect(settings.llm.api_key).toBe('sk-env-test')
    expect(settings.llm.model).toBe('env-model')
    expect(settings.llm.mode).toBe('manual')

    settings.update({ api_base: 'http://changed/v1', api_key: 'changed-key', model: 'changed' })
    settings.reset()
    expect(settings.llm.api_base).toBe('http://env.local/v1')
    expect(settings.llm.api_key).toBe('sk-env-test')
    expect(settings.llm.model).toBe('env-model')
    expect(settings.llm.enabled).toBe(false)
    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeNull()
  })
})
