// 阶段 F · FF.1–FF.4 对话树浮窗组件测试（假树数据）。
// mock 掉 REST 与 SSE，覆盖：浮窗开关、按 parent_id 渲染节点与连边、
// 悬停出缩略（含该次提问文案）、点「切到这里」触发 branchFrom 并刷新树。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

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

const sse = vi.hoisted(() => ({
  openConversationStream: vi.fn(),
  conversationStreamUrl: (id: string) => `/api/conversations/${id}/stream`,
}))
vi.mock('@/api/sse', () => sse)

import ConversationTreePanel from '@/views/panels/ConversationTreePanel.vue'
import ConversationTreeWindow from '@/components/tree/ConversationTreeWindow.vue'
import TreeThumbnail from '@/components/tree/TreeThumbnail.vue'
import { MIN_CANVAS_W } from '@/components/tree/treeLayout'
import { useConversationsStore } from '@/stores/conversations'
import { useChatStore } from '@/stores/chat'
import { useTreeStore } from '@/stores/tree'
import type { Conversation, TreeNode } from '@/api/types'

const treeNodes: TreeNode[] = [
  {
    node_id: 1,
    parent_id: null,
    role: 'user',
    created_at: 1,
    summary: '看一下数据量',
    seq: 1,
  },
  {
    node_id: 2,
    parent_id: 1,
    role: 'assistant',
    created_at: 2,
    summary: '已统计总行数',
    seq: 2,
  },
  {
    node_id: 3,
    parent_id: 1,
    role: 'user',
    created_at: 3,
    summary: '改成清洗后再看',
    seq: 3,
  },
]

function treeResponse(current: number) {
  return {
    ok: true,
    conversation_id: 'c1',
    nodes: treeNodes,
    current_node_id: current,
    current_branch: 'c1',
    branches: [],
  }
}

const conversation = (current: number): Conversation => ({
  conversation_id: 'c1',
  title: '测试对话',
  created_at: 1,
  updated_at: 9,
  current_node_id: current,
})

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  endpoints.readTree.mockResolvedValue(treeResponse(3))
  endpoints.readNode.mockResolvedValue({
    ok: true,
    conversation_id: 'c1',
    node: {
      ...treeNodes[0],
      conversation_id: 'c1',
      content: '看一下数据量（完整内容）',
      content_ref: null,
      thread_id: 'c1',
      checkpoint_id: null,
    },
  })
  endpoints.branchFrom.mockResolvedValue({
    ok: true,
    conversation_id: 'c1',
    branch: {
      branch_id: 1,
      from_node_id: 1,
      thread_id: 'c1:b1',
      parent_thread: 'c1',
      created_at: 9,
    },
    from_node_id: 1,
    current_node_id: 1,
    conversation: conversation(1),
  })
})

function selectConversation(id = 'c1'): void {
  useConversationsStore().currentId = id
}

async function mountWindow(show = true) {
  selectConversation()
  const wrapper = mount(ConversationTreeWindow, {
    props: { show },
    global: { plugins: [pinia] },
  })
  await flushPromises()
  return wrapper
}

describe('FF.1 · 对话树浮窗开关', () => {
  it('show 控制浮窗显隐；关闭按钮请求收起', async () => {
    const hidden = await mountWindow(false)
    expect(hidden.find('[data-testid="tree-window"]').exists()).toBe(false)

    await hidden.setProps({ show: true })
    await flushPromises()
    expect(hidden.find('[data-testid="tree-window"]').exists()).toBe(true)

    await hidden.find('[data-testid="tree-close"]').trigger('click')
    await flushPromises()
    expect(hidden.find('[data-testid="tree-window"]').exists()).toBe(false)
  })

  it('点②面板不自动弹窗：先显示缩略图预览，点按钮才打开浮窗', async () => {
    selectConversation()
    const wrapper = mount(ConversationTreePanel, {
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="panel-tree"]').exists()).toBe(true)
    // 切到②不自动弹出浮窗
    expect(wrapper.find('[data-testid="tree-window"]').exists()).toBe(false)
    // 左栏以缩略图预览
    expect(wrapper.find('[data-testid="tree-thumbnail"]').exists()).toBe(true)

    await wrapper.find('[data-testid="tree-open"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="tree-window"]').exists()).toBe(true)
  })
})

