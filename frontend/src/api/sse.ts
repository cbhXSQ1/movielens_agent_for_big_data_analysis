// FB.3 · SSE 客户端：每对话一条流（`EventSource`），八类事件分发、心跳、断线重连、close()。
//
// 线上格式（§3.6）：
//   event: <事件名>
//   data: {"event":"<事件名>","conversation_id":"c0001","seq":7,"ts":…,"data":{…}}
// 空闲期服务端发注释心跳 `: keep-alive`；浏览器 EventSource 会静默消化注释、保活连接，
// 注释不触发任何事件 —— 故这里把「注释心跳」的识别落在纯函数 `isHeartbeatComment` /
// `parseStreamEnvelope`（供测试与自定义解析），EventSource 路径则交由浏览器处理。
// 断线重连由本客户端显式接管（指数退避），而不是依赖 EventSource 的隐式重连。

import { sseUrl } from './config'
import type { AnyStreamEnvelope, StreamEventName } from './types'
import { STREAM_EVENT_NAMES, isStreamEventName } from './types'

/** 可能被某些实现用作命名心跳的事件名（服务端当前只发注释，这里作加性兜底）。 */
export const HEARTBEAT_EVENT_NAMES = ['ping', 'heartbeat', 'keep-alive'] as const

/** 一行是否为 SSE 注释（心跳）：以 `:` 开头。 */
export function isHeartbeatComment(line: string): boolean {
  return line.startsWith(':')
}

/**
 * 解析一帧 `data:` 负载为事件信封。
 * 空串 / 注释 / 非 JSON / 缺关键字段 → 返回 null（心跳与坏帧都据此忽略）。
 */
export function parseStreamEnvelope(raw: string): AnyStreamEnvelope | null {
  if (!raw) return null
  const text = raw.trim()
  if (!text || isHeartbeatComment(text)) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null) return null
  const env = parsed as Record<string, unknown>
  if (!isStreamEventName(env.event)) return null
  if (typeof env.conversation_id !== 'string') return null
  if (env.data === undefined || env.data === null) return null
  return parsed as AnyStreamEnvelope
}

/** 某对话推送流地址（§3.2 打开推送流）。 */
export function conversationStreamUrl(conversationId: string): string {
  return sseUrl(`/api/conversations/${encodeURIComponent(conversationId)}/stream`)
}

// ---------------------------------------------------------------------------
// 可注入的最小 EventSource 形态（便于单测注入假实现）
// ---------------------------------------------------------------------------

export interface EventSourceLike {
  readonly readyState: number
  addEventListener(type: string, listener: (event: MessageEvent) => void): void
  removeEventListener(
    type: string,
    listener: (event: MessageEvent) => void,
  ): void
  close(): void
}

export interface ConversationStreamOptions {
  /** 是否自动重连（默认 true）。 */
  reconnect?: boolean
  /** 重连基础退避（毫秒，默认 1000）。 */
  baseDelayMs?: number
  /** 重连退避上限（毫秒，默认 15000）。 */
  maxDelayMs?: number
  /** 注入 EventSource 实现（默认用浏览器全局 EventSource）。 */
  eventSourceFactory?: (url: string) => EventSourceLike
}

export interface StreamHandlers {
  /** 收到一条合法事件信封时回调（已解析为 AnyStreamEnvelope）。 */
  onEvent?: (envelope: AnyStreamEnvelope) => void
  /** 连接建立 / 重连成功。 */
  onOpen?: () => void
  /** 连接出错（重连前回调）。 */
  onError?: (error: Error) => void
  /** 安排了一次重连。 */
  onReconnect?: (attempt: number, delayMs: number) => void
  /** 流被关闭（含主动 close）。 */
  onClose?: () => void
}

function defaultFactory(url: string): EventSourceLike {
  return new EventSource(url) as unknown as EventSourceLike
}

function toError(value: unknown): Error {
  if (value instanceof Error) return value
  return new Error('SSE 连接出错')
}

