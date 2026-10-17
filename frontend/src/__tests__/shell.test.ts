import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import AppShell from '@/components/AppShell.vue'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'
import { NARROW_BREAKPOINT } from '@/composables/useNarrowScreen'
import { useArtifactsStore } from '@/stores/artifacts'
import { useChatStore } from '@/stores/chat'
import { useConversationsStore } from '@/stores/conversations'
import type { ArtifactRef } from '@/api/types'

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

function artifact(
  id: string,
  title: string,
  over: Partial<ArtifactRef> = {},
): ArtifactRef {
  return {
    id,
    title,
    category: '其它',
    kind: 'other',
    source: 'task_file',
    task_id: null,
    path: null,
    scheme_name: null,
    ...over,
  }
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

describe('FC.5 · 中栏结果标签（默认只有「对话」，点链接才开、可关）', () => {
  function labels(wrapper: ReturnType<typeof mountShell>): string[] {
    return wrapper.findAll('.n-tabs-tab').map((t) => t.text().trim())
  }

  function link(wrapper: ReturnType<typeof mountShell>, id: string) {
    return wrapper.find(`[data-testid="artifact-link-${id}"]`)
  }

  it('默认只有「对话」标签，显示对话视图', () => {
    const wrapper = mountShell()
    expect(labels(wrapper)).toEqual(['对话'])
    expect(findByTestId(wrapper, 'view-chat').exists()).toBe(true)
    // 「对话」标签不可关闭
    expect(findByTestId(wrapper, 'tab-close-chat').exists()).toBe(false)
  })

  it('收到多个产物时默认仍只有「对话」（不自动开标签）', async () => {
    const pinia = makePinia()
    const wrapper = mountShell(pinia)
    const conversations = useConversationsStore()
    const artifacts = useArtifactsStore()

    conversations.currentId = 'c1'
    artifacts.add('c1', artifact('task:t1:report.md', '评估报告', { kind: 'md' }))
    artifacts.add('c1', artifact('task:t1:cleaned.dat', '清洗数据', { kind: 'dat' }))
    artifacts.add('c1', artifact('task:t1:scores.json', '指标', { kind: 'json' }))
    await nextTick()

    expect(labels(wrapper)).toEqual(['对话'])
  })

  it('点对话里的产物链接：新开并激活该标签；再点同一链接不重复开', async () => {
    const pinia = makePinia()
    const wrapper = mountShell(pinia)
    const conversations = useConversationsStore()
    const chat = useChatStore()
    const artifacts = useArtifactsStore()

    conversations.currentId = 'c1'
    const art = artifact('task:t1:cleaned.dat', '清洗数据', {
      kind: 'dat',
      task_id: 't1',
      path: 'cleaned/ratings.dat',
    })
    artifacts.add('c1', art)
    const msg = chat.ensure(1, 'assistant')
    msg.text = '已完成'
    msg.done = true
    chat.attachArtifact(art.id)
    await nextTick()

    expect(link(wrapper, art.id).exists()).toBe(true)
    expect(labels(wrapper)).toEqual(['对话'])

    await link(wrapper, art.id).trigger('click')
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '清洗数据'])
    // 已激活：显示产物视图
    expect(findByTestId(wrapper, 'artifact-view').exists()).toBe(true)

    // 切回「对话」再点同一链接：不重复开
    const chatTab = wrapper
      .findAll('.n-tabs-tab')
      .find((t) => t.text().trim() === '对话')
    await chatTab!.trigger('click')
    await nextTick()
    await link(wrapper, art.id).trigger('click')
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '清洗数据'])
    expect(findByTestId(wrapper, 'artifact-view').exists()).toBe(true)
  })

  it('产物标签的关闭按钮移除该标签；全关后只剩「对话」（产物仍在）', async () => {
    const pinia = makePinia()
    const wrapper = mountShell(pinia)
    const conversations = useConversationsStore()
    const artifacts = useArtifactsStore()

    conversations.currentId = 'c1'
    artifacts.add('c1', artifact('a', '报告'))
    artifacts.add('c1', artifact('b', '清洗'))
    artifacts.openTab('c1', 'a')
    artifacts.openTab('c1', 'b')
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '报告', '清洗'])

    await findByTestId(wrapper, 'tab-close-a').trigger('click')
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '清洗'])
    // 关标签不删产物
    expect(artifacts.find('c1', 'a')).not.toBeNull()

    await findByTestId(wrapper, 'tab-close-b').trigger('click')
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话'])
  })

  it('切换对话：标签集合随对话变化（已开集合隔离，切回恢复）', async () => {
    const pinia = makePinia()
    const wrapper = mountShell(pinia)
    const conversations = useConversationsStore()
    const artifacts = useArtifactsStore()

    artifacts.add('c1', artifact('a1', '报告A'))
    artifacts.add('c2', artifact('a2', '报告B'))
    artifacts.openTab('c1', 'a1')
    artifacts.openTab('c2', 'a2')

    conversations.currentId = 'c1'
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '报告A'])

    conversations.currentId = 'c2'
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '报告B'])

    conversations.currentId = 'c1'
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '报告A'])
  })
})

describe('FC.1 · 顶栏', () => {
  it('显示产品名、连接状态与设置入口', () => {
    const wrapper = mountShell()
    expect(wrapper.text()).toContain('电影数据分析助手')
    expect(findByTestId(wrapper, 'connection-status').text()).toContain('未连接')
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
