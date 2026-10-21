// FB.7 / 阶段 F · 对话树 store（§3.5）。
// 组件不直接 fetch：读树 / 读单节点 / 从节点继续一律经本 store 走 endpoints。
// - `load`       读整棵树（前端据 parent_id 在视图层重建树）。
// - `loadNode`   读单节点完整内容（悬停缩略懒加载）。
// - `branchFrom` git 式从某节点另起一支并设为当前所在；成功后刷新树、
//                并把新的对话对象回填到 conversations store（current_node_id 同步）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  branchFrom as branchFromApi,
  readNode as readNodeApi,
  readTree as readTreeApi,
} from '@/api/endpoints'
import type {
  Branch,
  ConversationId,
  NodeDetail,
  NodeId,
  TreeNode,
} from '@/api/types'
import { useConversationsStore } from './conversations'
import { useChatStore } from './chat'

function describeError(err: unknown, fallback: string): string {
  if (err instanceof Error) return err.message
  return fallback
}

export const useTreeStore = defineStore('tree', () => {
  const conversationId = ref<ConversationId | null>(null)
  const nodes = ref<TreeNode[]>([])
  const currentNodeId = ref<NodeId | null>(null)
  const currentBranch = ref('')
  const branches = ref<Branch[]>([])
  const nodeDetails = ref<Record<NodeId, NodeDetail>>({})
  const loading = ref(false)
  const branching = ref(false)
  const lastError = ref<string | null>(null)

  const hasTree = computed(() => nodes.value.length > 0)

  function reset(): void {
    conversationId.value = null
    nodes.value = []
    currentNodeId.value = null
    currentBranch.value = ''
    branches.value = []
    nodeDetails.value = {}
    lastError.value = null
  }

  /** 读整棵树并回填当前所在节点 / 分支 / 分支表。 */
  async function load(cid: ConversationId): Promise<void> {
    loading.value = true
    try {
      const res = await readTreeApi(cid)
      conversationId.value = cid
      nodes.value = res.nodes ?? []
      currentNodeId.value = res.current_node_id ?? null
      currentBranch.value = res.current_branch ?? ''
      branches.value = res.branches ?? []
      nodeDetails.value = {}
      lastError.value = null
    } catch (err) {
      lastError.value = describeError(err, '对话树拉取失败')
    } finally {
      loading.value = false
    }
  }

  /** 读单节点完整内容；命中缓存则直接返回。 */
  async function loadNode(
    cid: ConversationId,
    nodeId: NodeId,
  ): Promise<NodeDetail | null> {
    const cached = nodeDetails.value[nodeId]
    if (cached) return cached
    try {
      const res = await readNodeApi(cid, nodeId)
      if (res.node) {
        nodeDetails.value = { ...nodeDetails.value, [nodeId]: res.node }
        return res.node
      }
      return null
    } catch (err) {
      lastError.value = describeError(err, '节点读取失败')
      return null
    }
  }

  /**
   * 从某节点另起一支并设为当前所在；成功后刷新树并把对话对象回填 conversations。
   * 返回是否成功（失败信息在 `lastError`）。
   */
  async function branchFrom(
    cid: ConversationId,
    fromNodeId: NodeId,
  ): Promise<boolean> {
    branching.value = true
    try {
      const res = await branchFromApi(cid, fromNodeId)
      if (res.current_node_id !== undefined) {
        currentNodeId.value = res.current_node_id
      }
      if (res.conversation) {
        useConversationsStore().applyConversation(res.conversation)
      }
      // 分支后对话框只显示「根 → 分支点 N」这条路径：把 chat 的当前节点设为 N，
      // N 之后（旧支）的消息随之隐藏（仍留在树里）。
      useChatStore().setCurrentNode(fromNodeId)
      await load(cid)
      lastError.value = null
      return true
    } catch (err) {
      lastError.value = describeError(err, '切换分支失败')
      return false
    } finally {
      branching.value = false
    }
  }

  return {
    conversationId,
    nodes,
    currentNodeId,
    currentBranch,
    branches,
    nodeDetails,
    loading,
    branching,
    lastError,
    hasTree,
    reset,
    load,
    loadNode,
    branchFrom,
  }
})
