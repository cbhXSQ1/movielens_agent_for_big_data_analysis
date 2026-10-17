// FB.7 · 对话 store（§3.2）：对话列表 / 当前对话 / 新建 / 切换，
// 并持有当前对话的**唯一** SSE 推送流（§3.6）。切换即关旧流、开新流。
//
// 事件分发统一在这里：onEvent(envelope) 按 envelope.event 分流到
//   - tools_snapshot  → tools.applySnapshot
//   - message_delta / message_done / node_added / error → chat.applyEvent
//   - task_status / task_resumed / result_payload → tasks.*
// 流自身的开/断另通知 connection store（用于顶栏连接状态）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { createConversation, deleteConversation, listConversations } from '@/api/endpoints'
import {
  openConversationStream,
  type ConversationStream,
  type ConversationStreamOptions,
} from '@/api/sse'
import type { AnyStreamEnvelope, Conversation, ConversationId } from '@/api/types'
import { useArtifactsStore } from './artifacts'
import { useChatStore } from './chat'
import { useConnectionStore } from './connection'
import { useTasksStore } from './tasks'
import { useToolsStore } from './tools'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'

/** 读上次的当前对话编号（刷新后回到同一对话）。 */
function readPersistedConversation(): ConversationId | null {
  try {
    return typeof localStorage !== 'undefined'
      ? localStorage.getItem(SHELL_STORAGE_KEYS.conversation)
      : null
  } catch {
    return null
  }
}

function persistConversation(id: ConversationId | null): void {
  try {
    if (typeof localStorage === 'undefined') return
    if (id) localStorage.setItem(SHELL_STORAGE_KEYS.conversation, id)
    else localStorage.removeItem(SHELL_STORAGE_KEYS.conversation)
  } catch {
    /* 存储不可用时静默忽略 */
  }
}

