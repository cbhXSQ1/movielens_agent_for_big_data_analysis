import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { FakeEventSource, fakeFactory, streamEnvelope } from './fakeEventSource'

vi.mock('@/api/endpoints', () => ({
  health: vi.fn(async () => ({
    ok: true,
    service: 'agent-http-api/2.0',
    scope_default: 'full',
    tools: { count: 1, sources: ['local'], available: true },
    llm: { supported: false, configured: false },
  })),
  createConversation: vi.fn(),
  listConversations: vi.fn(async () => ({ ok: true, conversations: [] })),
  listTasks: vi.fn(async () => ({ ok: true, conversation_id: 'c1', tasks: [] })),
  getTask: vi.fn(),
  getTaskResult: vi.fn(),
  listArtifacts: vi.fn(async () => ({
    ok: true,
    conversation_id: 'c1',
    artifacts: [],
  })),
  fetchTools: vi.fn(async () => ({ ok: true, tools: [] })),
  sendByMode: vi.fn(),
  listMessages: vi.fn(async () => ({
    ok: true,
    conversation_id: 'c1',
    messages: [],
  })),
  deleteConversation: vi.fn(async (id: string) => ({
    ok: true,
    conversation_id: id,
  })),
}))

import { useConversationsStore } from '@/stores/conversations'
import { useArtifactsStore } from '@/stores/artifacts'
import { useChatStore } from '@/stores/chat'
import { useToolsStore } from '@/stores/tools'
import { useTasksStore } from '@/stores/tasks'
import { useConnectionStore } from '@/stores/connection'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'
import { deleteConversation } from '@/api/endpoints'

beforeEach(() => {
  setActivePinia(createPinia())
  FakeEventSource.reset()
})

describe('conversations · 流生命周期', () => {
  it('打开对话开一条流；切换后旧流被关、新流被开', () => {
    const conv = useConversationsStore()

    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    expect(FakeEventSource.instances).toHaveLength(1)
    const first = FakeEventSource.instances[0]
    expect(first.url).toContain('/api/conversations/c1/stream')
    expect(conv.currentId).toBe('c1')

    conv.open('c2', { eventSourceFactory: fakeFactory, reconnect: false })
    expect(first.closed).toBe(true)
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances[1].url).toContain(
      '/api/conversations/c2/stream',
    )
    expect(conv.currentId).toBe('c2')
  })

  it('切换对话会清空 chat / tasks 的上一对话数据', () => {
    const conv = useConversationsStore()
    const chat = useChatStore()
    const tasks = useTasksStore()

    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'hi', node_id: 1 }, 1),
    )
    tasks.applyStatus({
      agent_task_id: 'a1',
      hadoop_task_id: null,
      name: 'n',
      summary: 's',
      status: 'running',
      status_cn: '运行',
      stage: 'x',
      progress_percent: 1,
      estimated_seconds: null,
      updated_at: 0,
    })
    expect(chat.messages).toHaveLength(1)
    expect(tasks.taskList).toHaveLength(1)

    conv.open('c2', { eventSourceFactory: fakeFactory, reconnect: false })
    expect(chat.messages).toHaveLength(0)
    expect(chat.lastSeq).toBe(0)
    expect(tasks.taskList).toHaveLength(0)
  })

  it('同对话重复 open 不重开流', () => {
    const conv = useConversationsStore()
    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].closed).toBe(false)
  })

  it('流 open/close 驱动连接状态', () => {
    const conv = useConversationsStore()
    const conn = useConnectionStore()
    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]
    src.emit('open', '')
    expect(conn.streamOpen).toBe(true)
    expect(conn.connected).toBe(true)
    conv.close()
    expect(conn.streamOpen).toBe(false)
  })
})

