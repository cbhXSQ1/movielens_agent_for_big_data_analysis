// 左栏（FC.2/FC.3/FC.4 增量）· 可拖拽调宽 + 顶部呼吸感回归。
// 约定：宽屏下左栏面板右缘有分隔条（left-rail-splitter），拖拽改宽并持久化；
// 双击复位；方向键微调；窄屏抽屉模式不出现分隔条。
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import AppShell from '@/components/AppShell.vue'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'

const MIN = 240
const MAX = 560
const DEFAULT = 320
const STEP = 16

function makePinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

function mountShell(pinia: Pinia = makePinia()) {
  return mount(AppShell, { global: { plugins: [pinia] } })
}

function splitter(wrapper: ReturnType<typeof mountShell>) {
  return wrapper.find('[data-testid="left-rail-splitter"]')
}

function panel(wrapper: ReturnType<typeof mountShell>) {
  return wrapper.find('[data-testid="left-panel"]')
}

function storedWidth(): string | null {
  return localStorage.getItem(SHELL_STORAGE_KEYS.leftWidth)
}

async function drag(
  wrapper: ReturnType<typeof mountShell>,
  fromX: number,
  toX: number,
): Promise<void> {
  await splitter(wrapper).trigger('pointerdown', { clientX: fromX })
  window.dispatchEvent(new MouseEvent('pointermove', { clientX: toX }))
  window.dispatchEvent(new MouseEvent('pointerup'))
  await nextTick()
}

beforeEach(() => {
  localStorage.clear()
  window.innerWidth = 1400
})

afterEach(() => {
  window.innerWidth = 1400
})

describe('左栏可拖拽调宽 · 分隔条', () => {
  it('宽屏默认宽度为 320px，并渲染可拖拽分隔条', () => {
    const wrapper = mountShell()
    expect(splitter(wrapper).exists()).toBe(true)
    expect(panel(wrapper).attributes('style')).toContain(`width: ${DEFAULT}px`)
  })

  it('挂载时从 localStorage 恢复宽度', () => {
    localStorage.setItem(SHELL_STORAGE_KEYS.leftWidth, '400')
    const wrapper = mountShell()
    expect(panel(wrapper).attributes('style')).toContain('width: 400px')
  })

  it('拖拽改变宽度并持久化到 localStorage', async () => {
    const wrapper = mountShell()
    // 面板左缘在图标条之后（64px），默认宽 320 → 右缘 x=384。
    await drag(wrapper, 384, 484)
    expect(panel(wrapper).attributes('style')).toContain('width: 420px')
    expect(storedWidth()).toBe('420')
  })

  it('拖拽在 MIN/MAX 处夹紧', async () => {
    const wrapper = mountShell()
    await drag(wrapper, 384, -9999)
    expect(panel(wrapper).attributes('style')).toContain(`width: ${MIN}px`)

    await drag(wrapper, 100, 99999)
    expect(panel(wrapper).attributes('style')).toContain(`width: ${MAX}px`)
  })

  it('双击分隔条复位为默认宽度', async () => {
    const wrapper = mountShell()
    await drag(wrapper, 384, 504)
    expect(panel(wrapper).attributes('style')).toContain('width: 440px')

    await splitter(wrapper).trigger('dblclick')
    await nextTick()
    expect(panel(wrapper).attributes('style')).toContain(`width: ${DEFAULT}px`)
    expect(storedWidth()).toBe(String(DEFAULT))
  })

  it('方向键按步长微调宽度（并夹紧）', async () => {
    const wrapper = mountShell()
    await splitter(wrapper).trigger('keydown', { key: 'ArrowRight' })
    await nextTick()
    expect(panel(wrapper).attributes('style')).toContain(`width: ${DEFAULT + STEP}px`)

    await splitter(wrapper).trigger('keydown', { key: 'ArrowLeft' })
    await splitter(wrapper).trigger('keydown', { key: 'ArrowLeft' })
    await nextTick()
    expect(panel(wrapper).attributes('style')).toContain(
      `width: ${DEFAULT - STEP}px`,
    )
  })

  it('分隔条具备可访问性语义', () => {
    const wrapper = mountShell()
    const el = splitter(wrapper)
    expect(el.attributes('role')).toBe('separator')
    expect(el.attributes('aria-orientation')).toBe('vertical')
    expect(el.attributes('aria-valuenow')).toBe(String(DEFAULT))
    expect(el.attributes('aria-valuemin')).toBe(String(MIN))
    expect(el.attributes('aria-valuemax')).toBe(String(MAX))
  })

  it('窄屏抽屉模式不渲染分隔条，也不改变收起/展开', async () => {
    window.innerWidth = 900
    const wrapper = mountShell()
    await nextTick()
    expect(splitter(wrapper).exists()).toBe(false)
    expect(wrapper.find('[data-testid="rail-toggle"]').exists()).toBe(false)

    window.innerWidth = 1400
    window.dispatchEvent(new Event('resize'))
    await nextTick()
    expect(splitter(wrapper).exists()).toBe(true)
  })

  it('收起面板时分隔条隐藏，展开后恢复', async () => {
    const wrapper = mountShell()
    expect(splitter(wrapper).exists()).toBe(true)
    await wrapper.find('[data-testid="rail-toggle"]').trigger('click')
    await nextTick()
    expect(splitter(wrapper).exists()).toBe(false)
    await wrapper.find('[data-testid="rail-toggle"]').trigger('click')
    await nextTick()
    expect(splitter(wrapper).exists()).toBe(true)
  })
})

describe('左栏顶部首行不被遮挡', () => {
  it('切到资源面板时，顶部分段标签行完整渲染在面板内', async () => {
    const wrapper = mountShell()
    await wrapper.find('[data-testid="panel-icon-resources"]').trigger('click')
    await nextTick()

    const tabs = wrapper.find('[data-testid="panel-resources"] .resources-panel__tabs')
    expect(tabs.exists()).toBe(true)
    expect(tabs.find('[data-testid="resources-tab-raw"]').exists()).toBe(true)
    expect(tabs.text()).toContain('原始数据')

    const panelEl = panel(wrapper)
    expect(panelEl.find('[data-testid="panel-resources"]').exists()).toBe(true)
    // 面板容器带内边距，首行不再贴着顶栏分隔线。
    expect(panelEl.attributes('data-top-inset')).toBe('true')
  })

  it('切到任务进度面板时标题行完整渲染', async () => {
    const wrapper = mountShell()
    await wrapper.find('[data-testid="panel-icon-progress"]').trigger('click')
    await nextTick()
    const panelEl = panel(wrapper)
    expect(panelEl.find('[data-testid="panel-progress"]').exists()).toBe(true)
    expect(panelEl.text()).toContain('任务进度')
  })
})
