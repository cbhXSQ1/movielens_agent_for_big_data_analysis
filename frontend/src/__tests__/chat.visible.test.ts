// 分支后「可见路径」：对话框只显示「根 → 当前节点」这条路径上的消息，
// 当前节点之后（更新 / 旧支）的消息隐藏但仍留在树里。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listMessages: vi.fn(),
  sendByMode: vi.fn(),
}))
vi.mock('@/api/endpoints', () => api)

import { useChatStore } from '@/stores/chat'
import type { TreeNode } from '@/api/types'

/** 一条 1(用户)→2(助手)→3(用户)→4(助手) 的线性历史。 */
const chain: TreeNode[] = [
  { node_id: 1, parent_id: null, role: 'user', created_at: 1, summary: '', seq: 1 },
  { node_id: 2, parent_id: 1, role: 'assistant', created_at: 2, summary: '', seq: 2 },
  { node_id: 3, parent_id: 2, role: 'user', created_at: 3, summary: '', seq: 3 },
  { node_id: 4, parent_id: 3, role: 'assistant', created_at: 4, summary: '', seq: 4 },
]

function seedChain(chat: ReturnType<typeof useChatStore>): void {
  const u1 = chat.ensure(1, 'user')
  u1.text = '问题一'
  u1.done = true
  const a2 = chat.ensure(2, 'assistant')
  a2.text = '回复一'
  a2.done = true
  const u3 = chat.ensure(3, 'user')
  u3.text = '问题二'
  u3.done = true
  const a4 = chat.ensure(4, 'assistant')
  a4.text = '回复二'
  a4.done = true
  chat.setNodes(chain)
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.listMessages.mockReset()
  api.sendByMode.mockReset()
})

describe('chat · 可见路径（visibleMessages）', () => {
  it('当前节点为空时回落全部消息（保持既有行为）', () => {
    const chat = useChatStore()
    seedChain(chat)
    expect(chat.currentNodeId).toBeNull()
    expect(chat.visibleMessages.map((m) => m.node_id)).toEqual([1, 2, 3, 4])
  })

  it('分支到 N 后只显示「根 → N」：N 之后的消息隐藏但仍在 messages 里', () => {
    const chat = useChatStore()
    seedChain(chat)

    chat.setCurrentNode(2)

    expect(chat.visibleMessages.map((m) => m.node_id)).toEqual([1, 2])
    // 旧支（更新）的消息仍保留在 store 里，只是不显示。
    expect(chat.messages.map((m) => m.node_id)).toEqual([1, 2, 3, 4])
  })

  it('在本支继续：node_added 推进当前节点，新消息可见、旧隐藏仍在隐藏', () => {
    const chat = useChatStore()
    seedChain(chat)
    chat.setCurrentNode(2)

    // 在本支（父 = 当前节点 2）新增一条用户消息 → 当前节点推进到 5。
    chat.applyNodeAdded({ node_id: 5, parent_id: 2, role: 'user', created_at: 5 })

    expect(chat.currentNodeId).toBe(5)
    expect(chat.visibleMessages.map((m) => m.node_id)).toEqual([1, 2, 5])
    // 被迫隐藏的 3、4 依旧隐藏，且未丢失。
    expect(chat.messages.map((m) => m.node_id)).toContain(3)
    expect(chat.messages.map((m) => m.node_id)).toContain(4)
  })

  it('loadHistory 按传入的当前节点过滤（切换对话只显示当前路径）', async () => {
    api.listMessages.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      messages: [
        { node_id: 1, parent_id: null, role: 'user', content: 'q1', created_at: 1, seq: 1 },
        { node_id: 2, parent_id: 1, role: 'assistant', content: 'a1', created_at: 2, seq: 2 },
        { node_id: 3, parent_id: 2, role: 'user', content: 'q2', created_at: 3, seq: 3 },
        { node_id: 4, parent_id: 3, role: 'assistant', content: 'a2', created_at: 4, seq: 4 },
      ],
    })
    const chat = useChatStore()

    await chat.loadHistory('c1', [], 2)
    expect(chat.currentNodeId).toBe(2)
    expect(chat.visibleMessages.map((m) => m.node_id)).toEqual([1, 2])
    expect(chat.messages).toHaveLength(4)
  })

  it('当前节点不在节点表内时回落全部（容错，不空白）', () => {
    const chat = useChatStore()
    seedChain(chat)
    chat.setCurrentNode(999)
    expect(chat.visibleMessages.map((m) => m.node_id)).toEqual([1, 2, 3, 4])
  })
})
