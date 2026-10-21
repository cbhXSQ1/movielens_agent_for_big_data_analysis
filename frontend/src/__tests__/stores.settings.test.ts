import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const providersApi = vi.hoisted(() => ({
  listProviders: vi.fn(),
  saveProvider: vi.fn(),
  deleteProvider: vi.fn(),
}))
vi.mock('@/api/llmProviders', () => providersApi)

import { useSettingsStore } from '@/stores/settings'

const LEGACY_STORAGE_KEY = 'movielens.llm.config'

const savedProvider = {
  name: '工作机',
  api_base: 'http://saved.local/v1',
  api_key: 'sk-saved',
  model: 'saved-model',
  mode: 'llm',
  updated_at: 1730000000,
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  providersApi.listProviders.mockReset()
  providersApi.saveProvider.mockReset()
  providersApi.deleteProvider.mockReset()
  providersApi.listProviders.mockResolvedValue({ ok: true, providers: [] })
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

describe('settings · 供应商档案', () => {
  it('loadProviders 读取并保存档案列表', async () => {
    providersApi.listProviders.mockResolvedValue({
      ok: true,
      providers: [savedProvider],
    })
    const settings = useSettingsStore()

    await settings.loadProviders()

    expect(settings.providers).toEqual([savedProvider])
  })

  it('saveProvider 提交当前连接字段与名称，然后刷新列表', async () => {
    providersApi.saveProvider.mockResolvedValue({ ok: true, provider: savedProvider })
    providersApi.listProviders
      .mockResolvedValueOnce({ ok: true, providers: [] })
      .mockResolvedValueOnce({ ok: true, providers: [savedProvider] })
    const settings = useSettingsStore()
    settings.update({
      api_base: ' http://saved.local/v1 ',
      api_key: ' sk-saved ',
      model: ' saved-model ',
      mode: 'llm',
    })
    await settings.loadProviders()

    await settings.saveProvider(' 工作机 ')

    expect(providersApi.saveProvider).toHaveBeenCalledWith({
      name: '工作机',
      api_base: 'http://saved.local/v1',
      api_key: 'sk-saved',
      model: 'saved-model',
      mode: 'llm',
    })
    expect(providersApi.listProviders).toHaveBeenCalledTimes(2)
    expect(settings.providers).toEqual([savedProvider])
  })

  it('同名保存覆盖旧档案，列表仍只有一条', async () => {
    providersApi.saveProvider.mockResolvedValue({ ok: true, provider: savedProvider })
    providersApi.listProviders.mockResolvedValue({
      ok: true,
      providers: [savedProvider],
    })
    const settings = useSettingsStore()
    settings.update({
      api_base: 'http://saved.local/v1',
      api_key: 'sk-saved',
      model: 'saved-model',
    })

    await settings.saveProvider('工作机')
    await settings.saveProvider('工作机')

    expect(providersApi.saveProvider).toHaveBeenCalledTimes(2)
    expect(providersApi.saveProvider.mock.calls[0][0].name).toBe('工作机')
    expect(providersApi.saveProvider.mock.calls[1][0].name).toBe('工作机')
    expect(settings.providers).toHaveLength(1)
  })

  it('applyProvider 用档案预填连接字段', async () => {
    providersApi.listProviders.mockResolvedValue({
      ok: true,
      providers: [savedProvider],
    })
    const settings = useSettingsStore()
    await settings.loadProviders()

    settings.applyProvider('工作机')

    expect(settings.llm).toMatchObject({
      api_base: 'http://saved.local/v1',
      api_key: 'sk-saved',
      model: 'saved-model',
      mode: 'llm',
      enabled: true,
    })
    expect(settings.hasOverride).toBe(true)
  })

  it('removeProvider 删除后刷新列表', async () => {
    providersApi.listProviders
      .mockResolvedValueOnce({ ok: true, providers: [savedProvider] })
      .mockResolvedValueOnce({ ok: true, providers: [] })
    providersApi.deleteProvider.mockResolvedValue({ ok: true, deleted: true })
    const settings = useSettingsStore()
    await settings.loadProviders()

    await settings.removeProvider('工作机')

    expect(providersApi.deleteProvider).toHaveBeenCalledWith('工作机')
    expect(settings.providers).toEqual([])
  })
})
