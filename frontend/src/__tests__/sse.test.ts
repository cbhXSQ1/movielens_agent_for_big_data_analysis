import { describe, it, expect, vi } from 'vitest'
import {
  ConversationStream,
  conversationStreamUrl,
  isHeartbeatComment,
  openConversationStream,
  parseStreamEnvelope,
} from '@/api/sse'
import { API_BASE } from '@/api/config'
import { STREAM_EVENT_NAMES, type StreamEventName } from '@/api/types'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  readyState = 0
  closed = false
  private listeners = new Map<string, Set<(event: MessageEvent) => void>>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (event: MessageEvent) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)?.add(fn)
  }

  removeEventListener(type: string, fn: (event: MessageEvent) => void): void {
    this.listeners.get(type)?.delete(fn)
  }

  close(): void {
    this.closed = true
    this.readyState = 2
  }

  emit(type: string, data: string): void {
    for (const fn of this.listeners.get(type) ?? []) {
      fn({ data } as MessageEvent)
    }
  }
}

function factory(url: string): FakeEventSource {
  return new FakeEventSource(url)
}

function envelopeJson(event: StreamEventName, data: unknown): string {
  return JSON.stringify({
    event,
    conversation_id: 'c0001',
    seq: 1,
    ts: 1729000000,
    data,
  })
}

describe('sse · 纯解析（心跳/坏帧）', () => {
  it('识别注释心跳', () => {
    expect(isHeartbeatComment(': keep-alive')).toBe(true)
    expect(isHeartbeatComment('data: {}')).toBe(false)
  })

  it('注释/空串/非 JSON → null', () => {
    expect(parseStreamEnvelope(': keep-alive')).toBeNull()
    expect(parseStreamEnvelope('')).toBeNull()
    expect(parseStreamEnvelope('not json')).toBeNull()
    expect(parseStreamEnvelope('[]')).toBeNull()
  })

  it('合法信封解析成功', () => {
    const parsed = parseStreamEnvelope(envelopeJson('message_done', { text: 'hi', node_id: 3 }))
    expect(parsed?.event).toBe('message_done')
    expect(parsed?.conversation_id).toBe('c0001')
  })
})

describe('sse · 流地址', () => {
  it('每对话一条，用 sseUrl 拼接并编码', () => {
    expect(conversationStreamUrl('c0001')).toBe(
      `${API_BASE}/api/conversations/c0001/stream`,
    )
    expect(conversationStreamUrl('c 1')).toContain('c%201')
  })
})

describe('sse · 九类事件分发', () => {
  it('构造即连接，按事件名分发并回调信封', () => {
    FakeEventSource.instances = []
    const seen: string[] = []
    const stream = openConversationStream(
      'c0001',
      { onEvent: (env) => seen.push(env.event) },
      { eventSourceFactory: factory, reconnect: false },
    )
    const source = FakeEventSource.instances[0]
    expect(source.url).toBe(`${API_BASE}/api/conversations/c0001/stream`)

    const payloads: Record<StreamEventName, unknown> = {
      tools_snapshot: { tools: [] },
      message_delta: { text: 'a', node_id: 1 },
      message_done: { text: 'ab', node_id: 1 },
      task_status: { agent_task_id: 'LT-0001' },
      task_resumed: { agent_task_id: 'LT-0001', status: 'succeeded' },
      result_payload: { task: {}, blocks: {} },
      node_added: { node_id: 2, parent_id: 1, role: 'user', created_at: 0 },
      artifact_added: {
        artifact: {
          id: 'scheme:x.json',
          title: '方案',
          category: '依据',
          kind: 'scheme',
          source: 'scheme',
          task_id: null,
          path: null,
          scheme_name: 'x.json',
        },
      },
      error: { error: { code: 'USAGE', message: 'x' } },
    }
    for (const name of STREAM_EVENT_NAMES) {
      source.emit(name, envelopeJson(name, payloads[name]))
    }

    expect(seen).toEqual([...STREAM_EVENT_NAMES])
    stream.close()
  })

  it('心跳注释与坏帧不分发事件', () => {
    FakeEventSource.instances = []
    const onEvent = vi.fn()
    const stream = openConversationStream(
      'c0001',
      { onEvent },
      { eventSourceFactory: factory, reconnect: false },
    )
    const source = FakeEventSource.instances[0]
    source.emit('message_delta', ': keep-alive')
    source.emit('message_delta', 'broken json')
    expect(onEvent).not.toHaveBeenCalled()
    stream.close()
  })
})

describe('sse · 生命周期与重连', () => {
  it('open 回调且 close 释放连接', () => {
    FakeEventSource.instances = []
    const onOpen = vi.fn()
    const onClose = vi.fn()
    const stream = openConversationStream(
      'c0001',
      { onOpen, onClose },
      { eventSourceFactory: factory, reconnect: false },
    )
    const source = FakeEventSource.instances[0]
    source.emit('open', '')
    expect(onOpen).toHaveBeenCalledTimes(1)

    stream.close()
    expect(stream.isClosed).toBe(true)
    expect(source.closed).toBe(true)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('断线安排指数退避重连', () => {
    FakeEventSource.instances = []
    const onReconnect = vi.fn()
    const stream = new ConversationStream(
      'c0001',
      { onReconnect },
      { eventSourceFactory: factory, baseDelayMs: 100, maxDelayMs: 1000 },
    )
    const source = FakeEventSource.instances[0]
    source.emit('error', '')
    expect(onReconnect).toHaveBeenCalledWith(1, 100)
    expect(source.closed).toBe(true)
    stream.close()
  })

  it('reconnect=false 时出错即关闭', () => {
    FakeEventSource.instances = []
    const onClose = vi.fn()
    const stream = openConversationStream(
      'c0001',
      { onClose },
      { eventSourceFactory: factory, reconnect: false },
    )
    const source = FakeEventSource.instances[0]
    source.emit('error', '')
    expect(stream.isClosed).toBe(true)
    expect(onClose).toHaveBeenCalled()
  })
})