describe('conversations · 九类事件分流', () => {
  it('各事件落到对应 store', () => {
    const conv = useConversationsStore()
    const chat = useChatStore()
    const tools = useToolsStore()
    const tasks = useTasksStore()

    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]

    src.emit(
      'tools_snapshot',
      streamEnvelope(
        'tools_snapshot',
        {
          tools: [
            {
              name: 'get_samples',
              description: '取样例',
              parameters: [],
              source: 'local',
              long_running: false,
              estimated_seconds: null,
            },
          ],
        },
        1,
      ),
    )
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: '你', node_id: 10 }, 2),
    )
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: '好', node_id: 10 }, 3),
    )
    src.emit(
      'message_done',
      streamEnvelope('message_done', { text: '你好', node_id: 10 }, 4),
    )
    src.emit(
      'node_added',
      streamEnvelope(
        'node_added',
        { node_id: 11, parent_id: 10, role: 'user', created_at: 1 },
        5,
      ),
    )
    src.emit(
      'task_status',
      streamEnvelope(
        'task_status',
        {
          agent_task_id: 'a1',
          hadoop_task_id: 'H-1',
          name: '清洗',
          summary: 's',
          status: 'running',
          status_cn: '运行',
          stage: 'x',
          progress_percent: 30,
          estimated_seconds: 60,
          updated_at: 1,
        },
        6,
      ),
    )
    src.emit(
      'task_resumed',
      streamEnvelope(
        'task_resumed',
        {
          agent_task_id: 'a1',
          hadoop_task_id: 'H-1',
          status: 'succeeded',
          result_summary: 'done',
        },
        7,
      ),
    )
    src.emit(
      'result_payload',
      streamEnvelope(
        'result_payload',
        { ok: true, task: { agent_task_id: 'a1' }, blocks: { report: 'r' } },
        8,
      ),
    )
    src.emit(
      'error',
      streamEnvelope(
        'error',
        { error: { code: 'USAGE', message: 'bad' }, node_id: 10 },
        9,
      ),
    )

    expect(tools.count).toBe(1)
    expect(tools.get('get_samples')?.description).toBe('取样例')

    const msg = chat.messagesByNode[10]
    expect(msg.text).toBe('你好')
    expect(msg.done).toBe(true)
    expect(msg.error?.code).toBe('USAGE')
    expect(chat.nodes.map((n) => n.node_id)).toContain(11)

    expect(tasks.get('a1')?.status).toBe('succeeded')
    expect(tasks.get('a1')?.hadoop_task_id).toBe('H-1')
    expect(tasks.getResult('a1')?.blocks.report).toBe('r')
  })

  it('artifact_added 落到产物 store 并挂到最近助手消息', () => {
    const conv = useConversationsStore()
    const chat = useChatStore()
    const artifacts = useArtifactsStore()

    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: '完成', node_id: 3 }, 1),
    )
    src.emit(
      'artifact_added',
      streamEnvelope(
        'artifact_added',
        {
          artifact: {
            id: 'task:t1:report.md',
            title: '评估报告',
            category: '报告',
            kind: 'md',
            source: 'task_file',
            task_id: 't1',
            path: 'report.md',
            scheme_name: null,
          },
        },
        2,
      ),
    )

    expect(artifacts.list('c1').map((a) => a.id)).toContain('task:t1:report.md')
    expect(chat.artifactIdsFor(3)).toEqual(['task:t1:report.md'])
  })

  it('message_delta 追加拼接', () => {
    const conv = useConversationsStore()
    const chat = useChatStore()
    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'foo', node_id: 1 }, 1),
    )
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'bar', node_id: 1 }, 2),
    )
    expect(chat.messagesByNode[1].text).toBe('foobar')
  })

  it('乱序帧按 seq 丢弃', () => {
    const conv = useConversationsStore()
    const chat = useChatStore()
    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'A', node_id: 1 }, 5),
    )
    // seq 更小 → 丢弃
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'B', node_id: 1 }, 4),
    )
    expect(chat.messagesByNode[1].text).toBe('A')
    expect(chat.lastSeq).toBe(5)
  })

  it('重复 seq（等于 lastSeq）也丢弃', () => {
    const conv = useConversationsStore()
    const chat = useChatStore()
    conv.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    const src = FakeEventSource.instances[0]
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'A', node_id: 1 }, 5),
    )
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'B', node_id: 1 }, 5),
    )
    expect(chat.messagesByNode[1].text).toBe('A')
  })
})

