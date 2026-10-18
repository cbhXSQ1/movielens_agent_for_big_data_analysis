// FE-ML-1 · 结果视图注册表与宿主分发。
// 注册表按 task_type 映射到独立视图组件（多态）；宿主 MlResultView 读取
// 结果里的 task_type（或按存在的板块推断）渲染对应组件，未知类型给柔和兜底。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ClassificationCard from '@/components/results/ml/ClassificationCard.vue'
import ClusteringPanel from '@/components/results/ml/ClusteringPanel.vue'
import DimReductionPanel from '@/components/results/ml/DimReductionPanel.vue'
import MlResultView from '@/components/results/ml/MlResultView.vue'
import { mlViewFor, ML_VIEW_REGISTRY } from '@/components/results/ml/registry'
import type { ResultPayload, TaskView } from '@/api/types'

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

const sampleClassification = { f1: 0.78, auc: 0.82, pr_auc: 0.7 }
const sampleClustering = { k: 3, silhouette: 0.5, clusters: [] }
const sampleDim = { n_components: 5, reconstruction_error: 0.1 }

describe('FE-ML-1 · 注册表按 task_type 映射', () => {
  it('三类任务类型各映射到独立组件', () => {
    expect(ML_VIEW_REGISTRY.classification).toBe(ClassificationCard)
    expect(ML_VIEW_REGISTRY.clustering).toBe(ClusteringPanel)
    expect(ML_VIEW_REGISTRY.dimensionality_reduction).toBe(DimReductionPanel)
    expect(mlViewFor('classification')).toBe(ClassificationCard)
    expect(mlViewFor('clustering')).toBe(ClusteringPanel)
    expect(mlViewFor('dimensionality_reduction')).toBe(DimReductionPanel)
  })

  it('未知/空 task_type 返回 null', () => {
    expect(mlViewFor('regression')).toBeNull()
    expect(mlViewFor(null)).toBeNull()
    expect(mlViewFor(undefined)).toBeNull()
  })
})

describe('FE-ML-1 · 宿主 MlResultView 分发', () => {
  function mountHost(result: ResultPayload) {
    const pinia = createPinia()
    setActivePinia(pinia)
    return mount(MlResultView, {
      props: { result },
      global: { plugins: [pinia] },
    })
  }

  it('task_type=classification 渲染分类卡', () => {
    const wrapper = mountHost(
      payload({
        task_type: 'classification',
        classification: sampleClassification,
      }),
    )
    expect(wrapper.find('[data-testid="ml-classification-card"]').exists()).toBe(true)
  })

  it('task_type=clustering 渲染聚类面板', () => {
    const wrapper = mountHost(
      payload({ task_type: 'clustering', clustering: sampleClustering }),
    )
    expect(wrapper.find('[data-testid="ml-clustering-panel"]').exists()).toBe(true)
  })

  it('task_type=dimensionality_reduction 渲染降维面板', () => {
    const wrapper = mountHost(
      payload({
        task_type: 'dimensionality_reduction',
        dimensionality_reduction: sampleDim,
      }),
    )
    expect(wrapper.find('[data-testid="ml-dim-reduction-panel"]').exists()).toBe(true)
  })

  it('无 task_type 时按存在的板块推断', () => {
    const wrapper = mountHost(payload({ clustering: sampleClustering }))
    expect(wrapper.find('[data-testid="ml-clustering-panel"]').exists()).toBe(true)
  })

  it('无 task_type 时按 dimensionality_reduction 板块推断降维视图', () => {
    const wrapper = mountHost(payload({ dimensionality_reduction: sampleDim }))
    expect(wrapper.find('[data-testid="ml-dim-reduction-panel"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="ml-dim-reduction-panel"]').text(),
    ).toContain('0.10')
  })

  it('未知 task_type 给柔和兜底', () => {
    const wrapper = mountHost(payload({ task_type: 'regression' }))
    expect(wrapper.find('[data-testid="ml-result-view-fallback"]').exists()).toBe(true)
  })

  it('无任何机器学习板块时不渲染', () => {
    const wrapper = mountHost(payload({ report: '# 报告' }))
    expect(wrapper.find('[data-testid="ml-result-view"]').exists()).toBe(false)
  })
})
