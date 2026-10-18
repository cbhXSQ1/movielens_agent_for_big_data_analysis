// FE-ML-1 · 结果板块透出与任务详情接线。
// useResultBlocks 原样透出 classification / clustering / dimensionalityReduction；
// TaskDetail 在结果区渲染 MlResultView，且不影响既有的块标签与版本展示。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TaskDetail from '@/components/progress/TaskDetail.vue'
import { useResultBlocks } from '@/composables/useResultBlocks'
import { useTasksStore } from '@/stores/tasks'
import type { ResultPayload, TaskView } from '@/api/types'

const classification = { f1: 0.78, auc: 0.82, pr_auc: 0.7, confusion_matrix: { tp: 1 } }
const clustering = { k: 4, silhouette: 0.5, clusters: [] }
const dimensionalityReduction = { n_components: 8, reconstruction_error: 0.1 }

function task(): TaskView {
  return {
    agent_task_id: 'ml-1',
    hadoop_task_id: 'ml-1',
    name: '训练分类器',
    summary: '',
    status: 'succeeded',
    status_cn: '成功',
    stage: '成功',
    progress_percent: 100,
    estimated_seconds: null,
    updated_at: 0,
  }
}

function payload(blocks: ResultPayload['blocks']): ResultPayload {
  return { ok: true, task: task(), blocks }
}

describe('FE-ML-1 · useResultBlocks 透出原始 ML 板块', () => {
  it('原样返回非规范化的三块', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useTasksStore().applyResultPayload(
      payload({
        task_type: 'classification',
        classification,
        clustering,
        dimensionality_reduction: dimensionalityReduction,
      }),
    )
    const blocks = useResultBlocks()
    expect(blocks.classification.value).toEqual(classification)
    expect(blocks.clustering.value).toEqual(clustering)
    expect(blocks.dimensionalityReduction.value).toEqual(dimensionalityReduction)
  })

  it('板块缺席时为 null', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useTasksStore().applyResultPayload(payload({ report: '# 报告' }))
    const blocks = useResultBlocks()
    expect(blocks.classification.value).toBeNull()
    expect(blocks.clustering.value).toBeNull()
    expect(blocks.dimensionalityReduction.value).toBeNull()
  })
})

describe('FE-ML-1 · TaskDetail 集成 MlResultView', () => {
  function mountDetail(blocks: ResultPayload['blocks']) {
    const pinia = createPinia()
    setActivePinia(pinia)
    useTasksStore().applyResultPayload(payload(blocks))
    return mount(TaskDetail, {
      props: { task: task(), conversationId: 'c1' },
      global: { plugins: [pinia] },
    })
  }

  it('结果含分类板块时渲染分类视图，且保留块标签', () => {
    const wrapper = mountDetail({
      task_type: 'classification',
      classification,
      report: '# 报告',
    })
    expect(wrapper.find('[data-testid="task-result-blocks"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-result-view"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="ml-classification-card"]').exists()).toBe(true)
  })

  it('结果含降维板块时渲染降维视图', () => {
    const wrapper = mountDetail({
      task_type: 'dimensionality_reduction',
      dimensionality_reduction: dimensionalityReduction,
    })
    expect(wrapper.find('[data-testid="ml-dim-reduction-panel"]').exists()).toBe(true)
  })

  it('无机器学习板块时不渲染 ML 视图（不干扰既有渲染）', () => {
    const wrapper = mountDetail({ report: '# 报告' })
    expect(wrapper.find('[data-testid="ml-result-view"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="task-result-blocks"]').exists()).toBe(true)
  })
})
