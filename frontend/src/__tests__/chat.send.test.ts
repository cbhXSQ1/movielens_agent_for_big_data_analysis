// chat.send 接线：已保存的 LLM 配置随请求 `llm` 字段传出，未配置时不带。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({ sendByMode: vi.fn() }))
vi.mock('@/api/endpoints', () => api)

import { useChatStore } from '@/stores/chat'
import { useSettingsStore } from '@/stores/settings'

function round(): Record<string, unknown> {
  return { ok: true, engine: 'llm', conversation_id: 'c1' }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  api.sendByMode.mockReset()
  vi.stubEnv('VITE_LLM_API_BASE', '')
  vi.stubEnv('VITE_LLM_API_KEY', '')
  vi.stubEnv('VITE_LLM_MODEL', '')
  vi.stubEnv('VITE_LLM_MODE', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('chat.send · LLM 配置随请求生效', () => {
  it('已保存配置时请求体带 llm（enabled/api_base/api_key/model/mode）', async () => {
    const settings = useSettingsStore()
    settings.update({
      enabled: true,
      api_base: 'http://llm.local/v1',
      api_key: 'sk-x',
      model: 'gpt-x',
    })
    api.sendByMode.mockResolvedValue(round())

    const chat = useChatStore()
    await chat.send('c1', 'llm', { text: '你好' })

    expect(api.sendByMode).toHaveBeenCalledTimes(1)
    const [cid, mode, payload, options] = api.sendByMode.mock.calls[0]
    expect(cid).toBe('c1')
    expect(mode).toBe('llm')
    expect(payload).toEqual({ text: '你好' })
    expect(options.llm).toMatchObject({
      enabled: true,
      api_base: 'http://llm.local/v1',
      api_key: 'sk-x',
      model: 'gpt-x',
    })
  })

  it('未配置时不带 llm 字段（交由后端默认配置）', async () => {
    api.sendByMode.mockResolvedValue(round())
    const chat = useChatStore()
    await chat.send('c1', 'llm', { text: 'hi' })

    const [, , , options] = api.sendByMode.mock.calls[0]
    expect(options.llm).toBeUndefined()
  })

  it('reasoning_effort 随 payload 透传；默认档不带该字段', async () => {
    api.sendByMode.mockResolvedValue(round())
    const chat = useChatStore()
    await chat.send('c1', 'llm', { text: '你好', reasoning_effort: 'high' })

    const [, , payload] = api.sendByMode.mock.calls[0]
    expect(payload).toEqual({ text: '你好', reasoning_effort: 'high' })

    api.sendByMode.mockClear()
    await chat.send('c1', 'llm', { text: 'hi' })
    const [, , payloadDefault] = api.sendByMode.mock.calls[0]
    expect(payloadDefault).toEqual({ text: 'hi' })
  })
})
