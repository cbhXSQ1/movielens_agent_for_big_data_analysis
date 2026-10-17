import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({ health: vi.fn() }))
vi.mock('@/api/endpoints', () => api)

import { useConnectionStore } from '@/stores/connection'

function healthy(over: Record<string, unknown> = {}) {
  return {
    ok: true,
    service: 'agent-http-api/2.0',
    scope_default: 'full',
    tools: { count: 2, sources: ['local'], available: true },
    llm: { supported: true, configured: false },
    ...over,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.health.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('connection · 健康探活', () => {
  it('check 成功置在线并暴露工具来源与模型可用性', async () => {
    api.health.mockResolvedValue(healthy())
    const conn = useConnectionStore()
    expect(await conn.check()).toBe(true)
    expect(conn.connected).toBe(true)
    expect(conn.toolSources).toEqual(['local'])
    expect(conn.toolsAvailable).toBe(true)
    expect(conn.llmSupported).toBe(true)
    expect(conn.llmConfigured).toBe(false)
  })

  it('check 失败置离线并记中文错误', async () => {
    api.health.mockRejectedValue(new Error('网络不可达'))
    const conn = useConnectionStore()
    expect(await conn.check()).toBe(false)
    expect(conn.connected).toBe(false)
    expect(conn.lastError).toBe('网络不可达')
  })

  it('startPolling 定时探活，stopPolling 停止', async () => {
    vi.useFakeTimers()
    api.health.mockResolvedValue(healthy())
    const conn = useConnectionStore()
    conn.startPolling(1000)
    expect(api.health).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.health).toHaveBeenCalledTimes(2)
    expect(conn.polling).toBe(true)
    conn.stopPolling()
    await vi.advanceTimersByTimeAsync(3000)
    expect(api.health).toHaveBeenCalledTimes(2)
    expect(conn.polling).toBe(false)
  })
})

describe('connection · 流开断驱动', () => {
  it('markStreamOpen / markStreamClosed 翻转连接', () => {
    const conn = useConnectionStore()
    expect(conn.connected).toBe(false)
    conn.markStreamOpen()
    expect(conn.streamOpen).toBe(true)
    expect(conn.connected).toBe(true)
    conn.markStreamClosed()
    expect(conn.streamOpen).toBe(false)
    expect(conn.connected).toBe(false)
  })
})