/**
 * 单段对话的推送流。构造即连接；`close()` 释放。
 * 事件按名称分发到同一个 `onEvent`，由上层按 `envelope.event` 再分流（八类）。
 */
export class ConversationStream {
  readonly conversationId: string
  private readonly url: string
  private readonly handlers: StreamHandlers
  private readonly reconnect: boolean
  private readonly baseDelayMs: number
  private readonly maxDelayMs: number
  private readonly factory: (url: string) => EventSourceLike

  private source: EventSourceLike | null = null
  private bound: Array<{ type: string; fn: (event: MessageEvent) => void }> = []
  private closed = false
  private attempts = 0
  private timer: ReturnType<typeof setTimeout> | null = null

  constructor(
    conversationId: string,
    handlers: StreamHandlers = {},
    options: ConversationStreamOptions = {},
  ) {
    this.conversationId = conversationId
    this.handlers = handlers
    this.reconnect = options.reconnect ?? true
    this.baseDelayMs = options.baseDelayMs ?? 1000
    this.maxDelayMs = options.maxDelayMs ?? 15000
    this.factory = options.eventSourceFactory ?? defaultFactory
    this.url = conversationStreamUrl(conversationId)
    this.connect()
  }

  get streamUrl(): string {
    return this.url
  }

  get isClosed(): boolean {
    return this.closed
  }

  private connect(): void {
    if (this.closed) return
    const source = this.factory(this.url)
    this.source = source

    for (const name of STREAM_EVENT_NAMES) {
      this.onSource(source, name, (event) => this.dispatch(event))
    }
    for (const name of HEARTBEAT_EVENT_NAMES) {
      // 命名心跳（若有）：仅用于保活/重置，不作为业务事件分发。
      this.onSource(source, name, () => undefined)
    }
    this.onSource(source, 'open', () => {
      this.attempts = 0
      this.handlers.onOpen?.()
    })
    this.onSource(source, 'error', (event) => {
      this.handleError(event)
    })
  }

  private onSource(
    source: EventSourceLike,
    type: string,
    fn: (event: MessageEvent) => void,
  ): void {
    source.addEventListener(type, fn)
    this.bound.push({ type, fn })
  }

  private dispatch(event: MessageEvent): void {
    const envelope = parseStreamEnvelope(String(event?.data ?? ''))
    if (!envelope) return
    this.handlers.onEvent?.(envelope)
  }

  private handleError(event: MessageEvent): void {
    if (this.closed) return
    this.handlers.onError?.(toError((event as unknown as { error?: unknown })?.error))
    if (!this.reconnect) {
      this.close()
      return
    }
    this.scheduleReconnect()
  }

  private scheduleReconnect(): void {
    if (this.closed || this.timer) return
    this.attempts += 1
    const delay = Math.min(
      this.maxDelayMs,
      this.baseDelayMs * 2 ** (this.attempts - 1),
    )
    this.handlers.onReconnect?.(this.attempts, delay)
    this.teardownSource()
    this.timer = setTimeout(() => {
      this.timer = null
      this.connect()
    }, delay)
  }

  private teardownSource(): void {
    if (!this.source) return
    for (const { type, fn } of this.bound) {
      this.source.removeEventListener(type, fn)
    }
    this.bound = []
    try {
      this.source.close()
    } catch {
      /* 已关闭 */
    }
    this.source = null
  }

  /** 主动关闭：释放 EventSource、取消待重连，并回掉 onClose。 */
  close(): void {
    if (this.closed) return
    this.closed = true
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    this.teardownSource()
    this.handlers.onClose?.()
  }
}

/** 便捷入口：为某对话打开一条流。 */
export function openConversationStream(
  conversationId: string,
  handlers: StreamHandlers = {},
  options: ConversationStreamOptions = {},
): ConversationStream {
  return new ConversationStream(conversationId, handlers, options)
}

export type { StreamEventName }
