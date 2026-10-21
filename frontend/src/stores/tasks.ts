// FB.8 · 长任务 store（§3.8 / §3.9）。
// 主来源是推送流上的 `task_status` / `task_resumed` 事件；刷新页面或补拉用
// `listTasks` / `getTask`；结构化结果用 `getTaskResult`（§3.9）。
// 一律按 `agent_task_id` upsert（双编号：agent_task_id + hadoop_task_id）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { cancelTask, getTask, getTaskResult, listTasks } from '@/api/endpoints'
import type {
  AgentTaskId,
  CancelTaskResponse,
  ResultPayload,
  TaskResumedData,
  TaskStatusData,
  TaskView,
} from '@/api/types'

/** 有进行中任务时的自动补拉间隔（清单①：SSE 丢事件时卡片不更新/僵尸卡不自愈）。 */
export const TASK_POLL_INTERVAL_MS = 5000

/** `cancel()` 的回执：执行侧是否真的被通知到，如实带给界面。 */
export interface CancelResult {
  ok: boolean
  task?: TaskView
  remote?: CancelTaskResponse['remote_cancel']
  error?: string
}

export function emptyTask(agentTaskId: AgentTaskId): TaskView {
  return {
    agent_task_id: agentTaskId,
    hadoop_task_id: null,
    name: '',
    summary: '',
    status: 'queued',
    status_cn: '',
    stage: '',
    progress_percent: 0,
    estimated_seconds: null,
    updated_at: 0,
  }
}

