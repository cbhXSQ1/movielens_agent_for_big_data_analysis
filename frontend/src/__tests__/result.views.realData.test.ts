// 结果六标签（真实数据）复核：来源统一为 tasks.currentResult。
// 覆盖：Score / Report / Basis 绑定当前任务结果；MlView 渲染当前任务 ML 板块；
// Metrics 空态与真实下载；Report Markdown 渲染与引用下载；Basis 从产物 store 派生方案文件。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

import ScoreView from '@/views/tabs/ScoreView.vue'
import ReportView from '@/views/tabs/ReportView.vue'
import MetricsView from '@/views/tabs/MetricsView.vue'
import BasisView from '@/views/tabs/BasisView.vue'
import MlView from '@/views/tabs/MlView.vue'
import { useTasksStore } from '@/stores/tasks'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { useModelsStore } from '@/stores/models'
import { schemeTabKey } from '@/components/centerTabs'
import type { ArtifactRef, ResultPayload, ResultBlocks, TaskView } from '@/api/types'

const resourcesApi = vi.hoisted(() => ({
  fileDownloadUrl: vi.fn(),
}))
const modelsApi = vi.hoisted(() => ({
  listModels: vi.fn(),
}))

vi.mock('@/api/resources', () => resourcesApi)
vi.mock('@/api/models', () => modelsApi)

const CID = 'conv-1'

function makeTask(over: Partial<TaskView> = {}): TaskView {
  return {
    agent_task_id: 'a1',
    hadoop_task_id: 'H-1',
    name: '清洗任务',
    summary: '',
    status: 'succeeded',
    status_cn: '成功',
    stage: 'done',
    progress_percent: 100,
    estimated_seconds: null,
    updated_at: 1,
    ...over,
  }
}

function payload(task: TaskView, blocks: ResultBlocks): ResultPayload {
  return { ok: true, task, blocks }
}

function seed(blocks: ResultBlocks, over: Partial<TaskView> = {}): TaskView {
  const task = makeTask(over)
  useTasksStore().applyResultPayload(payload(task, blocks))
  return task
}

function makePinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

function mountView(component: unknown, pinia: Pinia): VueWrapper {
  return mount(component as never, {
    global: { plugins: [pinia] },
  }) as VueWrapper
}

function artifact(over: Partial<ArtifactRef> = {}): ArtifactRef {
  return {
    id: 'task:H-1:report.md',
    title: '评估报告',
    category: '报告',
    kind: 'md',
    source: 'task_file',
    task_id: 'H-1',
    path: 'report.md',
    scheme_name: null,
    ...over,
  }
}

/** current（运行中）与 latest（最后到达）指向不同结果，用于验证绑定来源。 */
function seedCurrentVsLatest(currentBlocks: ResultBlocks, latestBlocks: ResultBlocks): void {
  seed(currentBlocks, {
    agent_task_id: 'current',
    hadoop_task_id: 'H-current',
    status: 'running',
    updated_at: 1,
  })
  seed(latestBlocks, {
    agent_task_id: 'latest',
    hadoop_task_id: 'H-latest',
    status: 'succeeded',
    updated_at: 2,
  })
  useConversationsStore().currentId = CID
}

