// 动态产物标签内容（ArtifactView，§3.12）组件测试。
// 只 mock @/api/resources（假预览 / 假方案内容 / 假下载地址），
// 组件 → resources store → 渲染 全链真实执行。覆盖各 kind：md / json / text / dat / scheme。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ArtifactView from '@/views/tabs/ArtifactView.vue'
import type { ArtifactRef } from '@/api/types'

const api = vi.hoisted(() => ({
  previewFile: vi.fn(),
  getSchemeContent: vi.fn(),
  fileDownloadUrl: vi.fn(),
}))

vi.mock('@/api/resources', () => api)

const scoringContent = {
  config_type: 'scoring_scheme',
  scheme_id: 'ml1m-quality-default',
  version: '1.0.0',
  dimensions: [
    {
      id: 'Accurate',
      name_zh: '准确性',
      definition: '真实反映现实。',
      weight: 0.25,
      metrics: [
        {
          id: 'A1',
          name: '评分值域合法率',
          enabled: true,
          measure: 'ratio',
          weight: 0.25,
          description: 'x',
          limitations: '格式合法不代表真实。',
          unverifiable: [],
        },
      ],
    },
  ],
}

function artifact(over: Partial<ArtifactRef> = {}): ArtifactRef {
  return {
    id: 'task:t1:report.md',
    title: '评估报告',
    category: '报告',
    kind: 'md',
    source: 'task_file',
    task_id: 't1',
    path: 'report.md',
    scheme_name: null,
    ...over,
  }
}

function mountArtifact(a: ArtifactRef | null): VueWrapper {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(ArtifactView, {
    props: { artifact: a },
    global: { plugins: [pinia] },
  }) as VueWrapper
}

beforeEach(() => {
  api.previewFile.mockReset()
  api.getSchemeContent.mockReset()
  api.fileDownloadUrl.mockReset()
  api.fileDownloadUrl.mockImplementation(
    (tid: string, path: string) =>
      `/api/files/download?task_id=${tid}&path=${encodeURIComponent(path)}`,
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('FT.3 · 产物标签：task_file 各 kind 渲染', () => {
  it('md → Markdown 渲染', async () => {
    api.previewFile.mockResolvedValue({
      ok: true,
      path: 'report.md',
      previewable: true,
      kind: 'md',
      content: '# 迭代一评估报告\n\n正文。',
    })
    const wrapper = mountArtifact(artifact({ kind: 'md' }))
    await flushPromises()

    expect(api.previewFile).toHaveBeenCalledWith('t1', 'report.md')
    expect(wrapper.find('[data-testid="artifact-markdown"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('迭代一评估报告')
  })

  it('json → 美化 JSON（可折叠）', async () => {
    api.previewFile.mockResolvedValue({
      ok: true,
      path: 'scores.json',
      previewable: true,
      kind: 'json',
      content: '{"a":1}',
    })
    const wrapper = mountArtifact(
      artifact({ id: 'task:t1:scores.json', kind: 'json', path: 'scores.json' }),
    )
    await flushPromises()

    const json = wrapper.find('[data-testid="artifact-json"]')
    expect(json.exists()).toBe(true)
    expect(wrapper.text()).toContain('"a": 1')
  })

  it('text → 纯文本', async () => {
    api.previewFile.mockResolvedValue({
      ok: true,
      path: 'notes.txt',
      previewable: true,
      kind: 'text',
      content: 'plain text',
    })
    const wrapper = mountArtifact(
      artifact({ id: 'task:t1:notes.txt', kind: 'text', path: 'notes.txt' }),
    )
    await flushPromises()
    expect(wrapper.find('[data-testid="artifact-text"]').text()).toContain(
      'plain text',
    )
  })

  it('dat → 二进制提示 + 下载（不调用预览）', async () => {
    const wrapper = mountArtifact(
      artifact({
        id: 'task:t1:cleaned/ratings.dat',
        kind: 'dat',
        path: 'cleaned/ratings.dat',
      }),
    )
    await flushPromises()

    expect(api.previewFile).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="artifact-binary"]').exists()).toBe(true)
    const download = wrapper.find('[data-testid="artifact-download"]')
    expect(download.attributes('href')).toContain(
      '/api/files/download?task_id=t1',
    )
  })
})

describe('FT.3 · 产物标签：scheme 复用方案渲染', () => {
  it('scheme → 取方案内容并渲染维度/指标', async () => {
    api.getSchemeContent.mockResolvedValue({
      ok: true,
      name: 'scoring_scheme.v1.json',
      path: 'config/scoring_scheme.v1.json',
      content: scoringContent,
    })
    const wrapper = mountArtifact(
      artifact({
        id: 'scheme:scoring_scheme.v1.json',
        title: '评分方案',
        category: '依据',
        kind: 'scheme',
        source: 'scheme',
        task_id: null,
        path: null,
        scheme_name: 'scoring_scheme.v1.json',
      }),
    )
    await flushPromises()

    expect(api.getSchemeContent).toHaveBeenCalledWith('scoring_scheme.v1.json')
    expect(wrapper.find('[data-testid="artifact-scheme"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="basis-dimension-Accurate"]').exists()).toBe(
      true,
    )
    // scheme 无下载端点 → 不显示下载
    expect(wrapper.find('[data-testid="artifact-download"]').exists()).toBe(false)
  })
})

describe('FT.3 · 产物标签：错误与缺失', () => {
  it('取内容失败如实显示错误', async () => {
    api.previewFile.mockRejectedValue(new Error('预览失败'))
    const wrapper = mountArtifact(artifact())
    await flushPromises()
    expect(wrapper.find('[data-testid="artifact-error"]').text()).toContain(
      '预览失败',
    )
  })

  it('产物为空时显示缺失占位', () => {
    const wrapper = mountArtifact(null)
    expect(wrapper.find('[data-testid="artifact-missing"]').exists()).toBe(true)
  })
})
