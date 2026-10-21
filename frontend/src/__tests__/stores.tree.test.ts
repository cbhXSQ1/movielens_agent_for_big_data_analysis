// 阶段 F · 对话树 store 单测（读树 / 读单节点 / 切分支）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const endpoints = vi.hoisted(() => ({
  health: vi.fn(),
  createConversation: vi.fn(),
  listConversations: vi.fn(),
  listTasks: vi.fn(),
  getTask: vi.fn(),
  getTaskResult: vi.fn(),
  fetchTools: vi.fn(),
  sendByMode: vi.fn(),
  readTree: vi.fn(),
  readNode: vi.fn(),
  branchFrom: vi.fn(),
  listMessages: vi.fn(),
  deleteConversation: vi.fn(),
}))
vi.mock('@/api/endpoints', () => endpoints)

import { useTreeStore } from '@/stores/tree'
import { useConversationsStore } from '@/stores/conversations'
import { useChatStore } from '@/stores/chat'
import type { TreeNode } from '@/api/types'

const nodes: TreeNode[] = [
  { node_id: 1, parent_id: null, role: 'user', created_at: 1, summary: 'q1', seq: 1 },
  { node_id: 2, parent_id: 1, role: 'assistant', created_at: 2, summary: 'a1', seq: 2 },
]

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('tree store · 读树', () => {
  it('load 回填节点 / 当前节点 / 分支', async () => {
    endpoints.readTree.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      nodes,
      current_node_id: 2,
      current_branch: 'c1',
      branches: [
        { branch_id: 1, from_node_id: 1, thread_id: 'c1:b1', parent_thread: 'c1', created_at: 9 },
      ],
    })

    const tree = useTreeStore()
    await tree.load('c1')

    expect(endpoints.readTree).toHaveBeenCalledWith('c1')
    expect(tree.nodes.map((n) => n.node_id)).toEqual([1, 2])
    expect(tree.currentNodeId).toBe(2)
    expect(tree.currentBranch).toBe('c1')
    expect(tree.branches).toHaveLength(1)
    expect(tree.hasTree).toBe(true)
    expect(tree.lastError).toBeNull()
  })

  it('load 失败记录 lastError', async () => {
    endpoints.readTree.mockRejectedValue(new Error('炸了'))
    const tree = useTreeStore()
    await tree.load('c1')
    expect(tree.lastError).toBe('炸了')
  })
})

describe('tree store · 读单节点', () => {
  it('loadNode 缓存命中不重复请求', async () => {
    const detail = {
      ...nodes[0],
      conversation_id: 'c1',
      content: '完整内容',
      content_ref: null,
      thread_id: 'c1',
      checkpoint_id: null,
    }
    endpoints.readNode.mockResolvedValue({ ok: true, conversation_id: 'c1', node: detail })

    const tree = useTreeStore()
    const first = await tree.loadNode('c1', 1)
    const second = await tree.loadNode('c1', 1)

    expect(endpoints.readNode).toHaveBeenCalledTimes(1)
    expect(first?.content).toBe('完整内容')
    expect(second?.content).toBe('完整内容')
  })
})

describe('tree store · 从节点继续', () => {
  it('branchFrom 调接口、重读树、更新当前并把对话回填 conversations', async () => {
    endpoints.readTree
      .mockResolvedValueOnce({
        ok: true,
        conversation_id: 'c1',
        nodes,
        current_node_id: 2,
        current_branch: 'c1',
        branches: [],
      })
      .mockResolvedValue({
        ok: true,
        conversation_id: 'c1',
        nodes,
        current_node_id: 1,
        current_branch: 'c1',
        branches: [],
      })
    endpoints.branchFrom.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      branch: { branch_id: 2, from_node_id: 1, thread_id: 'c1:b2', parent_thread: 'c1', created_at: 10 },
      from_node_id: 1,
      current_node_id: 1,
      conversation: {
        conversation_id: 'c1',
        title: 't',
        created_at: 1,
        updated_at: 10,
        current_node_id: 1,
      },
    })

    const tree = useTreeStore()
    const conversations = useConversationsStore()
    const chat = useChatStore()
    await tree.load('c1')

    const ok = await tree.branchFrom('c1', 1)

    expect(ok).toBe(true)
    expect(endpoints.branchFrom).toHaveBeenCalledWith('c1', 1)
    expect(endpoints.readTree).toHaveBeenCalledTimes(2)
    expect(tree.currentNodeId).toBe(1)
    expect(conversations.conversations[0]?.current_node_id).toBe(1)
    // 分支后 chat 的当前节点被设为分支点 N（对话框只显示「根 → N」）。
    expect(chat.currentNodeId).toBe(1)
  })
})
