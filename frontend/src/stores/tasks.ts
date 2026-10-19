// FB.8 · 长任务 store（§3.8 / §3.9）。
// 主来源是推送流上的 `task_status` / `task_resumed` 事件；刷新页面或补拉用
// `listTasks` / `getTask`；结构化结果用 `getTaskResult`（§3.9）。
// 一律按 `agent_task_id` upsert（双编号：agent_task_id + hadoop_task_id）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getTask, getTaskResult, listTasks } from '@/api/endpoints'
import type {
  AgentTaskId,
  ResultPayload,
  TaskResumedData,
  TaskStatusData,
  TaskView,
} from '@/api/types'

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

  const taskList = computed<TaskView[]>(() =>
    order.value
      .map((id) => byId.value[id])
      .filter((t): t is TaskView => Boolean(t)),
  )

  /** 当前任务：优先运行中/排队中，否则取最近更新的，再否则首个。 */
  const current = computed<TaskView | null>(() => {
    const list = taskList.value
    if (list.length === 0) return null
    const active = list.find(
      (t) => t.status === 'running' || t.status === 'queued',
    )
    if (active) return active
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

  /** 取任务结构化结果（§3.9）；未到终态会失败并记 lastError。 */
  async function loadResult(
    conversationId: string,
    agentTaskId: AgentTaskId,
  ): Promise<ResultPayload | null> {
    try {
      const payload = await getTaskResult(conversationId, agentTaskId)
      applyResultPayload(payload)
      lastError.value = null
      return payload
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '任务结果拉取失败'
      return null
    }
  }

  function get(agentTaskId: AgentTaskId): TaskView | null {
    return byId.value[agentTaskId] ?? null
  }

  function getResult(agentTaskId: AgentTaskId): ResultPayload | null {
    return results.value[agentTaskId] ?? null
  }

  function clear(): void {
    byId.value = {}
    order.value = []
    results.value = {}
    latestResult.value = null
    lastError.value = null
  }

  return {
    byId,
    order,
    results,
    latestResult,
    loading,
    lastError,
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
    get,
    getResult,
    clear,
  }
})
