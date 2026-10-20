// 阶段 D · 对话视图组件测试（FD.1–FD.4）。
// 一律用假 SSE 流（FakeEventSource）与对 store 动作的 spy 驱动，不依赖真实后端。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick, type App } from 'vue'
import ChatView from '@/views/tabs/ChatView.vue'
import MessageList from '@/components/chat/MessageList.vue'
import OfflineToolForm from '@/components/chat/OfflineToolForm.vue'
import { RESULT_TAB_NAV } from '@/composables/useResultTabNav'
import { useArtifactsStore } from '@/stores/artifacts'
import { useChatStore } from '@/stores/chat'
import { useConversationsStore } from '@/stores/conversations'
import { useToolsStore } from '@/stores/tools'
import type { ArtifactRef, RoundEnvelope } from '@/api/types'
import { FakeEventSource, fakeFactory, streamEnvelope } from './fakeEventSource'

interface OfflineFormHandle {
  selectTool: (name: string) => void
  getValues: () => Record<string, unknown>
  getError: () => string | null
}

function mountChat(pinia: Pinia, extra: { provide?: (app: App) => void } = {}) {
  const plugins: unknown[] = [pinia]
  if (extra.provide) {
    plugins.push({ install: (app: App) => extra.provide?.(app) })
  }
  return mount(ChatView, { global: { plugins: plugins as never[] } })
}

function offlineForm(wrapper: VueWrapper): OfflineFormHandle {
  return wrapper.findComponent(OfflineToolForm).vm as unknown as OfflineFormHandle
}

