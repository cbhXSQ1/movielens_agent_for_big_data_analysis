import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import MessageList from '@/components/chat/MessageList.vue'
import { useChatStore, type ChatMessage } from '@/stores/chat'
import type { RoundEnvelope } from '@/api/types'

const api = vi.hoisted(() => ({
  sendByMode: vi.fn(),
  listMessages: vi.fn(),
}))

vi.mock('@/api/endpoints', () => api)

const WARNING = '以下数字未能在工具结果中找到：888888'
const TEXT = '共有 888888 条记录'

function envelope(over: Partial<RoundEnvelope> = {}): RoundEnvelope {
  return {
    ok: true,
    engine: 'llm',
    conversation_id: 'c1',
    ...over,
  }
}

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    node_id: 1,
    role: 'assistant',
    text: TEXT,
    done: true,
    parent_id: null,
    created_at: null,
    error: null,
    ...over,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  api.sendByMode.mockReset()
  api.listMessages.mockReset()
  vi.stubEnv('VITE_LLM_API_BASE', '')
  vi.stubEnv('VITE_LLM_API_KEY', '')
  vi.stubEnv('VITE_LLM_MODEL', '')
  vi.stubEnv('VITE_LLM_MODE', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('chat store · 数字提示落位', () => {
  it('把 warning 与 spans 挂到信封指定的助手节点', async () => {
    const chat = useChatStore()
    chat.ensure(1, 'assistant')
    chat.ensure(2, 'assistant')
    api.sendByMode.mockResolvedValue(
      envelope({
        node_id: 1,
        final: TEXT,
        guard_warning: WARNING,
        guard_spans: [{ number: '888888', start: 3, end: 9 }],
      }),
    )

    await chat.send('c1', 'llm', { text: '查一下' })

    expect(chat.messagesByNode[1].warning).toBe(WARNING)
    expect(chat.messagesByNode[1].warningSpans).toEqual([
      { number: '888888', start: 3, end: 9 },
    ])
    expect(chat.messagesByNode[2].warning).toBeUndefined()
    expect(chat.messagesByNode[2].warningSpans).toBeUndefined()
  })

  it('节点尚未出现时先暂存，节点出现后再挂上', async () => {
    const chat = useChatStore()
    api.sendByMode.mockResolvedValue(
      envelope({
        node_id: 7,
        final: TEXT,
        guard_warning: WARNING,
        guard_spans: [{ number: '888888', start: 3, end: 9 }],
      }),
    )

    await chat.send('c1', 'llm', { text: '查一下' })
    expect(chat.messagesByNode[7]).toBeUndefined()

    const msg = chat.ensure(7, 'assistant')
    msg.text = TEXT

    expect(msg.warning).toBe(WARNING)
    expect(msg.warningSpans).toEqual([{ number: '888888', start: 3, end: 9 }])
  })
})

describe('MessageList · 无溯源数字行内标红', () => {
  it('命中区间显示红色标记，文字内容保持不变', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          message({
            warning: WARNING,
            warningSpans: [{ number: '888888', start: 3, end: 9 }],
          }),
        ],
        streamingNodeId: null,
        artifacts: {},
      },
    })

    // 助手消息走 Markdown 渲染，标红元素在 ChatMarkdown 内
    const mark = wrapper.find('.chat-markdown__num-warn')
    expect(mark.exists()).toBe(true)
    expect(mark.text()).toBe('888888')
    expect(mark.attributes('title')).toContain('未能在工具结果中找到')
    expect(wrapper.find('.message__text').text()).toBe(TEXT)
  })

  it('没有区间时不产生标红标记', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [message()],
        streamingNodeId: null,
        artifacts: {},
      },
    })

    expect(wrapper.find('.chat-markdown__num-warn').exists()).toBe(false)
    expect(wrapper.find('.message__text').text()).toBe(TEXT)
  })

  it('只有 warning 没有区间时显示一条兜底提示', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [message({ warning: WARNING })],
        streamingNodeId: null,
        artifacts: {},
      },
    })

    const warning = wrapper.find('[data-testid="message-guard-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain(WARNING)
    expect(wrapper.find('.chat-markdown__num-warn').exists()).toBe(false)
  })
})

describe('MessageList · 助手消息按 Markdown 渲染', () => {
  it('标题/列表/加粗排成结构化元素，不再显示原文符号', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          message({
            text: '## 小结\n- **评分**：95.07 → 99.95\n- 数据量已同屏展示',
          }),
        ],
        streamingNodeId: null,
        artifacts: {},
      },
    })

    const body = wrapper.find('.message__text')
    expect(body.find('h2').text()).toBe('小结')
    expect(body.findAll('li')).toHaveLength(2)
    expect(body.find('strong').text()).toBe('评分')
    expect(body.text()).not.toContain('##')
    expect(body.text()).not.toContain('**')
  })
})
