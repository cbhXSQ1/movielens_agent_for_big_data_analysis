// 贯穿 · FT.1（composable 单测）：
//   - useNarrowScreen  ：窄屏判定（阈值 / resize 实时更新 / 挂载清理）
//   - useResultBlocks  ：tasks.latestResult → 视图可直接消费的计算值（防御式规范化）
//   - useResultTabNav  ：壳层 provide 的中栏跳转契约（有/无 provide 两种情形）
// 每个 composable 都在一个最小宿主组件里运行（onMounted / inject 需要组件上下文），
// 不依赖真实后端。

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

import { NARROW_BREAKPOINT, useNarrowScreen } from '@/composables/useNarrowScreen'
import { useResultBlocks } from '@/composables/useResultBlocks'
import {
  provideResultTabNav,
  useResultTabNav,
  type ResultTabNavigator,
} from '@/composables/useResultTabNav'
import { useTasksStore } from '@/stores/tasks'
import type { ResultPayload, TaskView } from '@/api/types'

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  window.innerWidth = 1400
})

afterEach(() => {
  window.innerWidth = 1400
  vi.restoreAllMocks()
})

/** 在最小宿主组件里运行一个 composable，并暴露其返回值。 */
function runComposable<T>(use: () => T): { wrapper: VueWrapper; get: () => T } {
  let exposed!: T
  const Host = defineComponent({
    setup() {
      exposed = use()
      return () => h('div')
    },
  })
  const wrapper = mount(Host, { global: { plugins: [pinia] } })
  return { wrapper, get: () => exposed }
}

// ---------------------------------------------------------------------------
// useNarrowScreen（FC.4）
// ---------------------------------------------------------------------------

describe('FT.1 · useNarrowScreen 窄屏判定', () => {
  it('宽屏为 false；窄屏（≤ 阈值）为 true', () => {
    window.innerWidth = 1400
    expect(runComposable(() => useNarrowScreen()).get().value).toBe(false)

    window.innerWidth = 900
    expect(runComposable(() => useNarrowScreen()).get().value).toBe(true)
  })

  it('resize 实时更新；恰好等于阈值判为窄屏', async () => {
    window.innerWidth = 1400
    const { get } = runComposable(() => useNarrowScreen())
    expect(get().value).toBe(false)

    window.innerWidth = NARROW_BREAKPOINT
    window.dispatchEvent(new Event('resize'))
    await nextTick()
    expect(get().value).toBe(true)

    window.innerWidth = NARROW_BREAKPOINT + 1
    window.dispatchEvent(new Event('resize'))
    await nextTick()
    expect(get().value).toBe(false)
  })

  it('挂载登记 resize 监听，卸载移除（无泄漏）', () => {
    const add = vi.spyOn(window, 'addEventListener')
    const remove = vi.spyOn(window, 'removeEventListener')
    const { wrapper } = runComposable(() => useNarrowScreen())
    expect(add).toHaveBeenCalledWith('resize', expect.any(Function))
    wrapper.unmount()
    expect(remove).toHaveBeenCalledWith('resize', expect.any(Function))
  })

  it('自定义阈值生效', () => {
    window.innerWidth = 800
    expect(runComposable(() => useNarrowScreen(600)).get().value).toBe(false)
    expect(runComposable(() => useNarrowScreen(900)).get().value).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// useResultBlocks（FE.1）
// ---------------------------------------------------------------------------

function task(): TaskView {
  return {
    agent_task_id: 'a1',
    hadoop_task_id: 'H-1',
    name: '清洗任务',
    summary: '清洗并评分',
    status: 'succeeded',
    status_cn: '成功',
    stage: 'done',
    progress_percent: 100,
    estimated_seconds: null,
    updated_at: 1,
  }
}

function payload(blocks: Record<string, unknown>): ResultPayload {
  return { ok: true, task: task(), blocks }
}

describe('FT.1 · useResultBlocks 结果块绑定', () => {
  it('无结果时各计算值为 null / 空（不造数字）', () => {
    const { get } = runComposable(() => useResultBlocks())
    const b = get()
    expect(b.result.value).toBeNull()
    expect(b.task.value).toBeNull()
    expect(b.taskId.value).toBeNull()
    expect(b.blocks.value).toBeNull()
    expect(b.scoreComparison.value).toBeNull()
    expect(b.dataVolume.value).toBeNull()
    expect(b.quarantineSamples.value).toEqual([])
    expect(b.limitations.value).toEqual([])
    expect(b.report.value).toBe('')
  })

  it('绑定 store.latestResult 并做防御式规范化', async () => {
    const { get } = runComposable(() => useResultBlocks())
    useTasksStore().applyResultPayload(
      payload({
        score_comparison: { before: { Accurate: 90 }, after: { Accurate: 99 } },
        data_volume: { input: { ratings_lines: 10 } },
        quarantine_samples: [{ line_no: 3, rule_id: 'R2', reason: '评分越界' }],
        limitations: ['隔离 ≠ 修复', 123, { text: '时效性局限' }],
        report: { markdown: '# 评估报告' },
      }),
    )
    await nextTick()

    const b = get()
    expect(b.taskId.value).toBe('a1')
    expect(b.task.value?.name).toBe('清洗任务')
    expect(b.scoreComparison.value?.rows.map((r) => r.key)).toEqual(['Accurate'])
    expect(b.scoreComparison.value?.delta.Accurate).toBe(9)
    expect(b.dataVolume.value?.input[0].value).toBe(10)
    expect(b.quarantineSamples.value[0].ruleId).toBe('R2')
    expect(b.limitations.value).toEqual(['隔离 ≠ 修复', '时效性局限'])
    expect(b.report.value).toBe('# 评估报告')
  })
})

// ---------------------------------------------------------------------------
// useResultTabNav（FD.4 跳转契约）
// ---------------------------------------------------------------------------

describe('FT.1 · useResultTabNav 跳转契约', () => {
  function runTabNav(nav?: ResultTabNavigator): ResultTabNavigator | null {
    let exposed: ResultTabNavigator | null = null
    // provide 在父组件、inject 在子组件（inject 不会命中自身的 provide）。
    const Child = defineComponent({
      setup() {
        exposed = useResultTabNav()
        return () => h('div')
      },
    })
    const Parent = defineComponent({
      setup() {
        if (nav) provideResultTabNav(nav)
        return () => h(Child)
      },
    })
    mount(Parent, { global: { plugins: [pinia] } })
    return exposed
  }

  it('壳层 provide 后返回可用的跳转函数', () => {
    const spy = vi.fn<ResultTabNavigator>()
    const nav = runTabNav(spy)
    expect(nav).toBe(spy)
    nav?.('score')
    expect(spy).toHaveBeenCalledWith('score')
  })

  it('无 provide（独立单测场景）时返回 null', () => {
    expect(runTabNav()).toBeNull()
  })
})
