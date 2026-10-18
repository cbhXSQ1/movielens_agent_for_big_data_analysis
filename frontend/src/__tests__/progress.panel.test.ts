// 阶段 H（fe-progress）· FH.1–FH.3：任务进度面板组件测试。
// 只 mock 数据层的 endpoints / sse；组件→store→（被 mock 的）api 全链真实执行，
// 因此能断言「事件驱动进度」「补拉恢复」「终态取结果」等状态流转确实生效。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { NTag } from 'naive-ui'

const endpoints = vi.hoisted(() => ({
  health: vi.fn(),
  createConversation: vi.fn(),
  listConversations: vi.fn(),
  sendByMode: vi.fn(),
  fetchTools: vi.fn(),
  listTasks: vi.fn(),
  getTask: vi.fn(),
  getTaskResult: vi.fn(),
}))
vi.mock('@/api/endpoints', () => endpoints)

const sse = vi.hoisted(() => ({
  openConversationStream: vi.fn(),
  conversationStreamUrl: (id: string) => `/api/conversations/${id}/stream`,
}))
vi.mock('@/api/sse', () => sse)

import TasksPanel from '@/views/panels/TasksPanel.vue'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import { useConnectionStore } from '@/stores/connection'
import { useTasksStore } from '@/stores/tasks'
import { RESULT_TAB_NAV } from '@/composables/useResultTabNav'
import {
  isTerminal,
  statusLabel,
  statusTagType,
} from '@/components/progress/taskDisplay'
import type { AnyStreamEnvelope, ResultPayload, TaskView } from '@/api/types'

function makeTask(id: string, over: Partial<TaskView> = {}): TaskView {
  return {
    agent_task_id: id,
    hadoop_task_id: null,
    name: `任务-${id}`,
    summary: '',
    status: 'running',
    status_cn: '运行',
    stage: '',
    progress_percent: 0,
    estimated_seconds: null,
    updated_at: 0,
    ...over,
  }
}

function statusEvent(task: TaskView): AnyStreamEnvelope {
  return { event: 'task_status', conversation_id: 'c1', seq: 1, ts: 0, data: task }
}

function resultPayload(task: TaskView): ResultPayload {
  return { ok: true, task, blocks: { report: '# 报告全文' } }
}

let pinia: Pinia

function primeStores(): void {
  useConversationsStore().currentId = 'c1'
  useConnectionStore().markStreamOpen()
}

function mountPanel(nav?: (key: string) => void): VueWrapper {
  return mount(TasksPanel, {
    global: {
      plugins: [pinia],
      ...(nav ? { provide: { [RESULT_TAB_NAV]: nav } } : {}),
    },
  })
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  endpoints.listTasks.mockResolvedValue({
    ok: true,
    conversation_id: 'c1',
    tasks: [],
  })
})

describe('FH.1 · 任务列表/详情：双编号 + 名称 + 概括 + 中文状态', () => {
  it('列出任务并显示双编号/名称/概括/状态/进度，点开看详情', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [
        makeTask('a1', {
          hadoop_task_id: 'H-1',
          name: '清洗评分',
          summary: '对 ml-1m 清洗并评分',
          status: 'running',
          status_cn: '运行',
          stage: '清洗',
          progress_percent: 40,
          estimated_seconds: 120,
          updated_at: 1729000000,
        }),
        makeTask('a2', {
          name: '独立评分',
          status: 'succeeded',
          status_cn: '成功',
          progress_percent: 100,
        }),
      ],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(endpoints.listTasks).toHaveBeenCalledWith('c1')
    const item = wrapper.find('[data-testid="task-item-a1"]')
    expect(item.exists()).toBe(true)

    expect(wrapper.find('[data-testid="task-agent-id-a1"]').text()).toBe('a1')
    expect(wrapper.find('[data-testid="task-hadoop-id-a1"]').text()).toBe('H-1')
    expect(wrapper.find('[data-testid="task-name-a1"]').text()).toBe('清洗评分')
    expect(wrapper.find('[data-testid="task-summary-a1"]').text()).toBe(
      '对 ml-1m 清洗并评分',
    )
    expect(wrapper.find('[data-testid="task-status-a1"]').text()).toBe('运行')
    expect(wrapper.find('[data-testid="task-percent-a1"]').text()).toBe('40%')

    await item.trigger('click')
    const detail = wrapper.find('[data-testid="task-detail"]')
    expect(detail.exists()).toBe(true)
    expect(wrapper.find('[data-testid="detail-agent-id"]').text()).toBe('a1')
    expect(wrapper.find('[data-testid="detail-hadoop-id"]').text()).toBe('H-1')
    expect(wrapper.find('[data-testid="detail-stage"]').text()).toBe('清洗')
  })

  it('无 Hadoop 编号时如实显示占位；无预计时长不显示', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [makeTask('a2', { hadoop_task_id: null, estimated_seconds: null })],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="task-hadoop-id-a2"]').text()).toBe('—')
    expect(wrapper.find('[data-testid="task-estimate-a2"]').exists()).toBe(false)
  })
})

