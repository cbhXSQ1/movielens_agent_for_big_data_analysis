// 设置表单（迭代打磨）：默认值来自 frontend/.env.local、保存只在内存、测试连接（POST /api/llm/test）。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { nextTick } from 'vue'

const api = vi.hoisted(() => ({ testLlm: vi.fn() }))
vi.mock('@/api/endpoints', () => api)

const providersApi = vi.hoisted(() => ({
  listProviders: vi.fn(),
  saveProvider: vi.fn(),
  deleteProvider: vi.fn(),
}))
vi.mock('@/api/llmProviders', () => providersApi)

import SettingsForm from '@/components/SettingsForm.vue'
import { useSettingsStore } from '@/stores/settings'

const LEGACY_STORAGE_KEY = 'movielens.llm.config'

function mountForm() {
  return mount(SettingsForm, { global: { plugins: [createPinia()] } })
}

async function fill(
  wrapper: ReturnType<typeof mountForm>,
  values: { api_base: string; api_key: string; model: string },
): Promise<void> {
  await wrapper.find('[data-testid="settings-api-base"] input').setValue(values.api_base)
  await wrapper.find('[data-testid="settings-api-key"] input').setValue(values.api_key)
  await wrapper.find('[data-testid="settings-model"] input').setValue(values.model)
  await nextTick()
}

beforeEach(() => {
  localStorage.clear()
  api.testLlm.mockReset()
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

describe('设置表单 · 只保存供应商连接', () => {
  it('没有启用开关，并说明是否使用模型由对话模式决定', () => {
    const wrapper = mountForm()
    expect(wrapper.find('[data-testid="settings-enabled"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('这里只保存模型供应商连接')
    expect(wrapper.text()).toContain('选“自动”才会调用模型')
    expect(wrapper.text()).toContain('连接信息来自前端 .env.local')
  })

  it('表单从 VITE_LLM_* 预填，密钥不渲染到页面', () => {
    vi.stubEnv('VITE_LLM_API_BASE', 'http://env.local/v1')
    vi.stubEnv('VITE_LLM_API_KEY', 'sk-env-test')
    vi.stubEnv('VITE_LLM_MODEL', 'env-model')

    const wrapper = mountForm()
    const base = wrapper.find('[data-testid="settings-api-base"] input')
      .element as HTMLInputElement
    const key = wrapper.find('[data-testid="settings-api-key"] input')
      .element as HTMLInputElement
    const model = wrapper.find('[data-testid="settings-model"] input')
      .element as HTMLInputElement

    expect(base.value).toBe('http://env.local/v1')
    expect(key.value).toBe('sk-env-test')
    expect(model.value).toBe('env-model')
    expect(wrapper.text()).not.toContain('sk-env-test')
  })

  it('填写并保存后同步 store，且不写浏览器存储', async () => {
    const wrapper = mountForm()
    await fill(wrapper, {
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })
    await wrapper.find('[data-testid="settings-save"]').trigger('click')

    expect(localStorage.getItem(LEGACY_STORAGE_KEY)).toBeNull()

    const settings = useSettingsStore()
    expect(settings.llm.api_base).toBe('http://llm.local/v1')
    expect(settings.hasOverride).toBe(true)
    expect(wrapper.find('[data-testid="settings-result"]').text()).toContain('已保存')
  })

  it('只填三个供应商字段（不碰开关）即可生成 toOverride', async () => {
    const wrapper = mountForm()
    await fill(wrapper, {
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })
    await wrapper.find('[data-testid="settings-save"]').trigger('click')

    const settings = useSettingsStore()
    expect(settings.toOverride()).toMatchObject({
      enabled: true,
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })
  })
})

describe('设置表单 · 测试连接', () => {
  it('调用 /api/llm/test 并显示延迟', async () => {
    api.testLlm.mockResolvedValue({ ok: true, reachable: true, latency_ms: 321 })
    const wrapper = mountForm()
    await fill(wrapper, {
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })

    await wrapper.find('[data-testid="settings-test"]').trigger('click')
    await flushPromises()

    expect(api.testLlm).toHaveBeenCalledTimes(1)
    expect(api.testLlm.mock.calls[0][0]).toMatchObject({
      enabled: true,
      api_base: 'http://llm.local/v1',
      model: 'qwen',
    })
    expect(wrapper.find('[data-testid="settings-result"]').text()).toContain('321')
  })

  it('后端报错时显示失败原因', async () => {
    api.testLlm.mockRejectedValue(new Error('大模型不可达'))
    const wrapper = mountForm()
    await fill(wrapper, {
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })

    await wrapper.find('[data-testid="settings-test"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="settings-result"]').text()).toContain('大模型不可达')
  })
})

describe('设置表单 · 供应商档案', () => {
  const saved = {
    name: '工作机',
    api_base: 'http://saved.local/v1',
    api_key: 'sk-saved',
    model: 'saved-model',
    mode: 'llm',
    updated_at: 1730000000,
  }

  it('填写供应商名称后保存会按名称提交并提示长期有效', async () => {
    providersApi.saveProvider.mockResolvedValue({ ok: true, provider: saved })
    const wrapper = mountForm()
    await fill(wrapper, {
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })
    await wrapper
      .find('[data-testid="settings-provider-name"] input')
      .setValue('工作机')

    await wrapper.find('[data-testid="settings-save"]').trigger('click')
    await flushPromises()

    expect(providersApi.saveProvider).toHaveBeenCalledWith({
      name: '工作机',
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
      mode: 'llm',
    })
    expect(wrapper.find('[data-testid="settings-result"]').text()).toContain(
      '已保存（长期有效）',
    )
    expect(wrapper.text()).not.toContain('sk-abc')
  })

  it('选择已保存供应商后预填连接字段', async () => {
    providersApi.listProviders.mockResolvedValue({
      ok: true,
      providers: [saved],
    })
    const wrapper = mountForm()
    await flushPromises()

    await wrapper
      .find('[data-testid="settings-provider-select"]')
      .setValue('工作机')
    await nextTick()

    const base = wrapper.find('[data-testid="settings-api-base"] input')
      .element as HTMLInputElement
    const key = wrapper.find('[data-testid="settings-api-key"] input')
      .element as HTMLInputElement
    const model = wrapper.find('[data-testid="settings-model"] input')
      .element as HTMLInputElement
    expect(base.value).toBe('http://saved.local/v1')
    expect(key.value).toBe('sk-saved')
    expect(model.value).toBe('saved-model')
  })

  it('删除已保存供应商需确认', async () => {
    providersApi.listProviders
      .mockResolvedValueOnce({ ok: true, providers: [saved] })
      .mockResolvedValueOnce({ ok: true, providers: [] })
    providersApi.deleteProvider.mockResolvedValue({ ok: true, deleted: true })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mountForm()
    await flushPromises()

    await wrapper
      .find('[data-testid="settings-provider-delete"]')
      .trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalled()
    expect(providersApi.deleteProvider).toHaveBeenCalledWith('工作机')
  })
})