beforeEach(() => {
  localStorage.clear()
  resourcesApi.fileDownloadUrl.mockReset()
  resourcesApi.fileDownloadUrl.mockImplementation(
    (tid: string, path: string) =>
      `/api/files/download?task_id=${tid}&path=${encodeURIComponent(path)}`,
  )
  modelsApi.listModels.mockReset()
  modelsApi.listModels.mockResolvedValue({ ok: true, models: [] })
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// 来源统一
// ---------------------------------------------------------------------------

describe('结果视图 · 绑定 tasks.currentResult', () => {
  it('ScoreView 使用当前任务结果，而不是最后到达的结果', () => {
    const pinia = makePinia()
    seedCurrentVsLatest(
      {
        score_comparison: {
          before: { Accurate: 11, composite: 11 },
          after: { Accurate: 22, composite: 22 },
        },
      },
      {
        score_comparison: {
          before: { Accurate: 88, composite: 88 },
          after: { Accurate: 99, composite: 99 },
        },
      },
    )

    const wrapper = mountView(ScoreView, pinia)
    const composite = wrapper.find('.score-composite').text()
    expect(composite).toContain('11')
    expect(composite).toContain('22')
    expect(composite).not.toContain('88')
    expect(composite).not.toContain('99')
  })

  it('ScoreView 缺失 dedupe / fix 时显示 — 而不是 0', () => {
    const pinia = makePinia()
    seed({
      data_volume: {
        input: { ratings_lines: 10 },
        output: { ratings: 9 },
      },
    })

    const wrapper = mountView(ScoreView, pinia)
    const notice = wrapper.find('.notice').text()
    expect(notice).toContain('去重 —')
    expect(notice).toContain('修复 —')
  })

  it('ReportView 使用当前任务结果，并渲染 Markdown', () => {
    const pinia = makePinia()
    seedCurrentVsLatest(
      { report: '# 当前任务报告\n\n当前正文。' },
      { report: '# 其他任务报告\n\n其他正文。' },
    )

    const wrapper = mountView(ReportView, pinia)
    expect(wrapper.find('[data-testid="markdown-view"]').exists()).toBe(true)
    const text = wrapper.text()
    expect(text).toContain('当前任务报告')
    expect(text).toContain('当前正文')
    expect(text).not.toContain('其他任务报告')
    expect(wrapper.find('pre').exists()).toBe(false)
  })

  it('BasisView 使用当前任务结果', () => {
    const pinia = makePinia()
    seedCurrentVsLatest(
      { versions: { data: 'current-v1' }, time_boundaries: { T1: 'current-T1' } },
      { versions: { data: 'latest-v1' }, time_boundaries: { T1: 'latest-T1' } },
    )

    const wrapper = mountView(BasisView, pinia)
    const text = wrapper.text()
    expect(text).toContain('current-v1')
    expect(text).toContain('current-T1')
    expect(text).not.toContain('latest-v1')
    expect(text).not.toContain('latest-T1')
  })
})

// ---------------------------------------------------------------------------
// MlView
// ---------------------------------------------------------------------------

describe('MlView · 当前任务 ML 结果 + 模型列表', () => {
  it('当前结果含 ML 板块时渲染 MlResultView，并保留已登记模型区', async () => {
    const pinia = makePinia()
    seed({
      task_type: 'classification',
      classification: { f1: 0.78, auc: 0.82 },
    })
    useModelsStore().models = [
      {
        model_id: 'm-1',
        task_type: 'classification',
        algorithm: 'random_forest',
        data_version: 'v1',
        created_at: '2026-10-20',
        metrics_summary: { f1: 0.7846, k: 4, extra: { a: 1 } },
      },
    ]

    const wrapper = mountView(MlView, pinia)
    await flushPromises()

    expect(wrapper.find('[data-testid="ml-current-result"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-result-view"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-classification-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-model-m-1"]').exists()).toBe(true)
  })

  it('当前结果无 ML 板块时不渲染 MlResultView', async () => {
    const pinia = makePinia()
    seed({ report: '# 报告' })

    const wrapper = mountView(MlView, pinia)
    await flushPromises()

    expect(wrapper.find('[data-testid="ml-result-view"]').exists()).toBe(false)
  })

  it('指标摘要：比率转百分比、整数原样、嵌套值不显示 [object Object]', async () => {
    const pinia = makePinia()
    useModelsStore().models = [
      {
        model_id: 'm-2',
        task_type: 'clustering',
        algorithm: 'kmeans',
        data_version: 'v1',
        created_at: '2026-10-20',
        metrics_summary: {
          silhouette: 0.5123,
          k: 4,
          n_components: 8,
          extra: { a: 1 },
        },
      },
    ]

    const wrapper = mountView(MlView, pinia)
    await flushPromises()

    const card = wrapper.find('[data-testid="ml-model-m-2"]').text()
    expect(card).toContain('51.23%')
    expect(card).toContain('4')
    expect(card).toContain('8')
    expect(card).toContain('{"a":1}')
    expect(card).not.toContain('[object Object]')
  })
})

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------

describe('MetricsView · 空态与真实下载', () => {
  it('blocks 为空对象时显示空态，且不出现复制 / 下载', () => {
    const pinia = makePinia()
    seed({})
    useConversationsStore().currentId = CID

    const wrapper = mountView(MetricsView, pinia)
    expect(wrapper.find('[data-testid="metrics-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="metrics-copy"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="metrics-download"]').exists()).toBe(false)
  })

  it('找到当前任务的 metrics 产物时给出真实下载链接', () => {
    const pinia = makePinia()
    seed({ counts: { ratings: 9 } }, { hadoop_task_id: 'H-current' })
    useConversationsStore().currentId = CID
    useArtifactsStore().add(
      CID,
      artifact({
        id: 'task:H-current:metrics.json',
        title: '指标 JSON',
        category: '指标',
        kind: 'json',
        task_id: 'H-current',
        path: 'output/metrics.json',
      }),
    )

    const wrapper = mountView(MetricsView, pinia)
    const link = wrapper.find('[data-testid="metrics-download"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe(
      '/api/files/download?task_id=H-current&path=output%2Fmetrics.json',
    )
    expect(wrapper.find('[data-testid="metrics-empty"]').exists()).toBe(false)
  })

  it('没有对应 metrics 产物时不渲染下载按钮', () => {
    const pinia = makePinia()
    seed({ counts: { ratings: 9 } }, { hadoop_task_id: 'H-current' })
    useConversationsStore().currentId = CID
    useArtifactsStore().add(
      CID,
      artifact({
        id: 'task:H-other:metrics.json',
        task_id: 'H-other',
        path: 'output/metrics.json',
      }),
    )

    const wrapper = mountView(MetricsView, pinia)
    expect(wrapper.find('[data-testid="metrics-download"]').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Report 引用下载
// ---------------------------------------------------------------------------

describe('ReportView · 报告引用与产物下载', () => {
  it('报告块无全文但存在 report.md 产物时给出下载链接', () => {
    const pinia = makePinia()
    seed({ report: { artifact: 'report.md' } }, { hadoop_task_id: 'H-1' })
    useConversationsStore().currentId = CID
    useArtifactsStore().add(
      CID,
      artifact({ id: 'task:H-1:out/report.md', path: 'out/report.md' }),
    )

    const wrapper = mountView(ReportView, pinia)
    expect(wrapper.find('[data-testid="report-missing"]').exists()).toBe(true)
    const link = wrapper.find('[data-testid="report-download"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe(
      '/api/files/download?task_id=H-1&path=out%2Freport.md',
    )
  })
})

// ---------------------------------------------------------------------------
// Basis 方案产物
// ---------------------------------------------------------------------------

describe('BasisView · 方案文件来自产物 store', () => {
  it('按 source=scheme 派生方案，并打开对应方案标签', async () => {
    const pinia = makePinia()
    seed({ versions: { data: 'v1' } })
    useConversationsStore().currentId = CID
    useArtifactsStore().addMany(CID, [
      artifact({
        id: 'scheme:custom_rules.v2.json',
        title: '自定义清洗方案',
        category: '依据',
        kind: 'scheme',
        source: 'scheme',
        task_id: null,
        path: null,
        scheme_name: 'custom_rules.v2.json',
      }),
      artifact({
        id: 'scheme:custom_scoring.v3.json',
        title: '自定义评分方案',
        category: '依据',
        kind: 'scheme',
        source: 'scheme',
        task_id: null,
        path: null,
        scheme_name: 'custom_scoring.v3.json',
      }),
    ])

    const wrapper = mountView(BasisView, pinia)
    const text = wrapper.text()
    expect(text).toContain('自定义清洗方案')
    expect(text).toContain('自定义评分方案')
    expect(text).not.toContain('cleaning_rules.v1.json')
    expect(text).not.toContain('scoring_scheme.v1.json')

    const buttons = wrapper.findAll('button')
    const cleaning = buttons.find((b) => b.text().includes('自定义清洗方案'))
    expect(cleaning).toBeDefined()
    await cleaning!.trigger('click')

    const centerTabs = useCenterTabsStore()
    expect(centerTabs.isOpen(schemeTabKey('custom_rules.v2.json'))).toBe(true)
  })

  it('没有方案产物时提示且不渲染按钮', () => {
    const pinia = makePinia()
    seed({ versions: { data: 'v1' } })
    useConversationsStore().currentId = CID

    const wrapper = mountView(BasisView, pinia)
    expect(wrapper.find('[data-testid="basis-no-schemes"]').text()).toContain(
      '该结果未提供方案文件',
    )
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
})
