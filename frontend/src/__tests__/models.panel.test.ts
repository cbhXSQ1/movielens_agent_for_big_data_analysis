// FE-ML-2 · 资源面板「模型」段：列表 / 详情（按 role 分组）/ 下载 / 预览。
// 只伪造全局 fetch（假后端），组件→store→api→http 全链真实执行。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import ResourcesPanel from '@/views/panels/ResourcesPanel.vue'
import { useConnectionStore } from '@/stores/connection'
import { API_BASE } from '@/api/config'

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

async function selectModel(wrapper: VueWrapper): Promise<void> {
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

describe('FE-ML-2 · 模型段：详情按 role 分组', () => {
  it('选中模型后按 role 分组展示产物', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    for (const role of ['model', 'profile', 'labels', 'coords', 'meta']) {
      expect(
        wrapper.find(`[data-testid="model-artifact-group-${role}"]`).exists(),
      ).toBe(true)
    }
    expect(
      wrapper.find('[data-testid="model-artifact-group-profile"]').text(),
    ).toContain('群体画像')
    expect(
      wrapper.find('[data-testid="model-artifact-group-coords"]').text(),
    ).toContain('用户坐标')
  })

  it('未知 role 归入「其它」，已知分组按其固定顺序在前', async () => {
    handlers.modelDetail = () =>
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
          artifacts: [
            { id: `${MODEL_ID}:custom.bin`, title: '自定义产物', kind: 'other', role: 'custom', path: 'custom.bin', size: 10 },
            { id: `${MODEL_ID}:meta.json`, title: '模型元数据', kind: 'json', role: 'meta', path: 'meta.json', size: 90 },
            { id: `${MODEL_ID}:coords.csv`, title: '用户坐标', kind: 'csv', role: 'coords', path: 'coords.csv', size: 400 },
          ],
        },
      })
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    const groups = wrapper
      .findAll('[data-testid^="model-artifact-group-"]')
      .map((node) => node.attributes('data-testid'))
    expect(groups).toEqual([
      'model-artifact-group-coords',
      'model-artifact-group-meta',
      'model-artifact-group-custom',
    ])
    const other = wrapper.find('[data-testid="model-artifact-group-custom"]')
    expect(other.text()).toContain('其它')
    expect(other.text()).toContain('自定义产物')
  })

  it('详情报错只在详情区回显，列表照常', async () => {
    handlers.modelDetail = () =>
      jsonResponse({
        ok: false,
        error: { code: 'MODEL_NOT_FOUND', message: '模型不存在' },
      })
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    expect(wrapper.find('[data-testid="model-detail-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="models-error"]').exists()).toBe(false)
    expect(wrapper.find(`[data-testid="model-row-${MODEL_ID}"]`).exists()).toBe(true)
  })
})

describe('FE-ML-2 · 模型段：下载与预览', () => {
  it('joblib 二进制只下载，提示不支持在线预览', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    const hint = wrapper.find(
      '[data-testid="model-artifact-no-preview-model.joblib"]',
    )
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('不支持在线预览')
    expect(
      wrapper.find('[data-testid="model-artifact-preview-btn-model.joblib"]').exists(),
    ).toBe(false)
    expect(
      wrapper.find('[data-testid="model-artifact-download-model.joblib"]').exists(),
    ).toBe(true)
  })

  it('每个产物都提供下载按钮，地址走 /api/models/download', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    const href = wrapper
      .find('[data-testid="model-artifact-download-coords.csv"]')
      .attributes('href')
    expect(href).toBeTruthy()
    const u = new URL(String(href))
    expect(u.origin + u.pathname).toBe(`${API_BASE}/api/models/download`)
    expect(u.searchParams.get('model_id')).toBe(MODEL_ID)
    expect(u.searchParams.get('path')).toBe('coords.csv')
  })

  it('文本产物（json）可预览：取模型下载地址为文本并展示', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    handlers.modelDownload = () =>
      textResponse('{"clusters":[{"label":"高活跃"}]}')
    await wrapper
      .find('[data-testid="model-artifact-preview-btn-profile.json"]')
      .trigger('click')
    await flushPromises()

    const content = wrapper.find('[data-testid="model-preview-content"]')
    expect(content.exists()).toBe(true)
    expect(content.text()).toContain('高活跃')

    const call = fetchMock.mock.calls.find(
      ([url]) => new URL(String(url)).pathname === '/api/models/download',
    )
    const u = new URL(String(call![0]))
    expect(u.searchParams.get('model_id')).toBe(MODEL_ID)
    expect(u.searchParams.get('path')).toBe('profile.json')
  })

  it('预览文本过长时截断并提示', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await selectModel(wrapper)

    handlers.modelDownload = () => textResponse('x'.repeat(20_000))
    await wrapper
      .find('[data-testid="model-artifact-preview-btn-labels.csv"]')
      .trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="model-preview-truncated"]').exists()).toBe(true)
  })
})
