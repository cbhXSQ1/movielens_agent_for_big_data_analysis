// FB.7 · 消息与节点 store：当前对话的消息（按 node_id 拼装）与节点表。
// 只吃四类推送事件：`message_delta` / `message_done` / `node_added` / `error`
// （由 conversations.ts 的 onEvent 分流进来）。用事件信封的 `seq`（对话内单调递增）
// 丢弃乱序 / 重复帧。发送消息经 endpoints.sendByMode，LLM 配置只在本次请求内传出（见 settings）。
//
// 消息按 `node_id` 累积：message_delta 追加增量文本，message_done 用权威完整文本收尾。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listMessages, sendByMode } from '@/api/endpoints'
import type {
  AgentMode,
  AnyStreamEnvelope,
  ErrorEnvelope,
  GuardSpan,
  LlmConfigOverride,
  MessageDeltaData,
  MessageHistoryItem,
  MessageDoneData,
  NodeAddedData,
  NodeId,
  NodeRole,
  ReasoningEffort,
  RoundEnvelope,
  TreeNode,
} from '@/api/types'
import { useSettingsStore } from './settings'

/** chat 关注的四类事件（seq 丢弃只在这四类之间比较）。 */
export type ChatEnvelope = Extract<
  AnyStreamEnvelope,
  { event: 'message_delta' | 'message_done' | 'node_added' | 'error' }
>

export interface ChatMessage {
  node_id: NodeId
  role: NodeRole
  text: string
  done: boolean
  parent_id: NodeId | null
  created_at: number | null
  error: ErrorEnvelope | null
  warning?: string | null
  warningSpans?: GuardSpan[]
}

export interface SendChatPayload {
  text?: string
  tool?: string
  params?: Record<string, unknown>
  /** 思考档位（§3.3）：仅 llm 模式透传；缺省不发该字段。 */
  reasoning_effort?: ReasoningEffort
}

export interface SendChatOptions {
  llm?: LlmConfigOverride
  timeoutMs?: number
  signal?: AbortSignal
}