describe('FH.2 · 进度条：task_status 事件实时驱动', () => {
  it('事件推进 progress_percent / 状态 / 预计时长，无预计时长则隐藏', async () => {
    primeStores()
    const wrapper = mountPanel()
    await flushPromises()

    const conversations = useConversationsStore()
    conversations.handleEvent(
      statusEvent(
        makeTask('e1', {
          name: '清洗',
          status_cn: '运行',
          stage: '清洗',
          progress_percent: 10,
          estimated_seconds: 60,
        }),
      ),
    )
    await flushPromises()

    expect(wrapper.find('[data-testid="task-percent-e1"]').text()).toBe('10%')
    expect(wrapper.find('[data-testid="task-estimate-e1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="task-estimate-e1"]').text()).toContain('约')

    conversations.handleEvent(
      statusEvent(
        makeTask('e1', {
          status: 'running',
          status_cn: '运行',
          stage: '评分',
          progress_percent: 80,
          estimated_seconds: 30,
        }),
      ),
    )
    await flushPromises()
    expect(wrapper.find('[data-testid="task-percent-e1"]').text()).toBe('80%')
    expect(wrapper.find('[data-testid="task-stage-e1"]').text()).toBe('评分')

    conversations.handleEvent(
      statusEvent(
        makeTask('e1', {
          status: 'succeeded',
          status_cn: '成功',
          progress_percent: 100,
          estimated_seconds: null,
        }),
      ),
    )
    await flushPromises()
    expect(wrapper.find('[data-testid="task-percent-e1"]').text()).toBe('100%')
    expect(wrapper.find('[data-testid="task-status-e1"]').text()).toBe('成功')
    expect(wrapper.find('[data-testid="task-estimate-e1"]').exists()).toBe(false)
  })
})

describe('FH.3 · 刷新补拉与终态取结果', () => {
  it('挂载后经 listTasks 补拉恢复（刷新/重连）', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [makeTask('a1', { name: '恢复的任务', progress_percent: 55 })],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(endpoints.listTasks).toHaveBeenCalledWith('c1')
    expect(wrapper.find('[data-testid="task-name-a1"]').text()).toBe('恢复的任务')
    expect(wrapper.find('[data-testid="task-percent-a1"]').text()).toBe('55%')

    await wrapper.find('[data-testid="tasks-refresh"]').trigger('click')
    await flushPromises()
    expect(endpoints.listTasks).toHaveBeenCalledTimes(2)
  })

  it('终态任务可用 getTaskResult 取结果并打开对应产物标签', async () => {
    const task = makeTask('a1', { status: 'succeeded', status_cn: '成功' })
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [task],
    })
    endpoints.getTaskResult.mockResolvedValue(resultPayload(task))
    primeStores()
    useArtifactsStore().add('c1', {
      id: 'task:a1:report.md',
      title: '评估报告',
      category: '报告',
      kind: 'md',
      source: 'task_file',
      task_id: 'a1',
      path: 'report.md',
      scheme_name: null,
    })
    const nav = vi.fn()

    const wrapper = mountPanel(nav)
    await flushPromises()

    await wrapper.find('[data-testid="task-item-a1"]').trigger('click')
    expect(wrapper.find('[data-testid="task-load-result"]').exists()).toBe(true)

    await wrapper.find('[data-testid="task-load-result"]').trigger('click')
    await flushPromises()

    expect(endpoints.getTaskResult).toHaveBeenCalledWith('c1', 'a1')
    expect(useTasksStore().latestResult).not.toBeNull()
    expect(wrapper.find('[data-testid="task-result-blocks"]').exists()).toBe(true)

    await wrapper.find('[data-testid="task-result-link-report"]').trigger('click')
    expect(useArtifactsStore().isOpen('c1', 'task:a1:report.md')).toBe(true)
    expect(nav).toHaveBeenCalledWith('task:a1:report.md')
  })

  it('无对应产物的结果块只展示、不导航（不落到失效的旧键）', async () => {
    const task = makeTask('a1', { status: 'succeeded', status_cn: '成功' })
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [task],
    })
    endpoints.getTaskResult.mockResolvedValue(resultPayload(task))
    primeStores()
    const nav = vi.fn()

    const wrapper = mountPanel(nav)
    await flushPromises()

    await wrapper.find('[data-testid="task-item-a1"]').trigger('click')
    await wrapper.find('[data-testid="task-load-result"]').trigger('click')
    await flushPromises()

    const block = wrapper.find('[data-testid="task-result-link-report"]')
    expect(block.exists()).toBe(true)
    await block.trigger('click')
    expect(nav).not.toHaveBeenCalled()
  })

  it('未到终态不显示取结果入口', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [makeTask('a1', { status: 'running', status_cn: '运行' })],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="task-item-a1"]').trigger('click')

    expect(wrapper.find('[data-testid="task-result-pending"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="task-load-result"]').exists()).toBe(false)
  })
})

