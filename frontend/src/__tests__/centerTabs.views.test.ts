// 阶段 C · 中栏资源标签三视图单测：
//   - ModelDetailView：经 models store 取详情 → 指标（复用 ML 结果视图）+ 产物按 role 分组 + 下载 / 预览。
//   - SchemeContentView：经 resources store 取方案内容 → 美化 JSON。
//   - FilePreviewView：经 resources store 预览 → md / json / text 渲染与不可预览提示。
// 只 mock API 端点（假后端），组件 → store → 渲染 全链真实执行。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ModelDetailView from '@/views/tabs/ModelDetailView.vue'
import SchemeContentView from '@/views/tabs/SchemeContentView.vue'
import FilePreviewView from '@/views/tabs/FilePreviewView.vue'

const modelsApi = vi.hoisted(() => ({
  listModels: vi.fn(),
  getModel: vi.fn(),
  modelDownloadUrl: vi.fn(),
  previewModelArtifact: vi.fn(),
}))
const resourcesApi = vi.hoisted(() => ({
  listRawData: vi.fn(),
  listPublishedData: vi.fn(),
  listSchemes: vi.fn(),
  uploadScheme: vi.fn(),
  deleteScheme: vi.fn(),
  getSchemeContent: vi.fn(),
  listTaskFiles: vi.fn(),
  previewFile: vi.fn(),
  fileDownloadUrl: vi.fn(),
}))

vi.mock('@/api/models', () => modelsApi)
vi.mock('@/api/resources', () => resourcesApi)

const MODEL_ID = 'ml-20261018-103000-a1b2'

function makePinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

function mountView(
  component: unknown,
  props: Record<string, unknown>,
  pinia: Pinia,
): VueWrapper {
  return mount(component as never, {
    props: props as never,
    global: { plugins: [pinia] },
  }) as VueWrapper
}

