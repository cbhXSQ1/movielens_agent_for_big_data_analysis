// FE-ML-2 · 资源面板「模型」段：列表 + 「查看」在中栏开资源标签。
// 左栏不再内联详情/产物预览（改由中栏 ModelDetailView 承担）；「查看」只登记中栏标签。
// 只伪造全局 fetch（假后端），组件→store→api→http 全链真实执行。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import ResourcesPanel from '@/views/panels/ResourcesPanel.vue'
import { useConnectionStore } from '@/stores/connection'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { modelTabKey } from '@/components/centerTabs'

const MODEL_ID = 'ml-20261018-103000-a1b2'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function textResponse(body: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  } as unknown as Response
}

interface Handlers {
  raw: () => Response
  published: () => Response
  schemes: () => Response
  modelList: () => Response
  modelDetail: () => Response
  modelDownload: () => Response
}

let handlers: Handlers
let fetchMock: ReturnType<typeof vi.fn>

function installFetch(): void {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input)).pathname
    if (path === '/api/models/download') return handlers.modelDownload()
    if (path === '/api/models') return handlers.modelList()
    if (/^\/api\/models\/[^/]+$/.test(path)) return handlers.modelDetail()
    switch (path) {
      case '/api/data/raw':
        return handlers.raw()
      case '/api/data/published':
        return handlers.published()
      case '/api/schemes':
        return handlers.schemes()
      default:
        return jsonResponse({ ok: true })
    }
  })
  vi.stubGlobal('fetch', fetchMock)
}

function mountPanel(): VueWrapper {
  const pinia = createPinia()
  setActivePinia(pinia)
  const connection = useConnectionStore()
  connection.markStreamOpen()
  return mount(ResourcesPanel, { global: { plugins: [pinia] } })
}

/** 切到「模型」段并点某模型的「查看」（现在会登记一个中栏资源标签）。 */
async function openModelTab(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('[data-testid="resources-tab-models"]').trigger('click')
  await flushPromises()
  await wrapper.find(`[data-testid="model-select-${MODEL_ID}"]`).trigger('click')
  await flushPromises()
}

const ARTIFACTS = [
  { id: `${MODEL_ID}:model.joblib`, title: '模型本体', kind: 'joblib', role: 'model', path: 'model.joblib', size: 2048 },
  { id: `${MODEL_ID}:profile.json`, title: '群体画像', kind: 'json', role: 'profile', path: 'profile.json', size: 120 },
  { id: `${MODEL_ID}:labels.csv`, title: '每用户群标签', kind: 'csv', role: 'labels', path: 'labels.csv', size: 300 },
  { id: `${MODEL_ID}:coords.csv`, title: '用户坐标', kind: 'csv', role: 'coords', path: 'coords.csv', size: 400 },
  { id: `${MODEL_ID}:meta.json`, title: '模型元数据', kind: 'json', role: 'meta', path: 'meta.json', size: 90 },
]

beforeEach(() => {
  setActivePinia(createPinia())
  handlers = {
    raw: () => jsonResponse({ ok: true, dir: '/raw', datasets: [] }),
    published: () => jsonResponse({ ok: true, datasets: [] }),
    schemes: () => jsonResponse({ ok: true, schemes: [] }),
    modelList: () =>
      jsonResponse({
        ok: true,
        models: [
          {
            model_id: MODEL_ID,
            task_type: 'classification',
            algorithm: 'random_forest',
            data_version: 'ml1m-clean-v1',
            created_at: '2026-10-18 10:30:00',
            metrics_summary: { f1: 0.7846, auc: 0.8231 },
          },
          {
            model_id: 'ml-20261018-110000-c3d4',
            task_type: 'clustering',
            algorithm: 'kmeans',
            data_version: 'ml1m-clean-v1',
            created_at: '2026-10-18 11:00:00',
            metrics_summary: { k: 4, silhouette: 0.5123 },
          },
        ],
      }),
    modelDetail: () =>
      jsonResponse({
        ok: true,
        model: {
          model_id: MODEL_ID,
          task_type: 'classification',
          algorithm: 'random_forest',
          data_version: 'ml1m-clean-v1',
          created_at: '2026-10-18 10:30:00',
          metrics_summary: { f1: 0.7846 },
          metrics: { f1: 0.7846 },
          artifacts: ARTIFACTS,
        },
      }),
    modelDownload: () => textResponse('{"clusters":[{"label":"高活跃"}]}'),
  }
  installFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document
    .querySelectorAll('.v-binder-follower-container')
    .forEach((node) => node.remove())
})

describe('FE-ML-2 · 模型段：列表', () => {
  it('新增「模型」页签，连接就绪后拉取模型列表', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('[data-testid="resources-tab-models"]').exists()).toBe(true)
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).toContain('/api/models')
    expect(wrapper.find(`[data-testid="model-row-${MODEL_ID}"]`).exists()).toBe(true)
  })

  it('每行列出编号 / 任务类型 / 算法 / 数据版本 / 时间 / 指标摘要', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="resources-tab-models"]').trigger('click')

    const row = wrapper.find(`[data-testid="model-row-${MODEL_ID}"]`)
    const text = row.text()
    expect(text).toContain(MODEL_ID)
    expect(text).toContain('classification')
    expect(text).toContain('random_forest')
    expect(text).toContain('ml1m-clean-v1')
    expect(text).toContain('2026-10-18 10:30:00')
    expect(text).toContain('f1')
    expect(text).toContain('0.7846')
  })
})

describe('FE-ML-2 · 模型段：查看在中栏开标签', () => {
  it('点「查看」把模型登记为中栏资源标签（不再左栏内联详情）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await openModelTab(wrapper)

    const centerTabs = useCenterTabsStore()
    expect(centerTabs.isOpen(modelTabKey(MODEL_ID))).toBe(true)
    expect(centerTabs.list().map((t) => t.key)).toContain(modelTabKey(MODEL_ID))
    expect(wrapper.find('[data-testid="model-detail"]').exists()).toBe(false)

    // 「查看」不再在左栏内联拉详情
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
    expect(paths).not.toContain(`/api/models/${MODEL_ID}`)
  })

  it('重复点同一模型「查看」按 key 去重（只保留一个标签）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('[data-testid="resources-tab-models"]').trigger('click')
    await flushPromises()
    await wrapper.find(`[data-testid="model-select-${MODEL_ID}"]`).trigger('click')
    await wrapper.find(`[data-testid="model-select-${MODEL_ID}"]`).trigger('click')

    const matching = useCenterTabsStore()
      .list()
      .filter((t) => t.key === modelTabKey(MODEL_ID))
    expect(matching).toHaveLength(1)
  })

  it('左栏不再内联产物预览 / 下载入口（改由中栏标签承担）', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await openModelTab(wrapper)

    expect(wrapper.find('[data-testid="model-preview-panel"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid^="model-artifact-"]').exists()).toBe(false)
    expect(wrapper.find(`[data-testid="model-row-${MODEL_ID}"]`).exists()).toBe(true)
  })
})
