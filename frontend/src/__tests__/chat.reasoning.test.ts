// 思考档位控件：仅 LLM 模式可用（离线时置灰）；所选档位映射为请求体的 reasoning_effort。
// 新前端：档位为胶囊 + 弹出层（低/中/高/默认），默认「默认」。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import ChatView from '@/views/tabs/ChatView.vue'
import { useChatStore } from '@/stores/chat'
import { useConversationsStore } from '@/stores/conversations'
import type { RoundEnvelope } from '@/api/types'

function round(): RoundEnvelope {
  return { ok: true, engine: 'llm', conversation_id: 'c1' }
}

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  localStorage.clear()
})

function mountChat() {
  return mount(ChatView, { global: { plugins: [pinia] } })
}

describe('思考档位 · 控件放置与默认值', () => {
  it('LLM 主控下显示思考档位，默认选「默认」', () => {
    const wrapper = mountChat()
    expect(wrapper.find('[data-testid="chat-reasoning"]').exists()).toBe(true)
    const def = wrapper.find('[data-testid="chat-reasoning-default"]')
    expect(def.classes()).toContain('on')
  })

  it('切到离线直调时思考胶囊置灰（dimmed），不可点击', async () => {
    const wrapper = mountChat()
    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    const pill = wrapper.find('[data-testid="chat-reasoning-pill"]')
    expect(pill.exists()).toBe(true)
    expect(pill.classes()).toContain('dimmed')
  })
})

describe('思考档位 · 值与请求字段映射', () => {
  it('选「高」发送 → 请求体带 reasoning_effort="high"', async () => {
    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    const sendSpy = vi.spyOn(chat, 'send').mockResolvedValue(round())

    const wrapper = mountChat()
    await wrapper.find('[data-testid="chat-reasoning-high"]').trigger('click')
    await nextTick()
    await wrapper.find('textarea').setValue('分析评分')
    await wrapper.find('[data-testid="chat-send"]').trigger('click')
    await flushPromises()

    expect(sendSpy).toHaveBeenCalledTimes(1)
    const [cid, m, payload] = sendSpy.mock.calls[0]
    expect(cid).toBe('c1')
    expect(m).toBe('llm')
    expect(payload).toEqual({ text: '分析评分', reasoning_effort: 'high' })
  })

  it('选「默认」发送 → 请求体不带 reasoning_effort', async () => {
    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    const sendSpy = vi.spyOn(chat, 'send').mockResolvedValue(round())

    const wrapper = mountChat()
    await wrapper.find('[data-testid="chat-reasoning-default"]').trigger('click')
    await nextTick()
    await wrapper.find('textarea').setValue('分析评分')
    await wrapper.find('[data-testid="chat-send"]').trigger('click')
    await flushPromises()

    const [, , payload] = sendSpy.mock.calls[0]
    expect(payload).toEqual({ text: '分析评分' })
    expect(payload).not.toHaveProperty('reasoning_effort')
  })

  it('所选档位记入 localStorage（跨会话沿用）', async () => {
    const wrapper = mountChat()
    await wrapper.find('[data-testid="chat-reasoning-medium"]').trigger('click')
    await nextTick()

    expect(localStorage.getItem('movielens.reasoning.level')).toBe('medium')
  })
})