beforeEach(() => {
  localStorage.clear()
  for (const fn of Object.values(modelsApi)) fn.mockReset()
  for (const fn of Object.values(resourcesApi)) fn.mockReset()
  modelsApi.modelDownloadUrl.mockImplementation(
    (id: string, path: string) =>
      `/api/models/download?model_id=${id}&path=${encodeURIComponent(path)}`,
  )
  resourcesApi.fileDownloadUrl.mockImplementation(
    (tid: string, path: string) =>
      `/api/files/download?task_id=${tid}&path=${encodeURIComponent(path)}`,
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// ModelDetailView
// ---------------------------------------------------------------------------

const ARTIFACTS = [
  { id: `${MODEL_ID}:model.joblib`, title: '模型本体', kind: 'joblib', role: 'model', path: 'model.joblib', size: 2048 },
  { id: `${MODEL_ID}:profile.json`, title: '群体画像', kind: 'json', role: 'profile', path: 'profile.json', size: 120 },
  { id: `${MODEL_ID}:meta.json`, title: '模型元数据', kind: 'json', role: 'meta', path: 'meta.json', size: 90 },
]

describe('ModelDetailView · 指标 + 产物分组 + 下载 / 预览', () => {
  function mountModel(): VueWrapper {
    modelsApi.getModel.mockResolvedValue({
      ok: true,
      model: {
        model_id: MODEL_ID,
        task_type: 'classification',
        algorithm: 'random_forest',
        data_version: 'ml1m-clean-v1',
        created_at: '2026-10-18 10:30:00',
        metrics_summary: {},
        metrics: { f1: 0.7846, auc: 0.8231 },
        artifacts: ARTIFACTS,
      },
    })
    return mountView(ModelDetailView, { modelId: MODEL_ID }, makePinia())
  }

  it('经 store 拉取详情，渲染标题与指标（复用 ML 分类结果视图）', async () => {
    const wrapper = mountModel()
    await flushPromises()

    expect(modelsApi.getModel).toHaveBeenCalledWith(MODEL_ID)
    expect(wrapper.find('[data-testid="model-detail-title"]').text()).toContain(MODEL_ID)
    expect(wrapper.find('[data-testid="model-detail-metrics"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-classification-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-metric-f1"]').text()).toContain('78.46')
  })

  it('产物按 role 分组展示', async () => {
    const wrapper = mountModel()
    await flushPromises()

    for (const role of ['model', 'profile', 'meta']) {
      expect(
        wrapper.find(`[data-testid="model-detail-group-${role}"]`).exists(),
      ).toBe(true)
    }
    expect(
      wrapper.find('[data-testid="model-detail-group-profile"]').text(),
    ).toContain('群体画像')
  })

  it('joblib 只下载 + 提示不支持在线预览；文本类可预览', async () => {
    const wrapper = mountModel()
    await flushPromises()

    const noPreview = wrapper.find(
      '[data-testid="model-detail-no-preview-model.joblib"]',
    )
    expect(noPreview.exists()).toBe(true)
    expect(noPreview.text()).toContain('不支持在线预览')
    expect(
      wrapper.find('[data-testid="model-detail-preview-btn-profile.json"]').exists(),
    ).toBe(true)

    const href = wrapper
      .find('[data-testid="model-detail-download-model.joblib"]')
      .attributes('href')
    expect(String(href)).toContain('/api/models/download')
    expect(String(href)).toContain(`model_id=${MODEL_ID}`)
  })

  it('预览文本类产物：取模型下载地址为文本并渲染', async () => {
    const wrapper = mountModel()
    await flushPromises()

    modelsApi.previewModelArtifact.mockResolvedValue('{"clusters":[{"label":"高活跃"}]}')
    await wrapper
      .find('[data-testid="model-detail-preview-btn-profile.json"]')
      .trigger('click')
    await flushPromises()

    expect(modelsApi.previewModelArtifact).toHaveBeenCalledWith(
      MODEL_ID,
      'profile.json',
    )
    const content = wrapper.find('[data-testid="model-detail-preview-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('高活跃')
  })

  it('详情报错时如实回显', async () => {
    modelsApi.getModel.mockRejectedValue(new Error('模型不存在'))
    const wrapper = mountView(ModelDetailView, { modelId: MODEL_ID }, makePinia())
    await flushPromises()

    expect(
      wrapper.find('[data-testid="model-detail-view-error"]').text(),
    ).toContain('模型不存在')
  })
})

// ---------------------------------------------------------------------------
// SchemeContentView
// ---------------------------------------------------------------------------

describe('SchemeContentView · 美化 JSON', () => {
  it('经 resources store 取方案内容并美化 JSON 展示', async () => {
    resourcesApi.getSchemeContent.mockResolvedValue({
      ok: true,
      name: 'scoring_scheme.v1.json',
      path: 'config/scoring_scheme.v1.json',
      content: { weights: { popularity: 0.5 } },
    })
    const wrapper = mountView(
      SchemeContentView,
      { name: 'scoring_scheme.v1.json' },
      makePinia(),
    )
    await flushPromises()

    expect(resourcesApi.getSchemeContent).toHaveBeenCalledWith(
      'scoring_scheme.v1.json',
    )
    const json = wrapper.find('[data-testid="scheme-content-json"]')
    expect(json.exists()).toBe(true)
    expect(json.text()).toContain('"popularity": 0.5')
  })

  it('取内容失败如实显示错误', async () => {
    resourcesApi.getSchemeContent.mockRejectedValue(new Error('FILE_NOT_FOUND'))
    const wrapper = mountView(SchemeContentView, { name: 'x.json' }, makePinia())
    await flushPromises()

    expect(wrapper.find('[data-testid="scheme-content-error"]').text()).toContain(
      'FILE_NOT_FOUND',
    )
  })
})

// ---------------------------------------------------------------------------
// FilePreviewView
// ---------------------------------------------------------------------------

describe('FilePreviewView · md / json / text 与不可预览', () => {
  function mountFile() {
    return mountView(
      FilePreviewView,
      { taskId: 'job_1', path: 'scores.json' },
      makePinia(),
    )
  }

  it('json → 美化 JSON', async () => {
    resourcesApi.previewFile.mockResolvedValue({
      ok: true,
      path: 'scores.json',
      previewable: true,
      kind: 'json',
      content: '{"a":1}',
    })
    const wrapper = mountFile()
    await flushPromises()

    expect(resourcesApi.previewFile).toHaveBeenCalledWith('job_1', 'scores.json')
    expect(wrapper.find('[data-testid="file-preview-content"]').text()).toContain(
      '"a": 1',
    )
    expect(
      wrapper.find('[data-testid="file-preview-download"]').attributes('href'),
    ).toContain('task_id=job_1')
  })

  it('md → Markdown 渲染', async () => {
    resourcesApi.previewFile.mockResolvedValue({
      ok: true,
      path: 'report.md',
      previewable: true,
      kind: 'md',
      content: '# 评估报告\n\n正文。',
    })
    const wrapper = mountView(
      FilePreviewView,
      { taskId: 'job_1', path: 'report.md' },
      makePinia(),
    )
    await flushPromises()

    const content = wrapper.find('[data-testid="file-preview-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('评估报告')
  })

  it('text → 纯文本', async () => {
    resourcesApi.previewFile.mockResolvedValue({
      ok: true,
      path: 'notes.txt',
      previewable: true,
      kind: 'text',
      content: 'plain text',
    })
    const wrapper = mountView(
      FilePreviewView,
      { taskId: 'job_1', path: 'notes.txt' },
      makePinia(),
    )
    await flushPromises()

    expect(wrapper.find('[data-testid="file-preview-content"]').text()).toContain(
      'plain text',
    )
  })

  it('不可预览 → 提示改用下载', async () => {
    resourcesApi.previewFile.mockResolvedValue({
      ok: true,
      path: 'cleaned/ratings.dat',
      previewable: false,
      kind: 'dat',
    })
    const wrapper = mountView(
      FilePreviewView,
      { taskId: 'job_1', path: 'cleaned/ratings.dat' },
      makePinia(),
    )
    await flushPromises()

    expect(
      wrapper.find('[data-testid="file-preview-unavailable"]').exists(),
    ).toBe(true)
    expect(wrapper.find('[data-testid="file-preview-content"]').exists()).toBe(false)
  })
})