function roundEnvelope(): RoundEnvelope {
  return { ok: true, engine: 'offline', conversation_id: 'c1' }
}

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  FakeEventSource.reset()
  vi.stubEnv('VITE_LLM_API_BASE', '')
  vi.stubEnv('VITE_LLM_API_KEY', '')
  vi.stubEnv('VITE_LLM_MODEL', '')
  vi.stubEnv('VITE_LLM_MODE', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('FD.1 输入区 + 模式切换', () => {
  it('默认自动模式显示输入框；切到手动显示工具表单', async () => {
    const wrapper = mountChat(pinia)

    expect(wrapper.find('[data-testid="chat-composer"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="chat-input"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-tool-form"]').exists()).toBe(false)

    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    expect(wrapper.find('[data-testid="offline-tool-form"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="chat-input"]').exists()).toBe(false)

    await wrapper.find('[data-testid="chat-mode-llm"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="chat-input"]').exists()).toBe(true)
  })

  it('模式按钮与空消息提示使用白话标签', () => {
    const wrapper = mountChat(pinia)
    expect(wrapper.find('[data-testid="chat-mode-llm"]').text()).toContain('自动（用大模型）')
    expect(wrapper.find('[data-testid="chat-mode-offline"]').text()).toContain('手动（自己选工具）')

    const empty = mount(MessageList, {
      props: { messages: [], streamingNodeId: null, artifacts: {} },
    })
    expect(empty.find('[data-testid="chat-empty"]').text()).toContain(
      '选“自动”由模型选工具，或选“手动”自己选',
    )
  })

  it('模式分段控件与提示在输入卡片外（卡片内只有 textarea + 发送键）', () => {
    const wrapper = mountChat(pinia)
    const card = wrapper.find('[data-testid="chat-composer"]')
    expect(card.exists()).toBe(true)
    // 模式控件在卡片外的 composer-tools 内
    expect(wrapper.find('[data-testid="chat-mode"]').exists()).toBe(true)
    expect(card.find('[data-testid="chat-mode"]').exists()).toBe(false)
    // 卡片内只有 textarea + 发送键
    expect(card.find('[data-testid="chat-input"]').exists()).toBe(true)
    expect(card.find('[data-testid="chat-send"]').exists()).toBe(true)
    // 未配置提示在卡片外
    expect(wrapper.find('[data-testid="chat-no-conversation"]').exists()).toBe(true)
  })

  it('发送为渐变圆形按钮，空内容时禁用、有内容时可发送', async () => {
    const wrapper = mountChat(pinia)
    const card = wrapper.find('[data-testid="chat-composer"]')
    const btn = card.find('[data-testid="chat-send"]')
    expect(btn.exists()).toBe(true)
    expect(btn.find('svg').exists()).toBe(true)
    // 空内容：禁用
    expect(btn.attributes('disabled')).toBeDefined()

    await card.find('textarea').setValue('帮我分析评分')
    await nextTick()
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('无消息时显示欢迎语且输入卡片居中（hero 态）', () => {
    const wrapper = mountChat(pinia)
    expect(wrapper.find('[data-testid="chat-welcome"]').exists()).toBe(true)
    expect(wrapper.classes()).toContain('chat-view--hero')
  })

  it('有消息时隐藏欢迎语、消息区滚动展示', async () => {
    const chat = useChatStore()
    const msg = chat.ensure(1, 'assistant')
    msg.text = '在的'
    msg.done = true

    const wrapper = mountChat(pinia)
    await nextTick()
    expect(wrapper.find('[data-testid="chat-welcome"]').exists()).toBe(false)
    expect(wrapper.classes()).not.toContain('chat-view--hero')
    expect(wrapper.find('[data-testid="chat-messages"]').exists()).toBe(true)
  })

  it('未配置大模型时给出如实提示', async () => {
    const wrapper = mountChat(pinia)
    expect(wrapper.find('[data-testid="chat-llm-unconfigured"]').exists()).toBe(true)
  })
})

describe('FD.2 离线工具表单', () => {
  it('按工具表参数渲染类型与默认值，填写后提交经 sendOffline', async () => {
    const tools = useToolsStore()
    tools.applySnapshot([
      {
        name: 'clean_data',
        description: '清洗数据',
        parameters: [
          { name: 'chunk', type: 'integer', required: true, minimum: 1, maximum: 100, default: 10 },
          { name: 'method', type: 'enum', required: false, enum: ['mean', 'median'], default: 'mean' },
          { name: 'force', type: 'boolean', required: false, default: false },
          { name: 'tag', type: 'string', required: true, description: '实验标签' },
        ],
        source: 'local',
        long_running: false,
        estimated_seconds: null,
      },
    ])

    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    const sendSpy = vi.spyOn(chat, 'send').mockResolvedValue(roundEnvelope())

    const wrapper = mountChat(pinia)
    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    const form = offlineForm(wrapper)
    form.selectTool('clean_data')
    await nextTick()

    // 按类型渲染（string / integer / boolean / enum）
    expect(wrapper.find('[data-testid="offline-param-chunk"]').attributes('data-param-type')).toBe('integer')
    expect(wrapper.find('[data-testid="offline-param-method"]').attributes('data-param-type')).toBe('enum')
    expect(wrapper.find('[data-testid="offline-param-force"]').attributes('data-param-type')).toBe('boolean')
    expect(wrapper.find('[data-testid="offline-param-tag"]').attributes('data-param-type')).toBe('string')

    // 默认值已预填
    expect(form.getValues()).toMatchObject({ chunk: 10, method: 'mean', force: false })

    // 必填未填时提交被拦下
    await wrapper.find('[data-testid="offline-submit"]').trigger('click')
    expect(sendSpy).not.toHaveBeenCalled()
    expect(form.getError()).toContain('tag')

    // 填必填项后提交
    await wrapper.find('[data-testid="offline-param-tag"] input').setValue('实验A')
    await wrapper.find('[data-testid="offline-submit"]').trigger('click')
    await flushPromises()

    expect(sendSpy).toHaveBeenCalledTimes(1)
    const [cid, mode, payload] = sendSpy.mock.calls[0]
    expect(cid).toBe('c1')
    expect(mode).toBe('offline')
    expect(payload.tool).toBe('clean_data')
    expect(payload.params).toEqual({ chunk: 10, method: 'mean', force: false, tag: '实验A' })
  })

  it('提交成功后收起表单为回执摘要，点「重新选择工具」可恢复展开', async () => {
    const tools = useToolsStore()
    tools.applySnapshot([
      {
        name: 'clean_data',
        description: '清洗数据',
        parameters: [
          { name: 'chunk', type: 'integer', required: true, minimum: 1, maximum: 100, default: 10 },
          { name: 'tag', type: 'string', required: true, description: '实验标签' },
        ],
        source: 'local',
        long_running: false,
        estimated_seconds: null,
      },
    ])

    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    vi.spyOn(chat, 'send').mockResolvedValue(roundEnvelope())

    const wrapper = mountChat(pinia)
    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    offlineForm(wrapper).selectTool('clean_data')
    await nextTick()
    await wrapper.find('[data-testid="offline-param-tag"] input').setValue('实验A')
    await wrapper.find('[data-testid="offline-submit"]').trigger('click')
    await flushPromises()

    // 收起：参数表单与提交按钮消失，换成一行回执摘要。
    expect(wrapper.find('[data-testid="offline-receipt"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-param-tag"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="offline-submit"]').exists()).toBe(false)
    const receipt = wrapper.find('[data-testid="offline-receipt-text"]').text()
    expect(receipt).toContain('已调用')
    expect(receipt).toContain('clean_data')
    expect(receipt).toContain('tag=实验A')
    expect(receipt).toContain('chunk=10')

    // 「重新选择工具 / 修改参数」恢复展开（保留工具与已填参数）。
    await wrapper.find('[data-testid="offline-reopen"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="offline-receipt"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="offline-param-tag"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-submit"]').exists()).toBe(true)
    expect(offlineForm(wrapper).getValues()).toMatchObject({ chunk: 10, tag: '实验A' })
  })

  it('提交请求进行中给出反馈：按钮 loading + 「已提交」提示，落定后收起', async () => {
    const tools = useToolsStore()
    tools.applySnapshot([
      {
        name: 'clean_data',
        description: '清洗数据',
        parameters: [
          { name: 'tag', type: 'string', required: true, description: '实验标签' },
        ],
        source: 'local',
        long_running: false,
        estimated_seconds: null,
      },
    ])

    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    let resolveSend!: (v: RoundEnvelope) => void
    vi.spyOn(chat, 'send').mockReturnValue(
      new Promise<RoundEnvelope>((resolve) => {
        resolveSend = resolve
      }),
    )

    const wrapper = mountChat(pinia)
    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    offlineForm(wrapper).selectTool('clean_data')
    await nextTick()
    await wrapper.find('[data-testid="offline-param-tag"] input').setValue('实验A')
    await wrapper.find('[data-testid="offline-submit"]').trigger('click')
    await nextTick()

    // 进行中：表单未收起，按钮 loading，出现「已提交，结果见上方对话」提示。
    expect(wrapper.find('[data-testid="offline-receipt"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="offline-submitting"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-submitting"]').text()).toContain('已提交')
    expect(wrapper.find('[data-testid="offline-submit"]').classes()).toContain(
      'n-button--loading',
    )

    resolveSend(roundEnvelope())
    await flushPromises()
    // 落定后收起为回执。
    expect(wrapper.find('[data-testid="offline-submitting"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="offline-receipt"]').exists()).toBe(true)
  })

  it('切换到 LLM 再切回离线后为全新编辑态，不残留回执', async () => {
    const tools = useToolsStore()
    tools.applySnapshot([
      {
        name: 'get_samples',
        description: '取样本',
        parameters: [{ name: 'n', type: 'integer', required: true, default: 5 }],
        source: 'local',
        long_running: false,
        estimated_seconds: null,
      },
    ])

    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    vi.spyOn(chat, 'send').mockResolvedValue(roundEnvelope())

    const wrapper = mountChat(pinia)
    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    offlineForm(wrapper).selectTool('get_samples')
    await nextTick()
    await wrapper.find('[data-testid="offline-submit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="offline-receipt"]').exists()).toBe(true)

    await wrapper.find('[data-testid="chat-mode-llm"]').trigger('click')
    await nextTick()
    await wrapper.find('[data-testid="chat-mode-offline"]').trigger('click')
    await nextTick()

    expect(wrapper.find('[data-testid="offline-receipt"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="offline-tool-form"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="offline-submit"]').exists()).toBe(false)
  })
})