export const useConversationsStore = defineStore('conversations', () => {
  const conversations = ref<Conversation[]>([])
  const currentId = ref<ConversationId | null>(null)
  const loading = ref(false)
  const streamOpen = ref(false)
  const lastError = ref<string | null>(null)

  let stream: ConversationStream | null = null

  const current = computed(
    () =>
      conversations.value.find((c) => c.conversation_id === currentId.value) ??
      null,
  )
  const count = computed(() => conversations.value.length)

  /** 按事件名把一帧信封分派给对应 store。 */
  function routeEvent(envelope: AnyStreamEnvelope): void {
    switch (envelope.event) {
      case 'tools_snapshot':
        useToolsStore().applySnapshot(envelope.data.tools)
        break
      case 'task_status':
        useTasksStore().applyStatus(envelope.data)
        break
      case 'task_resumed':
        useTasksStore().applyResumed(envelope.data)
        break
      case 'result_payload':
        useTasksStore().applyResultPayload(envelope.data)
        break
      case 'artifact_added': {
        // §3.12：新增产物 → 存进该对话的产物列表，并挂到最近一条助手消息下。
        const { artifact } = envelope.data
        useArtifactsStore().add(envelope.conversation_id, artifact)
        useChatStore().attachArtifact(artifact.id)
        break
      }
      default:
        // 余下四类：message_delta / message_done / node_added / error
        useChatStore().applyEvent(envelope)
        // 当前支在增长：把列表里当前对话的 current_node_id 同步到最新节点，
        // 以免切走再切回来时用陈旧节点隐藏新消息。
        if (envelope.event === 'node_added' && currentId.value) {
          const idx = conversations.value.findIndex(
            (c) => c.conversation_id === currentId.value,
          )
          if (idx >= 0) {
            conversations.value[idx] = {
              ...conversations.value[idx],
              current_node_id: envelope.data.node_id,
            }
          }
        }
        break
    }
  }

  /** 对外暴露的事件入口（流回调与测试共用）。 */
  function handleEvent(envelope: AnyStreamEnvelope): void {
    routeEvent(envelope)
  }

  function closeStream(): void {
    if (stream) {
      stream.close()
      stream = null
    }
    streamOpen.value = false
  }

  /** 打开某对话：关旧流、切当前、重置消息/任务、清空并重载产物、开新流。 */
  function open(
    conversationId: ConversationId,
    options?: ConversationStreamOptions,
  ): void {
    if (currentId.value === conversationId && stream && !stream.isClosed) return
    closeStream()
    currentId.value = conversationId
    persistConversation(conversationId)
    useChatStore().reset()
    useTasksStore().clear()
    // §3.12 / §3.13：恢复该对话的产物列表与消息历史（刷新 / 切换后不丢内容）。
    const artifactsStore = useArtifactsStore()
    const chatStore = useChatStore()
    void (async () => {
      await artifactsStore.load(conversationId)
      const ids = artifactsStore.list(conversationId).map((a) => a.id)
      // 首屏当前节点 = 该对话的 current_node_id：可见路径以它为止（其后消息隐藏）。
      const conv = conversations.value.find(
        (c) => c.conversation_id === conversationId,
      )
      await chatStore.loadHistory(conversationId, ids, conv?.current_node_id ?? null)
    })()
    const connection = useConnectionStore()
    stream = openConversationStream(
      conversationId,
      {
        onEvent: (envelope) => routeEvent(envelope),
        onOpen: () => {
          streamOpen.value = true
          connection.markStreamOpen()
        },
        onError: () => {
          streamOpen.value = false
          connection.markStreamClosed()
        },
        onClose: () => {
          streamOpen.value = false
          connection.markStreamClosed()
        },
      },
      options,
    )
  }

  async function load(): Promise<Conversation[]> {
    loading.value = true
    try {
      const res = await listConversations()
      conversations.value = res.conversations ?? []
      lastError.value = null
      return conversations.value
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '对话列表拉取失败'
      return conversations.value
    } finally {
      loading.value = false
    }
  }

  async function create(title?: string): Promise<Conversation | null> {
    try {
      const res = await createConversation(title)
      const conv = res.conversation
      if (!conv) return null
      conversations.value = [
        conv,
        ...conversations.value.filter(
          (c) => c.conversation_id !== conv.conversation_id,
        ),
      ]
      lastError.value = null
      return conv
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '新建对话失败'
      return null
    }
  }

  /** 新建并立即切换（打开其推送流）。 */
  async function createAndOpen(
    title?: string,
    options?: ConversationStreamOptions,
  ): Promise<Conversation | null> {
    const conv = await create(title)
    if (conv) open(conv.conversation_id, options)
    return conv
  }

  function switchTo(
    conversationId: ConversationId,
    options?: ConversationStreamOptions,
  ): void {
    open(conversationId, options)
  }

  /** 用一份最新对话对象 upsert 列表（供切分支后回填 current_node_id 等）。 */
  function applyConversation(conv: Conversation): void {
    const idx = conversations.value.findIndex(
      (c) => c.conversation_id === conv.conversation_id,
    )
    if (idx >= 0) {
      conversations.value[idx] = { ...conversations.value[idx], ...conv }
    } else {
      conversations.value = [conv, ...conversations.value]
    }
  }

  /**
   * 删除一个对话（§3.2 DELETE）：调用后端成功后从列表移除、清其产物缓存与已开标签；
   * 若删的正是当前对话，则关闭它（清空当前、清 chat/tasks，回到空白「新对话」态），
   * 并清掉持久化的「当前对话」。返回是否成功（失败信息在 `lastError`）。
   */
  async function remove(conversationId: ConversationId): Promise<boolean> {
    try {
      await deleteConversation(conversationId)
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '删除对话失败'
      return false
    }
    conversations.value = conversations.value.filter(
      (c) => c.conversation_id !== conversationId,
    )
    useArtifactsStore().clear(conversationId)
    if (currentId.value === conversationId) {
      close()
      useChatStore().reset()
      useTasksStore().clear()
    }
    lastError.value = null
    return true
  }

  /** 关闭当前流并清空当前选中。 */
  function close(): void {
    closeStream()
    currentId.value = null
    persistConversation(null)
  }

  /** 启动恢复：上次的当前对话若仍在列表里，就重新打开（刷新后不退出该对话）。 */
  function restoreCurrent(): boolean {
    const id = readPersistedConversation()
    if (!id) return false
    if (!conversations.value.some((c) => c.conversation_id === id)) return false
    open(id)
    return true
  }

  function reset(): void {
    close()
    conversations.value = []
    useArtifactsStore().clear()
    lastError.value = null
  }

  return {
    conversations,
    currentId,
    loading,
    streamOpen,
    lastError,
    current,
    count,
    routeEvent,
    handleEvent,
    open,
    load,
    create,
    createAndOpen,
    switchTo,
    applyConversation,
    remove,
    close,
    restoreCurrent,
    reset,
  }
})
