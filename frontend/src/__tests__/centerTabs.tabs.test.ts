// 新前端 · 中栏固定 7 标签的渲染 / 切换 / 关闭回落。
// 标签集合 = 基础「对话」+ 已打开的可关闭标签（由 AppShell 经 visibleTabs 传入）。
// 关闭只关视图不删数据；全关回「对话」。
import { describe, it, expect, beforeEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import CenterTabs from '@/components/CenterTabs.vue'
import type { ResultTabKey } from '@/components/shellConfig'

function makePinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

function mountCenter(
  pinia: Pinia,
  visible: ResultTabKey[] = ['chat'],
): VueWrapper {
  return mount(CenterTabs, {
    global: { plugins: [pinia] },
    props: { visibleTabs: visible },
  }) as VueWrapper
}

function labels(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('.tab')
    .map((t) => t.text().trim().replace(/×$/, '').trim())
}

function closeBtn(wrapper: VueWrapper, key: string) {
  return wrapper.find(`[data-testid="tab-close-${key}"]`)
}

beforeEach(() => {
  localStorage.clear()
})

describe('CenterTabs · 固定 7 标签渲染 / 切换 / 关闭', () => {
  it('默认只有「对话」标签（可关闭标签未打开时不显示）', async () => {
    const pinia = makePinia()
    const wrapper = mountCenter(pinia, ['chat'])
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话'])
    // 「对话」标签不可关闭
    expect(closeBtn(wrapper, 'chat').exists()).toBe(false)
  })

  it('visibleTabs 含 score/report 时呈现对应标签', async () => {
    const pinia = makePinia()
    const wrapper = mountCenter(pinia, ['chat', 'score', 'report'])
    await nextTick()
    expect(labels(wrapper)).toEqual(['对话', '五维评分', '评估报告.md'])
    expect(closeBtn(wrapper, 'score').exists()).toBe(true)
  })

  it('关闭标签 emit close 事件（由上层决定移除）', async () => {
    const pinia = makePinia()
    const wrapper = mountCenter(pinia, ['chat', 'score'])
    await nextTick()
    await closeBtn(wrapper, 'score').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
    expect(wrapper.emitted('close')![0]).toEqual(['score'])
  })

  it('点标签切换 active', async () => {
    const pinia = makePinia()
    const wrapper = mountCenter(pinia, ['chat', 'score', 'ml'])
    await nextTick()
    const tabs = wrapper.findAll('.tab')
    const scoreTab = tabs.find((t) => t.text().includes('五维评分'))!
    await scoreTab.trigger('click')
    const updateEvents = wrapper.emitted('update:active')
    expect(updateEvents).toBeTruthy()
    expect(updateEvents![updateEvents!.length - 1]).toEqual(['score'])
  })
})