describe('FD.3 流式消息', () => {
  it('message_delta 逐段拼接显示，message_done 收尾去掉打字机光标', async () => {
    const conversations = useConversationsStore()
    conversations.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const source = FakeEventSource.instances[0]

    const wrapper = mountChat(pinia)

    source.emit('message_delta', streamEnvelope('message_delta', { text: '你', node_id: 1 }, 1))
    await nextTick()
    expect(wrapper.find('[data-testid="message-assistant"] .message__text').text()).toBe('你')
    expect(wrapper.find('[data-testid="typing-caret"]').exists()).toBe(true)

    source.emit('message_delta', streamEnvelope('message_delta', { text: '好，', node_id: 1 }, 2))
    await nextTick()
    expect(wrapper.find('[data-testid="message-assistant"] .message__text').text()).toBe('你好，')

    source.emit('message_delta', streamEnvelope('message_delta', { text: '世界', node_id: 1 }, 3))
    await nextTick()
    expect(wrapper.find('[data-testid="message-assistant"] .message__text').text()).toBe('你好，世界')

    source.emit('message_done', streamEnvelope('message_done', { text: '你好，世界！', node_id: 1 }, 4))
    await nextTick()
    expect(wrapper.find('[data-testid="message-assistant"] .message__text').text()).toBe('你好，世界！')
    expect(wrapper.find('[data-testid="typing-caret"]').exists()).toBe(false)
  })

  it('同时显示用户消息与助手消息', async () => {
    const chat = useChatStore()
    const user = chat.ensure(1, 'user')
    user.text = '我要清洗数据'
    user.done = true
    const assistant = chat.ensure(2, 'assistant')
    assistant.text = '好的，开始清洗'
    assistant.done = true

    const wrapper = mountChat(pinia)
    await nextTick()

    expect(wrapper.findAll('[data-testid="message-user"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="message-user"] .message__text').text()).toBe('我要清洗数据')
    expect(wrapper.find('[data-testid="message-assistant"] .message__text').text()).toBe('好的，开始清洗')
  })
})

