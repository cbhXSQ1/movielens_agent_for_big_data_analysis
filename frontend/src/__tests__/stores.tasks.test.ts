import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listTasks: vi.fn(),
  getTask: vi.fn(),
  getTaskResult: vi.fn(),
  cancelTask: vi.fn(),
}))

vi.mock('@/api/endpoints', () => api)

import { TASK_POLL_INTERVAL_MS, useTasksStore } from '@/stores/tasks'

function makeTask(id: string, over: Record<string, unknown> = {}) {
  return {
    agent_task_id: id,
    hadoop_task_id: null,
    name: `task-${id}`,
    summary: 's',
    status: 'running',
    status_cn: '运行',
    stage: 'stage',
    progress_percent: 10,
    estimated_seconds: 60,
    updated_at: 0,
    ...over,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.listTasks.mockReset()
  api.getTask.mockReset()
  api.getTaskResult.mockReset()
})

describe('tasks · 事件 upsert', () => {
  it('applyStatus 按 agent_task_id upsert（同一任务合并覆盖）', () => {
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { progress_percent: 10 }))
    tasks.applyStatus(makeTask('a1', { progress_percent: 80, status: 'running' }))
    expect(tasks.taskList).toHaveLength(1)
    expect(tasks.get('a1')?.progress_percent).toBe(80)
  })

  it('applyResumed 对新任务也能 upsert', () => {
    const tasks = useTasksStore()
    tasks.applyResumed({
      agent_task_id: 'a9',
      hadoop_task_id: 'H-9',
      status: 'succeeded',
      result_summary: 'done',
    })
    expect(tasks.get('a9')?.status).toBe('succeeded')
    expect(tasks.get('a9')?.hadoop_task_id).toBe('H-9')
  })

  it('双编号齐全', () => {
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { hadoop_task_id: 'H-1' }))
    expect(tasks.get('a1')?.agent_task_id).toBe('a1')
    expect(tasks.get('a1')?.hadoop_task_id).toBe('H-1')
  })
})

describe('tasks · 刷新补拉与结果', () => {
  it('refresh 用 listTasks 补拉并 upsert', async () => {
    api.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [makeTask('a1'), makeTask('a2', { status: 'succeeded' })],
    })
    const tasks = useTasksStore()
    await tasks.refresh('c1')
    expect(api.listTasks).toHaveBeenCalledWith('c1')
    expect(tasks.taskList.map((t) => t.agent_task_id)).toEqual(['a1', 'a2'])
    expect(tasks.get('a2')?.status).toBe('succeeded')
  })

  it('refreshOne 用 getTask 补单个任务', async () => {
    api.getTask.mockResolvedValue({
      ok: true,
      task: makeTask('a1', { progress_percent: 99 }),
    })
    const tasks = useTasksStore()
    const task = await tasks.refreshOne('c1', 'a1')
    expect(task?.progress_percent).toBe(99)
    expect(tasks.get('a1')?.progress_percent).toBe(99)
  })

  it('loadResult 取结果并按 agent_task_id 存', async () => {
    api.getTaskResult.mockResolvedValue({
      ok: true,
      task: makeTask('a1'),
      blocks: { report: 'full report' },
    })
    const tasks = useTasksStore()
    const payload = await tasks.loadResult('c1', 'a1')
    expect(payload?.blocks.report).toBe('full report')
    expect(tasks.getResult('a1')?.blocks.report).toBe('full report')
    expect(tasks.latestResult).not.toBeNull()
  })

  it('补拉失败记 lastError 且不抛', async () => {
    api.listTasks.mockRejectedValue(new Error('任务列表拉取失败'))
    const tasks = useTasksStore()
    await expect(tasks.refresh('c1')).resolves.toEqual([])
    expect(tasks.lastError).toBe('任务列表拉取失败')
  })
})

describe('tasks · 当前任务语义（清单②③：选中优先，不再偏置运行中）', () => {
  it('未选中时取最近更新的任务（僵尸运行中任务不再霸占结果标签）', () => {
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { status: 'running', updated_at: 5 }))
    tasks.applyStatus(makeTask('a2', { status: 'succeeded', updated_at: 9 }))
    expect(tasks.current?.agent_task_id).toBe('a2')
  })

  it('select 选中后 current 指向选中任务（点任务卡切换结果标签）', () => {
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { status: 'succeeded', updated_at: 5 }))
    tasks.applyStatus(makeTask('a2', { status: 'succeeded', updated_at: 9 }))
    tasks.select('a1')
    expect(tasks.current?.agent_task_id).toBe('a1')
    tasks.select(null)
    expect(tasks.current?.agent_task_id).toBe('a2')
  })

  it('选中的任务被清掉后回落到最近更新', () => {
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { status: 'succeeded', updated_at: 5 }))
    tasks.applyStatus(makeTask('a2', { status: 'succeeded', updated_at: 9 }))
    tasks.select('a1')
    expect(tasks.current?.agent_task_id).toBe('a1')
    tasks.clear()
    expect(tasks.current).toBeNull()
    expect(tasks.selectedId).toBeNull()
  })
})

describe('tasks · 取结果失败回显（清单④）', () => {
  it('loadResult 失败记 resultError，成功清空', async () => {
    api.getTaskResult.mockRejectedValueOnce(new Error('任务尚未结束'))
    const tasks = useTasksStore()
    const payload = await tasks.loadResult('c1', 'a1')
    expect(payload).toBeNull()
    expect(tasks.resultError).toBe('任务尚未结束')

    api.getTaskResult.mockResolvedValueOnce({
      ok: true,
      task: makeTask('a1'),
      blocks: {},
    })
    await tasks.loadResult('c1', 'a1')
    expect(tasks.resultError).toBeNull()
  })
})

describe('tasks · 自动补拉（清单①⑤：SSE 丢事件自愈）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('有活跃任务时按间隔补拉；无活跃任务不请求', async () => {
    api.listTasks.mockResolvedValue({ ok: true, conversation_id: 'c1', tasks: [] })
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { status: 'running', updated_at: 1 }))
    tasks.startPolling('c1')
    expect(api.listTasks).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS)
    expect(api.listTasks).toHaveBeenCalledTimes(1)

    // 全部终态后不再补拉
    tasks.applyStatus(makeTask('a1', { status: 'succeeded', updated_at: 2 }))
    await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS * 3)
    expect(api.listTasks).toHaveBeenCalledTimes(1)

    tasks.stopPolling()
    await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS * 3)
    expect(api.listTasks).toHaveBeenCalledTimes(1)
  })

  it('同一对话重复 startPolling 不叠加定时器', async () => {
    api.listTasks.mockResolvedValue({ ok: true, conversation_id: 'c1', tasks: [] })
    const tasks = useTasksStore()
    tasks.applyStatus(makeTask('a1', { status: 'queued', updated_at: 1 }))
    tasks.startPolling('c1')
    tasks.startPolling('c1')
    await vi.advanceTimersByTimeAsync(TASK_POLL_INTERVAL_MS)
    expect(api.listTasks).toHaveBeenCalledTimes(1)
    tasks.stopPolling()
  })

  it('clear 停掉轮询并清空选中/结果错误', () => {
    const tasks = useTasksStore()
    tasks.select('a1')
    tasks.startPolling('c1')
    tasks.clear()
    expect(tasks.selectedId).toBeNull()
    expect(tasks.resultError).toBeNull()
  })
})
