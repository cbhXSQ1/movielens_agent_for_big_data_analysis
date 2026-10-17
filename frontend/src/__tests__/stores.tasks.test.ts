import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({
  listTasks: vi.fn(),
  getTask: vi.fn(),
  getTaskResult: vi.fn(),
}))

vi.mock('@/api/endpoints', () => api)

import { useTasksStore } from '@/stores/tasks'

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
