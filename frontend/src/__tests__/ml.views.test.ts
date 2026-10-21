// FE-ML-1 · 三类机器学习结果视图与百分比换算。
// 固定三份样例指标（契约 §3.2–§3.4），断言组件如实渲染字段与数值；
// 组件一律接收 blocks 里的**原始值**（unknown），内部防御式规范化。
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ClassificationCard from '@/components/results/ml/ClassificationCard.vue'
import ClusteringPanel from '@/components/results/ml/ClusteringPanel.vue'
import DimReductionPanel from '@/components/results/ml/DimReductionPanel.vue'
import { formatPercent } from '@/utils/format'

const classification = {
  f1: 0.7846,
  auc: 0.8231,
  pr_auc: 0.7012,
  precision: 0.75,
  recall: 0.8,
  threshold: 0.5,
  positive_rate: 0.42,
  confusion_matrix: { tp: 120, fp: 30, tn: 200, fn: 50 },
  feature_importance: [
    { feature: 'rating_count', importance: 0.42 },
    { feature: 'avg_rating', importance: 0.31 },
  ],
  feature_importance_note: '朴素贝叶斯：各特征对后验的对数概率差',
  split_sizes: { train: 800, valid: 100, test: 100 },
  leakage: { policy: '仅用预测时点之前的历史', verified_on: '时间切分核验' },
}

const clustering = {
  k: 4,
  silhouette: 0.5123,
  davies_bouldin: 0.98,
  clusters: [
    {
      cluster_id: 0,
      size: 300,
      label: '高活跃好评群',
      top_genres: ['Drama', 'Comedy'],
      behavior: { activity: 120.5, avg_rating: 4.2, rating_std: 0.6, recency: 30 },
    },
    {
      cluster_id: 1,
      size: 210,
      label: '低活跃长尾群',
      top_genres: ['Horror'],
      behavior: { activity: 8.3, avg_rating: 2.9, rating_std: 1.1, recency: 400 },
    },
  ],
  k_selection: {
    method: 'silhouette+elbow',
    candidates: [
      { k: 2, silhouette: 0.41 },
      { k: 3, silhouette: 0.47 },
      { k: 4, silhouette: 0.51 },
    ],
  },
}

const dimReduction = {
  n_components: 8,
  explained_variance_ratio: [0.2, 0.15, 0.1],
  reconstruction_error: 0.1234,
  resource: { fit_seconds: 12.5, mem_mb: 256 },
  comparison: {
    before: { silhouette: 0.45, davies_bouldin: 1.1, k: 4 },
    after: { silhouette: 0.55, davies_bouldin: 0.9, k: 4 },
    agreement: { ari: 0.7, nmi: 0.65 },
  },
}

describe('FE-ML-1 · 百分比换算', () => {
  it('0–1 小数乘 100 保留两位', () => {
    expect(formatPercent(0.7846)).toBe('78.46%')
    expect(formatPercent(0.5)).toBe('50.00%')
    expect(formatPercent('0.7012')).toBe('70.12%')
  })

  it('缺失值返回空字符串，不造数字', () => {
    expect(formatPercent(null)).toBe('')
    expect(formatPercent(undefined)).toBe('')
    expect(formatPercent('nope')).toBe('')
  })
})