describe('conversations · 删除对话', () => {
  const conv = (id: string, title: string) => ({
    conversation_id: id,
    title,
    created_at: 1,
    updated_at: 1,
    current_node_id: null,
  })

  it('删除非当前对话：从列表移除并清其产物缓存', async () => {
    const store = useConversationsStore()
    const artifacts = useArtifactsStore()
    store.applyConversation(conv('c1', '一'))
    store.applyConversation(conv('c2', '二'))
    store.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    await flushPromises()
    artifacts.add('c2', {
      id: 'a1',
      title: '报告',
      category: '报告',
      kind: 'md',
      source: 'task_file',
      task_id: null,
      path: 'report.md',
      scheme_name: null,
    })

    const ok = await store.remove('c2')

    expect(ok).toBe(true)
    expect(vi.mocked(deleteConversation)).toHaveBeenCalledWith('c2')
    expect(store.conversations.map((c) => c.conversation_id)).toEqual(['c1'])
    expect(store.currentId).toBe('c1')
    expect(artifacts.list('c2')).toEqual([])
  })

  it('删除当前对话：关闭它、清 chat/tasks、清持久化的当前对话', async () => {
    const store = useConversationsStore()
    const chat = useChatStore()
    const tasks = useTasksStore()
    store.applyConversation(conv('c1', '一'))
    store.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    await flushPromises()
    const src = FakeEventSource.instances[0]
    src.emit(
      'message_delta',
      streamEnvelope('message_delta', { text: 'hi', node_id: 1 }, 1),
    )
    tasks.applyStatus({
      agent_task_id: 'a1',
      hadoop_task_id: null,
      name: 'n',
      summary: 's',
      status: 'running',
      status_cn: '运行',
      stage: 'x',
      progress_percent: 1,
      estimated_seconds: null,
      updated_at: 0,
    })
    expect(localStorage.getItem(SHELL_STORAGE_KEYS.conversation)).toBe('c1')
    expect(chat.messages).toHaveLength(1)
    expect(tasks.taskList).toHaveLength(1)

    const ok = await store.remove('c1')

    expect(ok).toBe(true)
    expect(store.conversations).toHaveLength(0)
    expect(store.currentId).toBeNull()
    expect(localStorage.getItem(SHELL_STORAGE_KEYS.conversation)).toBeNull()
    expect(chat.messages).toHaveLength(0)
    expect(tasks.taskList).toHaveLength(0)
  })

  it('删除失败：保留列表并记 lastError', async () => {
    const store = useConversationsStore()
    store.applyConversation(conv('c1', '一'))
    vi.mocked(deleteConversation).mockRejectedValueOnce(new Error('后端炸了'))

    const ok = await store.remove('c1')

    expect(ok).toBe(false)
    expect(store.conversations.map((c) => c.conversation_id)).toEqual(['c1'])
    expect(store.lastError).toBe('后端炸了')
  })
})

describe('conversations · 当前节点同步', () => {
  it('node_added 同步当前对话的 current_node_id（切走再切回仍显示当前路径）', async () => {
    const store = useConversationsStore()
    store.applyConversation({
      conversation_id: 'c1',
      title: '一',
      created_at: 1,
      updated_at: 1,
      current_node_id: null,
    })
    store.open('c1', { eventSourceFactory: fakeFactory, reconnect: false })
    await flushPromises()

    const src = FakeEventSource.instances[0]
    src.emit(
      'node_added',
      streamEnvelope(
        'node_added',
        { node_id: 7, parent_id: null, role: 'user', created_at: 1 },
        1,
      ),
    )

    expect(
      store.conversations.find((c) => c.conversation_id === 'c1')
        ?.current_node_id,
    ).toBe(7)
  })
})
