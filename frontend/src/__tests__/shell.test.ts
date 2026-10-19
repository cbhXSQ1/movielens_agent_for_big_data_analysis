import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import AppShell from '@/components/AppShell.vue'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'
import { NARROW_BREAKPOINT } from '@/composables/useNarrowScreen'

function makePinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

function mountShell(pinia: Pinia = makePinia()) {
  return mount(AppShell, {
    global: { plugins: [pinia] },
  })
}

function findByTestId(wrapper: ReturnType<typeof mountShell>, testid: string) {
  return wrapper.find(`[data-testid="${testid}"]`)
}

beforeEach(() => {
  localStorage.clear()
  window.innerWidth = 1400
})

afterEach(() => {
  window.innerWidth = 1400
})

describe('FC.2/FC.3 · 左栏图标切换与收起/展开', () => {
  it('默认显示「对话列表」面板，点图标切换到「对话树」', async () => {
    const wrapper = mountShell()
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(true)
    expect(findByTestId(wrapper, 'panel-list').exists()).toBe(true)

    await findByTestId(wrapper, 'panel-icon-tree').trigger('click')
    await nextTick()

    expect(findByTestId(wrapper, 'panel-tree').exists()).toBe(true)
    expect(findByTestId(wrapper, 'panel-list').exists()).toBe(false)
  })

  it('收起只留图标条，展开恢复面板，并记忆到 localStorage', async () => {
    const wrapper = mountShell()
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(true)

    await findByTestId(wrapper, 'rail-toggle').trigger('click')
    await nextTick()
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(false)
    expect(localStorage.getItem(SHELL_STORAGE_KEYS.collapsed)).toBe('1')

    await findByTestId(wrapper, 'rail-toggle').trigger('click')
    await nextTick()
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(true)
    expect(localStorage.getItem(SHELL_STORAGE_KEYS.collapsed)).toBe('0')
  })

  it('收起态下点图标会自动展开面板', async () => {
    const wrapper = mountShell()
    await findByTestId(wrapper, 'rail-toggle').trigger('click')
    await nextTick()
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(false)

    await findByTestId(wrapper, 'panel-icon-resources').trigger('click')
    await nextTick()
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(true)
    expect(findByTestId(wrapper, 'panel-resources').exists()).toBe(true)
  })

  it('四个图标齐备，点「资源」「任务进度」切到对应面板', async () => {
    const wrapper = mountShell()
    for (const key of ['list', 'tree', 'resources', 'progress']) {
      expect(findByTestId(wrapper, `panel-icon-${key}`).exists()).toBe(true)
    }

    await findByTestId(wrapper, 'panel-icon-resources').trigger('click')
    await nextTick()
    expect(findByTestId(wrapper, 'panel-resources').exists()).toBe(true)

    await findByTestId(wrapper, 'panel-icon-progress').trigger('click')
    await nextTick()
    expect(findByTestId(wrapper, 'panel-progress').exists()).toBe(true)
    expect(findByTestId(wrapper, 'panel-resources').exists()).toBe(false)
  })
})

describe('FC.4 · 窄屏抽屉判定', () => {
  it(`宽度 ≤ ${NARROW_BREAKPOINT}px 视为窄屏：左栏变抽屉、内联面板隐藏`, async () => {
    window.innerWidth = 900
    const wrapper = mountShell()
    await nextTick()

    expect(findByTestId(wrapper, 'left-rail').classes()).toContain('left-rail--narrow')
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(false)
    expect(findByTestId(wrapper, 'rail-toggle').exists()).toBe(false)
  })

  it('由窄变宽（resize）后恢复内联面板', async () => {
    window.innerWidth = 900
    const wrapper = mountShell()
    await nextTick()
    expect(findByTestId(wrapper, 'left-rail').classes()).toContain('left-rail--narrow')

    window.innerWidth = 1400
    window.dispatchEvent(new Event('resize'))
    await nextTick()

    expect(findByTestId(wrapper, 'left-rail').classes()).not.toContain('left-rail--narrow')
    expect(findByTestId(wrapper, 'left-panel').exists()).toBe(true)
  })
})

describe('FC.5 · 中栏固定 7 标签（默认只有「对话」，可关可开）', () => {
  function labels(wrapper: ReturnType<typeof mountShell>): string[] {
    return wrapper
      .findAll('.tab')
      .map((t) => t.text().trim().replace(/×$/, '').trim())
  }

  it('默认只有「对话」标签，显示对话视图', () => {
    const wrapper = mountShell()
    expect(labels(wrapper)).toEqual(['对话'])
    expect(findByTestId(wrapper, 'view-chat').exists()).toBe(true)
    // 「对话」标签不可关闭
    expect(findByTestId(wrapper, 'tab-close-chat').exists()).toBe(false)
  })

  it('右栏结果入口点击打开对应标签', async () => {
    const wrapper = mountShell()
    // 右栏默认展开（宽屏），点「五维评分」入口
    const entry = wrapper.find('[data-testid="right-rail"] .rp-grid button')
    expect(entry.exists()).toBe(true)
    await entry.trigger('click')
    await nextTick()
    // 出现至少两个标签（对话 + 五维评分）
    expect(labels(wrapper).length).toBeGreaterThanOrEqual(2)
    expect(labels(wrapper)).toContain('五维评分')
  })

  it('关闭标签后回到「对话」', async () => {
    const wrapper = mountShell()
    // 先开 score
    const entry = wrapper.find('[data-testid="right-rail"] .rp-grid button')
    await entry.trigger('click')
    await nextTick()
    expect(labels(wrapper)).toContain('五维评分')

    await findByTestId(wrapper, 'tab-close-score').trigger('click')
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话'])
  })
})

describe('FC.1 · 中央顶栏', () => {
  it('显示品牌、连接状态与设置入口', () => {
    const wrapper = mountShell()
    // 品牌在左栏
    expect(wrapper.text()).toContain('MovieLens Agent')
    expect(findByTestId(wrapper, 'connection-status').exists()).toBe(true)
    expect(findByTestId(wrapper, 'settings-entry').exists()).toBe(true)
  })

  it('点「设置」真正打开设置弹窗（含保存 / 测试连接）', async () => {
    const wrapper = mountShell()
    await findByTestId(wrapper, 'settings-entry').trigger('click')
    await nextTick()
    await flushPromises()

    expect(document.querySelector('[data-testid="settings-modal"]')).toBeTruthy()
    expect(document.querySelector('[data-testid="settings-save"]')).toBeTruthy()
    expect(document.querySelector('[data-testid="settings-test"]')).toBeTruthy()
    wrapper.unmount()
  })
})