describe('FE-ML-1 · 分类结果卡', () => {
  it('F1/AUC/PR-AUC 以百分比显示，保留两位', () => {
    const wrapper = mount(ClassificationCard, { props: { metrics: classification } })
    expect(wrapper.find('[data-testid="ml-metric-f1"]').text()).toContain('78.46%')
    expect(wrapper.find('[data-testid="ml-metric-auc"]').text()).toContain('82.31%')
    expect(wrapper.find('[data-testid="ml-metric-pr_auc"]').text()).toContain('70.12%')
  })

  it('精确率/召回率以百分比显示', () => {
    const wrapper = mount(ClassificationCard, { props: { metrics: classification } })
    expect(wrapper.find('[data-testid="ml-metric-precision"]').text()).toContain('75.00%')
    expect(wrapper.find('[data-testid="ml-metric-recall"]').text()).toContain('80.00%')
  })

  it('混淆矩阵显示 tp/fp/tn/fn', () => {
    const wrapper = mount(ClassificationCard, { props: { metrics: classification } })
    const cm = wrapper.find('[data-testid="ml-confusion"]').text()
    expect(cm).toContain('120')
    expect(cm).toContain('30')
    expect(cm).toContain('200')
    expect(cm).toContain('50')
  })

  it('特征重要性列表与说明文字', () => {
    const wrapper = mount(ClassificationCard, { props: { metrics: classification } })
    expect(wrapper.find('[data-testid="ml-feature-rating_count"]').text()).toContain('rating_count')
    expect(wrapper.find('[data-testid="ml-feature-avg_rating"]').text()).toContain('avg_rating')
    expect(
      wrapper.find('[data-testid="ml-feature-importance-note"]').text(),
    ).toContain('对数概率差')
  })

  it('划分规模与防泄漏小字', () => {
    const wrapper = mount(ClassificationCard, { props: { metrics: classification } })
    expect(wrapper.find('[data-testid="ml-split-sizes"]').text()).toContain('800')
    expect(wrapper.find('[data-testid="ml-leakage"]').text()).toContain('仅用预测时点之前的历史')
  })

  it('无特征重要性说明时不渲染说明节点', () => {
    const wrapper = mount(ClassificationCard, {
      props: { metrics: { ...classification, feature_importance_note: undefined } },
    })
    expect(wrapper.find('[data-testid="ml-feature-importance-note"]').exists()).toBe(false)
  })
})

describe('FE-ML-1 · 聚类面板', () => {
  it('逐群显示 cluster_id/size/label/top_genres/behavior', () => {
    const wrapper = mount(ClusteringPanel, { props: { metrics: clustering } })
    const row = wrapper.find('[data-testid="ml-cluster-0"]').text()
    expect(row).toContain('高活跃好评群')
    expect(row).toContain('300')
    expect(row).toContain('Drama')
    expect(row).toContain('Comedy')
    expect(row).toContain('4.20')
    expect(wrapper.find('[data-testid="ml-cluster-1"]').text()).toContain('低活跃长尾群')
  })

  it('有 k_selection 时显示候选 k/轮廓系数折线', () => {
    const wrapper = mount(ClusteringPanel, { props: { metrics: clustering } })
    expect(wrapper.find('[data-testid="ml-k-selection"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="ml-k-selection-candidate-4"]').text(),
    ).toContain('0.51')
  })

  it('无 k_selection 时不渲染折线', () => {
    const wrapper = mount(ClusteringPanel, {
      props: { metrics: { ...clustering, k_selection: undefined } },
    })
    expect(wrapper.find('[data-testid="ml-k-selection"]').exists()).toBe(false)
  })
})

describe('FE-ML-1 · 降维面板', () => {
  it('comparison 前后与一致性', () => {
    const wrapper = mount(DimReductionPanel, { props: { metrics: dimReduction } })
    const text = wrapper.find('[data-testid="ml-comparison"]').text()
    expect(text).toContain('0.45')
    expect(text).toContain('0.55')
    expect(text).toContain('0.70')
    expect(text).toContain('0.65')
  })

  it('解释方差比 / 重构误差 / 资源占用', () => {
    const wrapper = mount(DimReductionPanel, { props: { metrics: dimReduction } })
    expect(wrapper.find('[data-testid="ml-explained-variance"]').text()).toContain('0.20')
    expect(wrapper.find('[data-testid="ml-reconstruction-error"]').text()).toContain('0.1234')
    const res = wrapper.find('[data-testid="ml-resource"]').text()
    expect(res).toContain('12.5')
    expect(res).toContain('256')
  })

  it('NMF（解释方差比为 null）时如实说明不适用', () => {
    const wrapper = mount(DimReductionPanel, {
      props: { metrics: { ...dimReduction, explained_variance_ratio: null } },
    })
    expect(wrapper.find('[data-testid="ml-explained-variance"]').text()).toContain('不适用')
  })

  it('说明坐标来自 coords 产物', () => {
    const wrapper = mount(DimReductionPanel, { props: { metrics: dimReduction } })
    expect(wrapper.find('[data-testid="ml-coords-note"]').exists()).toBe(true)
  })
})
