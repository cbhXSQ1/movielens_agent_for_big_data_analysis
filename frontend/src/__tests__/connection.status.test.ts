// 顶栏连接状态（迭代打磨）：清晰文案 + 悬停说明 + 点击重试。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'

const api = vi.hoisted(() => ({ health: vi.fn() }))
vi.mock('@/api/endpoints', () => api)

import ConnectionStatus from '@/components/ConnectionStatus.vue'

function healthy(over: Record<string, unknown> = {}) {
  return {
    ok: true,
    service: 'agent-http-api/2.0',
    scope_default: 'full',
    tools: { count: 3, sources: ['local'], available: true },
    llm: { supported: true, configured: true },
    ...over,
  }
}

function mountStatus() {
  return mount(ConnectionStatus, { global: { plugins: [createPinia()] } })
}

beforeEach(() => {
  api.health.mockReset()
})

describe('顶栏连接状态', () => {
  it('未连接时显示「后端未连接」，悬停说明含启动方式', () => {
    const wrapper = mountStatus()
    const el = wrapper.find('[data-testid="connection-status"]')
    expect(el.text()).toContain('后端未连接')
    expect(el.attributes('title')).toContain('start_backend.bat')
    expect(el.attributes('title')).toContain('start_backend.bat')
  })

  it('点击重试触发探活，成功后显示「已连接」并含工具数', async () => {
    api.health.mockResolvedValue(healthy())
    const wrapper = mountStatus()

    await wrapper.find('[data-testid="connection-status"]').trigger('click')
    await flushPromises()

    expect(api.health).toHaveBeenCalledTimes(1)
    const el = wrapper.find('[data-testid="connection-status"]')
    expect(el.text()).toContain('已连接')
    expect(el.attributes('title')).toContain('可用工具 3 个')
  })
})
