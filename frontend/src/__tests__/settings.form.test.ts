// 设置表单（迭代打磨）：保存/读取 localStorage、测试连接（POST /api/llm/test）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { nextTick } from 'vue'

const api = vi.hoisted(() => ({ testLlm: vi.fn() }))
vi.mock('@/api/endpoints', () => api)

import SettingsForm from '@/components/SettingsForm.vue'
import { LLM_STORAGE_KEY, useSettingsStore } from '@/stores/settings'

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
})

describe('设置表单 · 保存 / 读取 localStorage', () => {
  it('填写并保存后写入 localStorage，store 同步', async () => {
    const wrapper = mountForm()
    await wrapper.find('[data-testid="settings-enabled"]').trigger('click')
    await fill(wrapper, {
      api_base: 'http://llm.local/v1',
      api_key: 'sk-abc',
      model: 'qwen',
    })
    await wrapper.find('[data-testid="settings-save"]').trigger('click')

    const raw = localStorage.getItem(LLM_STORAGE_KEY)
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw as string)
    expect(parsed.enabled).toBe(true)
    expect(parsed.api_base).toBe('http://llm.local/v1')
    expect(parsed.api_key).toBe('sk-abc')
    expect(parsed.model).toBe('qwen')

    const settings = useSettingsStore()
    expect(settings.llm.api_base).toBe('http://llm.local/v1')
    expect(settings.hasOverride).toBe(true)
    expect(wrapper.find('[data-testid="settings-result"]').text()).toContain('已保存')
  })

  it('重新挂载时从 localStorage 回读（刷新不丢）', async () => {
    localStorage.setItem(
      LLM_STORAGE_KEY,
      JSON.stringify({
        enabled: true,
        api_base: 'http://x/v1',
        api_key: 'k',
        model: 'm',
        mode: 'llm',
      }),
    )
    const wrapper = mountForm()
    const base = wrapper.find('[data-testid="settings-api-base"] input')
      .element as HTMLInputElement
    const model = wrapper.find('[data-testid="settings-model"] input')
      .element as HTMLInputElement
    expect(base.value).toBe('http://x/v1')
    expect(model.value).toBe('m')
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
