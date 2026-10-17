// FB.9 · 产物 store（§3.12）。按对话保存产物引用列表：
//   - 主来源是推送流上的 `artifact_added` 事件（经 conversations.routeEvent 分流）；
//   - `load(cid)` 用 `listArtifacts` 恢复（刷新 / 切换对话补拉）；
//   - 切换对话时由上层 clear + load 重载。
// 一律按 `artifact.id` 去重（后端保证 id 稳定可去重）。
//
// 状态分离（产物标签交互）：
//   - `byConversation`：**全部产物**（用于在对话里渲染超链接、随对话恢复）；
//   - `openByConversation`：**已打开的标签 id**（用户点击链接才产生）。
// 产物到达**不再**自动开标签；两者独立，按对话隔离（内存态）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listArtifacts } from '@/api/endpoints'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'
import type { ArtifactRef, ConversationId } from '@/api/types'

/** 从 localStorage 读「已打开的产物标签」集合（刷新后恢复）。 */
function readOpenTabs(): Record<ConversationId, string[]> {
  try {
    if (typeof localStorage === 'undefined') return {}
    const raw = localStorage.getItem(SHELL_STORAGE_KEYS.openTabs)
    if (!raw) return {}
    const obj = JSON.parse(raw)
    return obj && typeof obj === 'object' ? (obj as Record<ConversationId, string[]>) : {}
  } catch {
    return {}
  }
}

export const useArtifactsStore = defineStore('artifacts', () => {
  /** 对话编号 → 该对话的产物引用列表（保持后端给出/事件到达的顺序）。 */
  const byConversation = ref<Record<ConversationId, ArtifactRef[]>>({})
  /** 对话编号 → 该对话**已打开**的产物标签 id 列表（点击链接才新增）。 */
  const openByConversation = ref<Record<ConversationId, string[]>>(readOpenTabs())
  const loading = ref(false)
  const lastError = ref<string | null>(null)

  /** 把「已打开标签」写回 localStorage（刷新后能恢复标签栏）。 */
  function persistOpenTabs(): void {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(
          SHELL_STORAGE_KEYS.openTabs,
          JSON.stringify(openByConversation.value),
        )
      }
    } catch {
      /* 存储不可用时静默忽略 */
    }
  }

  const conversationCount = computed(() => Object.keys(byConversation.value).length)

  /** 某对话的产物列表（无则空数组）。 */
  function list(conversationId: ConversationId | null | undefined): ArtifactRef[] {
    if (!conversationId) return []
    return byConversation.value[conversationId] ?? []
  }

  /** 在某个对话内按 id 取一条产物。 */
  function find(
    conversationId: ConversationId | null | undefined,
    artifactId: string,
  ): ArtifactRef | null {
    return list(conversationId).find((a) => a.id === artifactId) ?? null
  }

  /** 某对话已打开的产物标签 id 列表（无则空数组）。 */
  function openIds(conversationId: ConversationId | null | undefined): string[] {
    if (!conversationId) return []
    return openByConversation.value[conversationId] ?? []
  }

  /** 某产物是否已开标签。 */
  function isOpen(
    conversationId: ConversationId | null | undefined,
    artifactId: string,
  ): boolean {
    return openIds(conversationId).includes(artifactId)
  }

  /** 打开某产物的标签（点链接触发；已开则幂等，不重复）。 */
  function openTab(conversationId: ConversationId | null, artifactId: string): void {
    if (!conversationId || !artifactId) return
    const current =
      openByConversation.value[conversationId] ??
      (openByConversation.value[conversationId] = [])
    if (current.includes(artifactId)) return
    current.push(artifactId)
    persistOpenTabs()
  }

  /** 关闭某产物的标签（只从已开集合移除，不删产物本身）。 */
  function closeTab(
    conversationId: ConversationId | null | undefined,
    artifactId: string,
  ): void {
    if (!conversationId) return
    const current = openByConversation.value[conversationId]
    if (!current) return
    const idx = current.indexOf(artifactId)
    if (idx >= 0) current.splice(idx, 1)
    persistOpenTabs()
  }

  /** 去重追加一条产物（`artifact_added` 事件 / 恢复合并共用）。 */
  function add(conversationId: ConversationId, artifact: ArtifactRef): void {
    if (!conversationId || !artifact || !artifact.id) return
    const current =
      byConversation.value[conversationId] ??
      (byConversation.value[conversationId] = [])
    if (current.some((a) => a.id === artifact.id)) return
    current.push(artifact)
  }

  function addMany(
    conversationId: ConversationId,
    artifacts: readonly ArtifactRef[] | null | undefined,
  ): void {
    for (const artifact of artifacts ?? []) add(conversationId, artifact)
  }

  /** 清空缓存并重载某对话的产物列表（刷新 / 切换对话）。 */
  async function load(conversationId: ConversationId): Promise<ArtifactRef[]> {
    byConversation.value[conversationId] = []
    loading.value = true
    try {
      const res = await listArtifacts(conversationId)
      addMany(conversationId, res.artifacts ?? [])
      lastError.value = null
      return list(conversationId)
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '产物列表拉取失败'
      return list(conversationId)
    } finally {
      loading.value = false
    }
  }

  /** 清空某对话的产物与已开标签；不给编号则清空全部（切换 / 关闭对话）。 */
  function clear(conversationId?: ConversationId): void {
    if (conversationId) {
      delete byConversation.value[conversationId]
      delete openByConversation.value[conversationId]
    } else {
      byConversation.value = {}
      openByConversation.value = {}
    }
    persistOpenTabs()
    lastError.value = null
  }

  return {
    byConversation,
    openByConversation,
    loading,
    lastError,
    conversationCount,
    list,
    find,
    openIds,
    isOpen,
    openTab,
    closeTab,
    add,
    addMany,
    load,
    clear,
  }
})