describe('FD.4 产物超链接', () => {
  function taskArtifact(
    id: string,
    title: string,
    over: Partial<ArtifactRef> = {},
  ): ArtifactRef {
    return {
      id,
      title,
      category: '报告',
      kind: 'md',
      source: 'task_file',
      task_id: 't1',
      path: 'report.md',
      scheme_name: null,
      ...over,
    }
  }

  it('助手回合的产物在对话里显示为超链接，点击激活该产物标签', async () => {
    const conversations = useConversationsStore()
    conversations.currentId = 'c1'
    const chat = useChatStore()
    const artifacts = useArtifactsStore()
    const art = taskArtifact('task:t1:report.md', '评估报告')
    artifacts.add('c1', art)

    const msg = chat.ensure(1, 'assistant')
    msg.text = '已完成'
    msg.done = true
    chat.attachArtifact(art.id)

    const nav = vi.fn<(key: string) => void>()
    const wrapper = mountChat(pinia, {
      provide: (app) => app.provide(RESULT_TAB_NAV, nav),
    })
    await nextTick()

    const link = wrapper.find(`[data-testid="artifact-link-${art.id}"]`)
    expect(link.exists()).toBe(true)
    expect(link.text()).toBe('评估报告')

    await link.trigger('click')
    expect(nav).toHaveBeenCalledWith(art.id)
  })

  it('产物先于助手消息到达时挂到随后出现的助手消息下（不丢）', () => {
    const chat = useChatStore()
    chat.attachArtifact('a-late')
    expect(chat.pendingArtifactIds).toContain('a-late')

    const msg = chat.ensure(7, 'assistant')
    msg.text = 'x'
    expect(chat.artifactIdsFor(7)).toEqual(['a-late'])
    expect(chat.pendingArtifactIds).toEqual([])
  })
})
