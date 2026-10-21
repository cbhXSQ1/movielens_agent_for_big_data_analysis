// FE-5 · 结果详情展示四类版本与两个时间边界。
// 直接展示结果板块里的 `versions` / `time_boundaries` **后端原值**，不推断、不换算；
// 板块缺席时不显示该区。任务详情经 tasks.getResult(agent_task_id) 取本次任务结果，
// 因此这里直接向 tasks store 塞入结果载荷（不 mock endpoints，组件→store 全链真实执行）。
import { describe, it, expect } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import TaskDetail from '@/components/progress/TaskDetail.vue'
import { useTasksStore } from '@/stores/tasks'
import type { ResultPayload, TaskView } from '@/api/types'

function task(): TaskView {
  return {
    agent_task_id: 'a1',
    hadoop_task_id: 'H-1',
    name: '数据清洗',
    summary: '',
    status: 'succeeded',
    status_cn: '成功',
    stage: '成功',
    progress_percent: 100,
    estimated_seconds: null,
    updated_at: 0,
  }
}

/** 置好 pinia 并把结果载荷塞入 tasks store（detail 经 getResult 读取）。 */
function prime(blocks: ResultPayload['blocks']): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  const t = task()
  useTasksStore().applyResultPayload({ ok: true, task: t, blocks })
  return pinia
}

function mountDetail(pinia: Pinia): VueWrapper {
  return mount(TaskDetail, {
    props: { task: task(), conversationId: 'c1' },
    global: { plugins: [pinia] },
  }) as VueWrapper
}

describe('FE-5 · 结果详情：版本与时间边界展示', () => {
  it('有版本与时间边界时逐项显示后端原值', () => {
    const pinia = prime({
      report: '# 报告',
      versions: {
        data: 'ml1m-clean-v1',
        rule: '1.0.0',
        scoring: '1.0.0',
        policy: 'policy-v1',
      },
      time_boundaries: {
        T1: '2001-12-31T23:59:59Z',
        T2: '2002-06-30T23:59:59Z',
      },
    })
    const wrapper = mountDetail(pinia)

    expect(wrapper.find('[data-testid="task-result-versions"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('版本')
    expect(wrapper.find('[data-testid="task-version-data"]').text()).toContain(
      'ml1m-clean-v1',
    )
    expect(wrapper.find('[data-testid="task-version-rule"]').text()).toContain(
      '1.0.0',
    )
    expect(wrapper.find('[data-testid="task-version-scoring"]').text()).toContain(
      '1.0.0',
    )
    expect(wrapper.find('[data-testid="task-version-policy"]').text()).toContain(
      'policy-v1',
    )

    expect(wrapper.text()).toContain('时间边界（训练期/验证期）')
    expect(
      wrapper.find('[data-testid="task-time-boundary-T1"]').text(),
    ).toContain('2001-12-31T23:59:59Z')
    expect(
      wrapper.find('[data-testid="task-time-boundary-T2"]').text(),
    ).toContain('2002-06-30T23:59:59Z')
  })

  it('只给部分版本时，只显示后端给的那些键', () => {
    const pinia = prime({
      versions: { data: 'ml1m-clean-v1', rule: '1.0.0' },
    })
    const wrapper = mountDetail(pinia)

    expect(wrapper.find('[data-testid="task-version-data"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="task-version-rule"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="task-version-scoring"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="task-version-policy"]').exists()).toBe(false)
  })

  it('板块缺席时不显示版本/时间边界区', () => {
    const pinia = prime({ report: '# 报告' })
    const wrapper = mountDetail(pinia)

    expect(wrapper.find('[data-testid="task-result-versions"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('时间边界')
  })
})