export const useChatStore = defineStore('chat', () => {
  const messagesByNode = ref<Record<NodeId, ChatMessage>>({})
  const order = ref<NodeId[]>([])
  const nodes = ref<TreeNode[]>([])
  /**
   * 当前所在节点（对话「当前分支到当前位置」的末端）：
   *   - loadHistory（首屏 / 切换对话）设为该对话的 `current_node_id`；
   *   - `node_added` 到达时推进到该新节点（当前支在增长）；
   *   - 从历史节点 N 分支后由 tree store 设为 N。
   * 为空时 `visibleMessages` 回落全部消息。
   */
  const currentNodeId = ref<NodeId | null>(null)
  const lastSeq = ref(0)
  const streamingNodeId = ref<NodeId | null>(null)
  const lastError = ref<ErrorEnvelope | null>(null)
  const sending = ref(false)
  /** 每轮助手消息带来的产物 id（§3.12）：消息 node_id → 产物 id 列表。 */
  const artifactIdsByNode = ref<Record<NodeId, string[]>>({})
  /** 产物先于助手消息到达时的暂存（助手消息出现后并入）。 */
  const pendingArtifactIds = ref<string[]>([])
  /** 数字提示先于助手消息到达时的暂存（按 node_id 等待对应消息出现）。 */
  const pendingGuards = ref<
    Record<NodeId, { warning: string | null; warningSpans: GuardSpan[] }>
  >({})

  const messages = computed<ChatMessage[]>(() =>
    order.value
      .map((id) => messagesByNode.value[id])
      .filter((m): m is ChatMessage => Boolean(m)),
  )

  /** 从 `nodes` 的血缘（parent_id）求「根 → tip」这条路径上的节点集合；tip 不在表内返回 null。 */
  function pathToRoot(tip: NodeId): Set<NodeId> | null {
    const parentById: Record<NodeId, NodeId | null> = {}
    for (const n of nodes.value) parentById[n.node_id] = n.parent_id
    if (!(tip in parentById)) return null
    const path = new Set<NodeId>()
    let cursor: NodeId | null = tip
    while (cursor !== null) {
      if (path.has(cursor)) break
      path.add(cursor)
      cursor = parentById[cursor] ?? null
    }
    return path
  }

  /**
   * 对话框**应当显示**的消息：只保留「根 → 当前节点」这条路径上的消息（含当前节点）。
   * 当前节点之后（更新）的消息、其它分支上的消息一律隐藏（仍留在树里）。
   * 当前节点为空 / 不在节点表内时回落全部消息（保持既有行为）。
   */
  const visibleMessages = computed<ChatMessage[]>(() => {
    const tip = currentNodeId.value
    if (tip === null) return messages.value
    const path = pathToRoot(tip)
    if (!path) return messages.value
    return messages.value.filter((m) => path.has(m.node_id))
  })
  /** 当前正在流式输出的文本（打字机视图可直接绑定）。 */
  const streamingText = computed(() => {
    const id = streamingNodeId.value
    return id === null ? '' : (messagesByNode.value[id]?.text ?? '')
  })

  function ensure(
    node_id: NodeId,
    role: NodeRole = 'assistant',
    parent_id: NodeId | null = null,
  ): ChatMessage {
    let msg = messagesByNode.value[node_id]
    if (!msg) {
      msg = {
        node_id,
        role,
        text: '',
        done: false,
        parent_id,
        created_at: null,
        error: null,
      }
      messagesByNode.value[node_id] = msg
      order.value.push(node_id)
      if (role === 'assistant') {
        flushPendingArtifacts(node_id)
        flushPendingGuard(node_id)
      }
    }
    return msg
  }

  /** 最近一条助手消息的 node_id（产物落位用）。 */
  function latestAssistantId(): NodeId | null {
    for (let i = order.value.length - 1; i >= 0; i--) {
      const id = order.value[i]
      if (messagesByNode.value[id]?.role === 'assistant') return id
    }
    return null
  }

  function appendArtifactId(nodeId: NodeId, artifactId: string): void {
    const list =
      artifactIdsByNode.value[nodeId] ??
      (artifactIdsByNode.value[nodeId] = [])
    if (!list.includes(artifactId)) list.push(artifactId)
  }

  function flushPendingArtifacts(nodeId: NodeId): void {
    if (!pendingArtifactIds.value.length) return
    for (const id of pendingArtifactIds.value) appendArtifactId(nodeId, id)
    pendingArtifactIds.value = []
  }

  function applyGuard(
    nodeId: NodeId,
    warning: string | null,
    warningSpans: GuardSpan[],
  ): void {
    const text = typeof warning === 'string' && warning.length > 0 ? warning : null
    const spans = Array.isArray(warningSpans) ? [...warningSpans] : []
    if (text === null && !spans.length) return

    const msg = messagesByNode.value[nodeId]
    if (msg) {
      msg.warning = text
      msg.warningSpans = spans
      return
    }
    pendingGuards.value[nodeId] = { warning: text, warningSpans: spans }
  }

  function flushPendingGuard(nodeId: NodeId): void {
    const pending = pendingGuards.value[nodeId]
    if (!pending) return
    const msg = messagesByNode.value[nodeId]
    if (!msg) return
    msg.warning = pending.warning
    msg.warningSpans = pending.warningSpans
    delete pendingGuards.value[nodeId]
  }

  /**
   * `artifact_added` 分流入口：把产物归到**当前 / 最近一条助手消息**下。
   * 尚无助手消息时暂存，待助手消息出现再并入（保证不丢）。
   */
  function attachArtifact(artifactId: string): void {
    if (!artifactId) return
    const target = streamingNodeId.value ?? latestAssistantId()
    if (target === null) {
      if (!pendingArtifactIds.value.includes(artifactId)) {
        pendingArtifactIds.value.push(artifactId)
      }
      return
    }
    appendArtifactId(target, artifactId)
  }

  /** 某条消息关联的产物 id 列表（无则空数组）。 */
  function artifactIdsFor(nodeId: NodeId): string[] {
    return artifactIdsByNode.value[nodeId] ?? []
  }

  /** `message_delta`：把增量文本追加到该 node。 */
  function applyDelta(data: MessageDeltaData): void {
    const msg = ensure(data.node_id, 'assistant')
    msg.text += data.text ?? ''
    msg.done = false
    streamingNodeId.value = data.node_id
  }

  /** `message_done`：以完整文本收尾（权威覆盖）。 */
  function applyDone(data: MessageDoneData): void {
    const msg = ensure(data.node_id, 'assistant')
    if (typeof data.text === 'string') msg.text = data.text
    msg.done = true
    if (streamingNodeId.value === data.node_id) streamingNodeId.value = null
  }

  /** `node_added`：登记节点（供对话树/排序用）。 */
  function applyNodeAdded(data: NodeAddedData): void {
    const node: TreeNode = {
      node_id: data.node_id,
      parent_id: data.parent_id,
      role: data.role,
      created_at: data.created_at,
      summary: '',
      seq: data.node_id,
    }
    const idx = nodes.value.findIndex((n) => n.node_id === data.node_id)
    if (idx >= 0) nodes.value[idx] = { ...nodes.value[idx], ...node }
    else nodes.value.push(node)

    if (data.role === 'user') {
      const existing = messagesByNode.value[data.node_id]
      if (!existing) ensure(data.node_id, data.role, data.parent_id)
      else existing.parent_id = data.parent_id
    }
    // 当前支在增长：把当前节点推进到刚到达的新节点（可见路径随之延长）。
    currentNodeId.value = data.node_id
  }

  /** `error`：记到最近错误；带 node_id 时挂到对应消息上。 */
  function applyError(data: { error: ErrorEnvelope; node_id?: NodeId }): void {
    lastError.value = data.error
    if (data.node_id !== undefined) {
      const msg = ensure(data.node_id, 'assistant')
      msg.error = data.error
      msg.done = true
    }
  }

  /**
   * 事件总入口：先按 seq 丢弃乱序 / 重复帧，再落到四类处理。
   * 注意 seq 只在 chat 关注的四类事件间比较（任务/工具事件另由各自 store 处理）。
   */
  function applyEvent(envelope: ChatEnvelope): void {
    if (envelope.seq <= lastSeq.value) return
    lastSeq.value = envelope.seq
    switch (envelope.event) {
      case 'message_delta':
        applyDelta(envelope.data)
        break
      case 'message_done':
        applyDone(envelope.data)
        break
      case 'node_added':
        applyNodeAdded(envelope.data)
        break
      case 'error':
        applyError(envelope.data)
        break
    }
  }

  /** 用整棵树hydrate（供对话树/读树后回填）。 */
  function setNodes(list: TreeNode[]): void {
    nodes.value = Array.isArray(list) ? [...list] : []
  }

  function setLastSeq(seq: number): void {
    lastSeq.value = seq
  }

  /** 设置当前所在节点（分支 / 切树后由 tree store 调用）。 */
  function setCurrentNode(nodeId: NodeId | null): void {
    currentNodeId.value = nodeId
  }

  /**
   * 发送一条消息（offline / llm）。LLM 配置：优先本次 options.llm，否则取本地 settings
   * （仅在本次请求内传出，不落服务端）。
   */
  async function send(
    conversationId: string,
    mode: AgentMode,
    payload: SendChatPayload,
    options: SendChatOptions = {},
  ): Promise<RoundEnvelope> {
    sending.value = true
    try {
      const settings = useSettingsStore()
      const llm = options.llm ?? settings.toOverride()
      const envelope = await sendByMode(conversationId, mode, payload, {
        ...(options.timeoutMs !== undefined ? { timeoutMs: options.timeoutMs } : {}),
        ...(options.signal ? { signal: options.signal } : {}),
        ...(llm ? { llm } : {}),
      })
      if (envelope.error) lastError.value = envelope.error
      if (payload.text && envelope.user_node_id !== undefined) {
        const userMsg = ensure(envelope.user_node_id, 'user')
        userMsg.text = payload.text
        userMsg.done = true
      }
      if (
        envelope.node_id !== undefined &&
        (envelope.guard_warning != null || (envelope.guard_spans?.length ?? 0) > 0)
      ) {
        applyGuard(
          envelope.node_id,
          envelope.guard_warning ?? null,
          envelope.guard_spans ?? [],
        )
      }
      return envelope
    } finally {
      sending.value = false
    }
  }

  /**
   * 用后端消息历史水合当前对话（刷新 / 切换后恢复对话内容）。
   * `artifactIds` 为该对话的产物 id，挂到最近一条助手消息下（供对话里的产物链接）。
   * `tip` 为首屏的当前节点（取该对话的 `current_node_id`）：可见路径以它为止，
   * 其之后（更新）的消息隐藏。缺省为空时回落全部消息。
   * 注意：**不**改 lastSeq（事件 seq 与节点 seq 不同源）。
   */
  async function loadHistory(
    conversationId: string,
    artifactIds: string[] = [],
    tip: NodeId | null = null,
  ): Promise<void> {
    let items: MessageHistoryItem[] = []
    try {
      const res = await listMessages(conversationId)
      items = res.messages ?? []
    } catch {
      return
    }
    const byNode: Record<NodeId, ChatMessage> = {}
    const ord: NodeId[] = []
    const nodeList: TreeNode[] = []
    for (const it of items) {
      const role: NodeRole = it.role === 'user' ? 'user' : 'assistant'
      byNode[it.node_id] = {
        node_id: it.node_id,
        role,
        text: it.content ?? '',
        done: true,
        parent_id: it.parent_id ?? null,
        created_at: it.created_at ?? null,
        error: null,
      }
      ord.push(it.node_id)
      nodeList.push({
        node_id: it.node_id,
        parent_id: it.parent_id ?? null,
        role: it.role,
        created_at: it.created_at ?? 0,
        summary: '',
        seq: it.seq ?? it.node_id,
      })
    }
    messagesByNode.value = byNode
    order.value = ord
    nodes.value = nodeList
    currentNodeId.value = tip
    artifactIdsByNode.value = {}
    pendingArtifactIds.value = []
    pendingGuards.value = {}
    if (artifactIds.length && ord.length) {
      const lastAssistant = [...ord]
        .reverse()
        .find((id) => byNode[id]?.role === 'assistant')
      const target = lastAssistant ?? ord[ord.length - 1]
      artifactIdsByNode.value[target] = [...artifactIds]
    }
  }

  function reset(): void {
    messagesByNode.value = {}
    order.value = []
    nodes.value = []
    currentNodeId.value = null
    lastSeq.value = 0
    streamingNodeId.value = null
    lastError.value = null
    artifactIdsByNode.value = {}
    pendingArtifactIds.value = []
    pendingGuards.value = {}
  }

  return {
    messagesByNode,
    order,
    nodes,
    currentNodeId,
    lastSeq,
    streamingNodeId,
    lastError,
    sending,
    artifactIdsByNode,
    pendingArtifactIds,
    messages,
    streamingText,
    visibleMessages,
    ensure,
    applyDelta,
    applyDone,
    applyNodeAdded,
    applyError,
    applyEvent,
    attachArtifact,
    artifactIdsFor,
    setNodes,
    setLastSeq,
    setCurrentNode,
    send,
    loadHistory,
    reset,
  }
})