describe('FF.2 · 画布与平移容器', () => {
  it('画出足尺寸画布，滚动容器承载横/纵滑块', async () => {
    const wrapper = await mountWindow()
    // 滚动容器（overflow:auto）承载横/纵滑块，拖动即平移。
    expect(wrapper.find('.tree-window__body').exists()).toBe(true)

    const canvas = wrapper.find('[data-testid="tree-canvas"]')
    expect(canvas.exists()).toBe(true)
    // 画布给出明确宽高（大图时超出视口即出现滚动条）。
    const style = canvas.attributes('style') ?? ''
    expect(style).toContain('width:')
    expect(style).toContain('height:')
    const width = Number((style.match(/width:\s*(\d+)px/) ?? [])[1])
    expect(width).toBeGreaterThanOrEqual(MIN_CANVAS_W)
  })
})

describe('FF.3 · 回合圆点渲染与连边 + 当前高亮', () => {
  it('user 节点与 assistant 节点聚成一个回合：只渲染回合 1、3 两个圆点', async () => {
    const wrapper = await mountWindow()

    // 节点 1（提问）+ 节点 2（回答）= 回合 1；节点 3 自成回合 3。
    expect(wrapper.find('[data-testid="tree-node-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tree-node-3"]').exists()).toBe(true)
    // assistant 节点 2 不再单独成节点。
    expect(wrapper.find('[data-testid="tree-node-2"]').exists()).toBe(false)
    // 回合 3 的父是回合 1 → 1 条边。
    expect(wrapper.findAll('[data-testid="tree-edge"]')).toHaveLength(1)

    expect(wrapper.find('[data-testid="tree-node-3"]').classes()).toContain(
      'tree-round--current',
    )
    expect(wrapper.find('[data-testid="tree-node-1"]').classes()).not.toContain(
      'tree-round--current',
    )
  })
})

describe('FF.3 · 悬停缩略显示该回合的提问与回复', () => {
  it('悬停回合圆点弹出缩略窗，含该回合提问', async () => {
    const wrapper = await mountWindow()

    expect(wrapper.find('[data-testid="tree-thumb"]').exists()).toBe(false)
    await wrapper.find('[data-testid="tree-node-1"]').trigger('mouseenter')
    await flushPromises()

    const thumb = wrapper.find('[data-testid="tree-thumb"]')
    expect(thumb.exists()).toBe(true)
    expect(wrapper.find('[data-testid="tree-thumb-text"]').text()).toContain(
      '看一下数据量',
    )
    // 同一回合内的 assistant 节点内容也列入「回复」。
    expect(wrapper.find('[data-testid="tree-thumb-reply"]').text()).toContain(
      '看一下数据量',
    )

    await wrapper.find('[data-testid="tree-node-1"]').trigger('mouseleave')
    await flushPromises()
    expect(wrapper.find('[data-testid="tree-thumb"]').exists()).toBe(false)
  })
})

describe('FF.4 · 在此继续（切到这里）', () => {
  it('点「在此继续」调 branchFrom(该回合的末尾节点 id)，整轮留在新支里', async () => {
    endpoints.readTree.mockReset()
    endpoints.readTree
      .mockResolvedValueOnce(treeResponse(3))
      .mockResolvedValue(treeResponse(1))

    const wrapper = await mountWindow()
    expect(endpoints.readTree).toHaveBeenCalledTimes(1)

    await wrapper.find('[data-testid="tree-switch-1"]').trigger('click')
    await flushPromises()

    // 从回合 1 的**末尾节点**（助手回复，id=2）另起一支：整轮（问 + 答）留在新支里，
    // 只把该轮之后的消息当作旧支隐藏。
    expect(endpoints.branchFrom).toHaveBeenCalledWith('c1', 2)
    expect(endpoints.readTree).toHaveBeenCalledTimes(2)

    const tree = useTreeStore()
    expect(tree.currentNodeId).toBe(1)
    // chat 的当前节点设为分支点（tip=2）→ 对话框只显示到该轮。
    expect(useChatStore().currentNodeId).toBe(2)

    expect(wrapper.find('[data-testid="tree-node-1"]').classes()).toContain(
      'tree-round--current',
    )

    // 对话对象被回填（current_node_id 同步到 1）。
    expect(useConversationsStore().conversations[0]?.current_node_id).toBe(1)
  })

  it('当前回合上的按钮被禁用', async () => {
    const wrapper = await mountWindow()
    expect(
      wrapper.find('[data-testid="tree-switch-3"]').attributes('disabled'),
    ).toBeDefined()
  })
})

describe('FF.3 · 缩略图按回合渲染', () => {
  it('渲染的圆点数量等于回合数（3 个节点 → 2 个回合）', () => {
    const wrapper = mount(TreeThumbnail, {
      props: { nodes: treeNodes, currentNodeId: 3 },
      global: { plugins: [pinia] },
    })

    expect(wrapper.findAll('[data-testid="tree-thumbnail-dot"]')).toHaveLength(
      2,
    )
  })
})