export const useTasksStore = defineStore('tasks', () => {
  const byId = ref<Record<AgentTaskId, TaskView>>({})
  const order = ref<AgentTaskId[]>([])
  const results = ref<Record<AgentTaskId, ResultPayload>>({})
  const latestResult = ref<ResultPayload | null>(null)
  const loading = ref(false)
  const lastError = ref<string | null>(null)
  /** 「取结果」失败的本地回显（清单④：失败只写全局 lastError，界面零反馈）。 */
  const resultError = ref<string | null>(null)

  /** 是否存在进行中（排队/运行）任务：自动补拉只在有活跃任务时跑。 */
  const hasActive = computed(() =>
    taskList.value.some((t) => t.status === 'running' || t.status === 'queued'),
  )

  const taskList = computed<TaskView[]>(() =>
    order.value
      .map((id) => byId.value[id])
      .filter((t): t is TaskView => Boolean(t)),
  )

/** 结果标签/顶栏/右栏消费的「当前任务」：选中优先，否则最近更新的。 */
const selectedId = ref<AgentTaskId | null>(null)

/** 手动选中某个任务（点任务卡即切换结果标签）；传 null 取消选中。 */
function select(id: AgentTaskId | null): void {
  selectedId.value = id ?? null
}

/**
 * 当前任务：**选中优先**（点任务卡切换结果标签，清单③）；未选中时取最近更新的
 * （不再偏置运行中/排队中——僵尸任务曾靠这条偏置霸占结果标签，清单②）。
 */
const current = computed<TaskView | null>(() => {
  const list = taskList.value
  if (list.length === 0) return null
  if (selectedId.value) {
    const picked = byId.value[selectedId.value]
    if (picked) return picked
  }
  const sorted = [...list].sort((a, b) => b.updated_at - a.updated_at)
  return sorted[0] ?? list[0]
})

  /** 当前任务的结构化结果（用于版本/T1T2/状态展示）。 */
  const currentResult = computed<ResultPayload | null>(() => {
    const id = current.value?.agent_task_id
    if (!id) return null
    return results.value[id] ?? null
  })

  /** 最近一次到达的结果（全局，右栏最近产物用）。 */
  const latest = computed<ResultPayload | null>(() => latestResult.value)

  /** 按 agent_task_id upsert（新任务追加到列表尾）。 */
  function upsert(task: TaskView): void {
    const id = task.agent_task_id
    if (id === undefined || id === null || id === '') return
    if (!(id in byId.value)) order.value.push(id)
    byId.value[id] = { ...(byId.value[id] ?? emptyTask(id)), ...task }
  }

  /** `task_status` 事件的 data 即任务视图。 */
  function applyStatus(data: TaskStatusData): void {
    upsert(data)
  }

  /** `task_resumed`：补状态（任务通常已存在）。 */
  function applyResumed(data: TaskResumedData): void {
    const base = byId.value[data.agent_task_id] ?? emptyTask(data.agent_task_id)
    upsert({
      ...base,
      agent_task_id: data.agent_task_id,
      hadoop_task_id: data.hadoop_task_id,
      status: data.status,
      updated_at: Date.now() / 1000,
    })
  }

  /** `result_payload`：存结构化结果（按 task.agent_task_id），并顺带 upsert 任务视图。 */
  function applyResultPayload(payload: ResultPayload): void {
    const id = payload.task?.agent_task_id
    if (id) results.value[id] = payload
    latestResult.value = payload
    if (payload.task) upsert(payload.task)
  }

  /** 刷新补拉：拉该对话全部任务的当前状态并 upsert。 */
  async function refresh(conversationId: string): Promise<TaskView[]> {
    loading.value = true
    try {
      const res = await listTasks(conversationId)
      for (const task of res.tasks ?? []) upsert(task)
      lastError.value = null
      return taskList.value
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '任务列表拉取失败'
      return taskList.value
    } finally {
      loading.value = false
    }
  }

  /** 单任务补拉。 */
  async function refreshOne(
    conversationId: string,
    agentTaskId: AgentTaskId,
  ): Promise<TaskView | null> {
    try {
      const res = await getTask(conversationId, agentTaskId)
      upsert(res.task)
      lastError.value = null
      return res.task
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '任务拉取失败'
      return null
    }
  }

  /** 取任务结构化结果（§3.9）；未到终态会失败并记 resultError / lastError。 */
  async function loadResult(
    conversationId: string,
    agentTaskId: AgentTaskId,
  ): Promise<ResultPayload | null> {
    try {
      const payload = await getTaskResult(conversationId, agentTaskId)
      applyResultPayload(payload)
      lastError.value = null
      resultError.value = null
      return payload
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '任务结果拉取失败'
      resultError.value = lastError.value
      return null
    }
  }

  /**
   * 用户手动中止一个长任务（「关掉它」）。
   *
   * 后端会尽力通知执行侧真的停，再把本地记录按「已中止」收口并推
   * `task_status`（SSE 到达即刷新本行）。返回后端的完整回执：
   * 执行侧没确认中止时 `remote_cancel.ok === false`（原因在 `message`），
   * 由调用方如实展示——**不假装已经停干净了**。
   */
  async function cancel(
    conversationId: string,
    agentTaskId: AgentTaskId,
  ): Promise<CancelResult> {
    try {
      const res = await cancelTask(conversationId, agentTaskId)
      if (res.task) upsert(res.task)
      lastError.value = null
      return { ok: true, task: res.task, remote: res.remote_cancel }
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '中止任务失败'
      return { ok: false, error: lastError.value }
    }
  }

  function get(agentTaskId: AgentTaskId): TaskView | null {
    return byId.value[agentTaskId] ?? null
  }

  function getResult(agentTaskId: AgentTaskId): ResultPayload | null {
    return results.value[agentTaskId] ?? null
  }

  // ---------------------------------------------------------------- 自动补拉
  // SSE 事件流可能丢事件（后端已终态、卡片还停在 running），僵尸卡也不会自愈。
  // 有活跃任务时每 5 秒补拉一次；没有活跃任务就空转（不请求），终态卡片保持稳定。
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let pollConversationId: string | null = null

  function startPolling(conversationId: string): void {
    if (pollTimer && pollConversationId === conversationId) return
    stopPolling()
    pollConversationId = conversationId
    pollTimer = setInterval(() => {
      const cid = pollConversationId
      if (!cid || !hasActive.value) return
      void refresh(cid)
    }, TASK_POLL_INTERVAL_MS)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    pollConversationId = null
  }

  function clear(): void {
    byId.value = {}
    order.value = []
    results.value = {}
    latestResult.value = null
    lastError.value = null
    resultError.value = null
    selectedId.value = null
    stopPolling()
  }

  return {
    byId,
    order,
    results,
    latestResult,
    loading,
    lastError,
    resultError,
    hasActive,
    selectedId,
    taskList,
    current,
    currentResult,
    latest,
    upsert,
    applyStatus,
    applyResumed,
    applyResultPayload,
    refresh,
    refreshOne,
    loadResult,
    cancel,
    get,
    getResult,
    select,
    startPolling,
    stopPolling,
    clear,
  }
})
