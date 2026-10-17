// store 单测共用：可注入的假 EventSource 与信封构造器。
import type { EventSourceLike } from '@/api/sse'

export class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = []

  static reset(): void {
    FakeEventSource.instances = []
  }

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

  emit(type: string, data: unknown): void {
    const payload = typeof data === 'string' ? data : JSON.stringify(data)
    for (const fn of this.listeners.get(type) ?? []) {
      fn({ data: payload } as MessageEvent)
    }
  }
}

export function fakeFactory(url: string): FakeEventSource {
  return new FakeEventSource(url)
}

/** 造一条 `data:` 行的 JSON 信封字符串（§3.6）。 */
export function streamEnvelope(
  event: string,
  data: unknown,
  seq: number,
  conversationId = 'c1',
): string {
  return JSON.stringify({
    event,
    conversation_id: conversationId,
    seq,
    ts: 1729000000,
    data,
  })
}
