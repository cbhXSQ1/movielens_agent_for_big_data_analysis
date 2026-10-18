// 阶段 C · 中栏容器与资源标签集成：资源标签出现在标签栏、可切换到其视图、关闭回落「对话」。
// 资源视图经注册表分发，内容经 resources store 取得（此处 mock 端点，链路其余部分真实执行）。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import CenterTabs from '@/components/CenterTabs.vue'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { schemeTab } from '@/components/centerTabViews'

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

vi.mock('@/api/resources', () => resourcesApi)

function makePinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

function mountCenter(pinia: Pinia): VueWrapper {
  return mount(CenterTabs, { global: { plugins: [pinia] } }) as VueWrapper
}

function labels(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.n-tabs-tab').map((t) => t.text().trim())
}

function tabByLabel(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('.n-tabs-tab').find((t) => t.text().trim() === label)
}

beforeEach(() => {
  localStorage.clear()
  for (const fn of Object.values(resourcesApi)) fn.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('CenterTabs · 资源标签渲染 / 切换 / 关闭回落', () => {
  it('默认只有「对话」；store 里已有的资源标签一并呈现', async () => {
    const pinia = makePinia()
    useCenterTabsStore().open(schemeTab('s.json'))
    const wrapper = mountCenter(pinia)
    await nextTick()

    expect(labels(wrapper)).toEqual(['对话', 's.json'])
    // 资源标签可关闭
    expect(
      wrapper.find('[data-testid="tab-close-resource:scheme:s.json"]').exists(),
    ).toBe(true)
  })

  it('点资源标签切到其视图（经注册表渲染 SchemeContentView）', async () => {
    const pinia = makePinia()
    useCenterTabsStore().open(schemeTab('s.json'))
    resourcesApi.getSchemeContent.mockResolvedValue({
      ok: true,
      name: 's.json',
      path: 'config/s.json',
      content: { weights: { popularity: 0.5 } },
    })
    const wrapper = mountCenter(pinia)
    await nextTick()

    await tabByLabel(wrapper, 's.json')!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="scheme-content-view"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="scheme-content-json"]').text()).toContain(
      'popularity',
    )
  })

  it('关闭当前资源标签 → 回落「对话」', async () => {
    const pinia = makePinia()
    useCenterTabsStore().open(schemeTab('s.json'))
    resourcesApi.getSchemeContent.mockResolvedValue({
      ok: true,
      name: 's.json',
      path: 'config/s.json',
      content: {},
    })
    const wrapper = mountCenter(pinia)
    await nextTick()

    await tabByLabel(wrapper, 's.json')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="scheme-content-view"]').exists()).toBe(true)

    await wrapper
      .find('[data-testid="tab-close-resource:scheme:s.json"]')
      .trigger('click')
    await nextTick()

    expect(labels(wrapper)).toEqual(['对话'])
    expect(wrapper.find('[data-testid="view-chat"]').exists()).toBe(true)
    expect(useCenterTabsStore().isOpen('resource:scheme:s.json')).toBe(false)
  })
})