describe('FH.4 · 中止状态显示与终态行为', () => {
  it('状态映射：中止显示为已中止、标签 warning、且为终态', () => {
    expect(statusLabel({ status: 'aborted', status_cn: '已中止' } as any)).toBe('已中止')
    expect(statusTagType('aborted')).toBe('warning')
    expect(isTerminal('aborted')).toBe(true)
  })

  it('列表中的中止任务显示「已中止」且标签为 warning，详情仍可「取结果」', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [
        makeTask('ab1', {
          status: 'aborted',
          status_cn: '已中止',
          progress_percent: 60,
        }),
      ],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    const statusTag = wrapper.find('[data-testid="task-status-ab1"]')
    expect(statusTag.text()).toBe('已中止')
    expect(statusTag.findComponent(NTag).props('type')).toBe('warning')

    await wrapper.find('[data-testid="task-item-ab1"]').trigger('click')
    const detail = wrapper.find('[data-testid="task-detail"]')
    expect(detail.exists()).toBe(true)
    expect(wrapper.find('[data-testid="detail-status"]').text()).toBe('已中止')
    expect(wrapper.find('[data-testid="task-load-result"]').exists()).toBe(true)
  })

  it('status_cn 缺失但 status=aborted 时，兜底仍显示「已中止」', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [makeTask('ab2', { status: 'aborted', status_cn: '' })],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="task-status-ab2"]').text()).toBe('已中止')

    await wrapper.find('[data-testid="task-item-ab2"]').trigger('click')
    expect(wrapper.find('[data-testid="detail-status"]').text()).toBe('已中止')
    expect(wrapper.find('[data-testid="task-load-result"]').exists()).toBe(true)
  })
})

describe('FH.5 · 长任务进度提示（轮询不变）', () => {
  it('运行中的任务在列表与详情都提示「仍在运行，可继续等待」', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [
        makeTask('r1', {
          status: 'running',
          status_cn: '运行',
          stage: '清洗',
          progress_percent: 40,
        }),
      ],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="task-running-hint-r1"]').text()).toContain('仍在运行')
    expect(wrapper.find('[data-testid="task-percent-r1"]').text()).toBe('40%')
    expect(wrapper.find('[data-testid="task-stage-r1"]').text()).toBe('清洗')

    await wrapper.find('[data-testid="task-item-r1"]').trigger('click')
    expect(wrapper.find('[data-testid="detail-running-hint"]').text()).toContain('仍在运行')
    expect(wrapper.find('[data-testid="detail-percent"]').text()).toBe('40%')
  })

  it('排队中的任务同样提示仍在运行、可继续等待', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [makeTask('q1', { status: 'queued', status_cn: '排队', progress_percent: 0 })],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="task-running-hint-q1"]').text()).toContain('仍在运行')
  })

  it('中止的任务显示「已中止」且进度停在中断处（不强制到 100%）', async () => {
    endpoints.listTasks.mockResolvedValue({
      ok: true,
      conversation_id: 'c1',
      tasks: [
        makeTask('ab1', {
          status: 'aborted',
          status_cn: '已中止',
          stage: '评分',
          progress_percent: 60,
        }),
      ],
    })
    primeStores()

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="task-status-ab1"]').text()).toBe('已中止')
    expect(wrapper.find('[data-testid="task-percent-ab1"]').text()).toBe('60%')
    expect(wrapper.find('[data-testid="task-running-hint-ab1"]').exists()).toBe(false)

    await wrapper.find('[data-testid="task-item-ab1"]').trigger('click')
    expect(wrapper.find('[data-testid="detail-status"]').text()).toBe('已中止')
    expect(wrapper.find('[data-testid="detail-percent"]').text()).toBe('60%')
    expect(wrapper.find('[data-testid="detail-aborted-note"]').text()).toContain('已中止')
  })
})
