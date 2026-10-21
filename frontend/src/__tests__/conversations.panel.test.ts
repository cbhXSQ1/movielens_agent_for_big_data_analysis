// 阶段 D · FD.5：对话列表面板测试。
// mock 掉 REST 与 SSE，验证「列出 / 新建 / 切换」，并断言切换时确实换了推送流。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

const endpoints = vi.hoisted(() => ({
  health: vi.fn(),
  createConversation: vi.fn(),
  listConversations: vi.fn(),
  listTasks: vi.fn(),
  getTask: vi.fn(),
  getTaskResult: vi.fn(),
  listArtifacts: vi.fn(),
  listMessages: vi.fn(),
  fetchTools: vi.fn(),
  sendByMode: vi.fn(),
  deleteConversation: vi.fn(),
}))
vi.mock('@/api/endpoints', () => endpoints)

const sse = vi.hoisted(() => ({
  openConversationStream: vi.fn(),
  conversationStreamUrl: (id: string) => `/api/conversations/${id}/stream`,
}))
vi.mock('@/api/sse', () => sse)

import ConversationListPanel from '@/views/panels/ConversationListPanel.vue'
import { useConversationsStore } from '@/stores/conversations'
import { useConnectionStore } from '@/stores/connection'

function fakeStream() {
  return { close: vi.fn(), isClosed: false, conversationId: 'x', streamUrl: '' }
}

const conversation = (id: string, title: string, updated: number) => ({
  conversation_id: id,
  title,
  created_at: updated - 10,
  updated_at: updated,
  current_node_id: null,
})

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  sse.openConversationStream.mockImplementation(() => fakeStream())
  endpoints.listConversations.mockResolvedValue({
    ok: true,
    conversations: [conversation('c1', '对话一', 10), conversation('c2', '对话二', 20)],
  })
  endpoints.createConversation.mockResolvedValue({
    ok: true,
    conversation: conversation('c-new', '新对话', 99),
  })
  endpoints.listArtifacts.mockResolvedValue({
    ok: true,
    conversation_id: 'c1',
    artifacts: [],
  })
  endpoints.listMessages.mockResolvedValue({
    ok: true,
    conversation_id: 'c1',
    messages: [],
  })
  endpoints.deleteConversation.mockImplementation(async (id: string) => ({
    ok: true,
    conversation_id: id,
  }))
})

afterEach(() => {
  document
    .querySelectorAll('.v-binder-follower-container')
    .forEach((node) => node.remove())
})

/** 触发某对话的删除按钮，并在弹出的确认框里点「删除」。 */
async function confirmDelete(
  wrapper: VueWrapper,
  conversationId: string,
): Promise<void> {
  await wrapper
    .find(`[data-testid="conversation-delete-${conversationId}"]`)
    .trigger('click')
  await flushPromises()
  const buttons = Array.from(
    document.querySelectorAll('.n-popconfirm__action button'),
  ) as HTMLButtonElement[]
  const confirm = buttons.find((b) => (b.textContent ?? '').includes('删除'))
  expect(confirm).toBeDefined()
  confirm?.click()
  await flushPromises()
}

describe('FD.5 对话列表：列出 / 新建 / 切换', () => {
  it('连接就绪后列出全部对话', async () => {
    const connection = useConnectionStore()
    connection.markStreamOpen()

    const wrapper = mount(ConversationListPanel, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(endpoints.listConversations).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="conversation-item-c1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="conversation-item-c2"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('对话一')
    expect(wrapper.text()).toContain('对话二')
  })

  it('点击对话即切换当前并换一条推送流', async () => {
    const connection = useConnectionStore()
    connection.markStreamOpen()

    const wrapper = mount(ConversationListPanel, { global: { plugins: [pinia] } })
    await flushPromises()

    await wrapper.find('[data-testid="conversation-item-c2"]').trigger('click')
    await flushPromises()

    const conversations = useConversationsStore()
    expect(conversations.currentId).toBe('c2')
    expect(sse.openConversationStream).toHaveBeenCalledTimes(1)
    expect(vi.mocked(sse.openConversationStream).mock.calls.at(-1)?.[0]).toBe('c2')
    expect(
      wrapper.find('[data-testid="conversation-item-c2"]').classes(),
    ).toContain('conversation-item--active')
  })

  it('新建对话会创建、加入列表并切为当前', async () => {
    const connection = useConnectionStore()
    connection.markStreamOpen()

    const wrapper = mount(ConversationListPanel, { global: { plugins: [pinia] } })
    await flushPromises()

    await wrapper.find('[data-testid="conversation-new"]').trigger('click')
    await flushPromises()

    expect(endpoints.createConversation).toHaveBeenCalledTimes(1)
    const conversations = useConversationsStore()
    expect(conversations.currentId).toBe('c-new')
    expect(wrapper.find('[data-testid="conversation-item-c-new"]').exists()).toBe(true)
    expect(sse.openConversationStream).toHaveBeenCalledTimes(1)
    expect(vi.mocked(sse.openConversationStream).mock.calls.at(-1)?.[0]).toBe('c-new')
  })
})

describe('FD.5 对话列表：删除对话', () => {
  function mountPanel() {
    return mount(ConversationListPanel, {
      global: { plugins: [pinia] },
      attachTo: document.body,
    })
  }

  it('每个对话有删除入口；确认后调用删除接口并从列表移除', async () => {
    const connection = useConnectionStore()
    connection.markStreamOpen()

    const wrapper = mountPanel()
    await flushPromises()
    expect(
      wrapper.find('[data-testid="conversation-delete-c2"]').exists(),
    ).toBe(true)

    await confirmDelete(wrapper, 'c2')

    expect(endpoints.deleteConversation).toHaveBeenCalledWith('c2')
    const conversations = useConversationsStore()
    expect(conversations.conversations.map((c) => c.conversation_id)).toEqual(['c1'])
    expect(wrapper.find('[data-testid="conversation-item-c2"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('取消确认不删除', async () => {
    const connection = useConnectionStore()
    connection.markStreamOpen()

    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="conversation-delete-c2"]').trigger('click')
    await flushPromises()
    const cancel = Array.from(
      document.querySelectorAll('.n-popconfirm__action button'),
    ).find((b) => (b.textContent ?? '').includes('取消')) as
      | HTMLButtonElement
      | undefined
    cancel?.click()
    await flushPromises()

    expect(endpoints.deleteConversation).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="conversation-item-c2"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('删除当前对话会关闭它（回到空白态）', async () => {
    const connection = useConnectionStore()
    connection.markStreamOpen()

    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('[data-testid="conversation-item-c1"]').trigger('click')
    await flushPromises()
    const conversations = useConversationsStore()
    expect(conversations.currentId).toBe('c1')

    await confirmDelete(wrapper, 'c1')

    expect(conversations.currentId).toBeNull()
    expect(wrapper.find('[data-testid="conversation-item-c1"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
