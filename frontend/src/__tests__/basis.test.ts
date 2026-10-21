// 阶段 I · FI.2（抽为可复用渲染件）：方案渲染组件 SchemeView 单测。
// 说明：原「依据」固定标签已移除，方案渲染抽成 `components/basis/SchemeView.vue`，
// 由动态产物标签（ArtifactView，source=scheme）复用。此处只验证渲染：维度/指标/权重/
// 适用范围/局限/无法验证项、清洗规则清单与流水线、缺失字段如实标注。
// 内容直接以 prop 传入（不再自行拉取方案列表）。

import { describe, it, expect, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SchemeView from '@/components/basis/SchemeView.vue'

const scoringContent = {
  config_type: 'scoring_scheme',
  scheme_id: 'ml1m-quality-default',
  version: '1.0.0',
  status: 'registered_default',
  created: '2026-09-23',
  description: 'MovieLens 1M 五维质量评分默认方案。',
  engine_compat: {
    operator_library: 'ml1m-ops.v1',
    scoring_engine: 'scoring-engine.v1',
    notes: '指标公式只能使用已登记的算子。',
  },
  score_scale: { min: 0, max: 100, rounding: 2 },
  comparability: {
    before_after_same_formulas: true,
    valid_scope: '同一 (rule_version, scoring_scheme_version) 内',
    cross_version_comparison: false,
    quarantine_effect_note: '隔离会使比率类指标机械上升，禁止将隔离表述为修复。',
  },
  dimensions: [
    {
      id: 'Accurate',
      name_zh: '准确性',
      definition: '数据的值应当真实反映现实世界的情况。',
      weight: 0.25,
      metrics: [
        {
          id: 'A1',
          name: '评分值域合法率',
          enabled: true,
          measure: 'ratio',
          weight: 0.25,
          description: '1–5 整数评分占全部可解析评分记录的比例。',
          limitations: '格式合法不代表评分真实。',
          unverifiable: ['评分是否真实反映用户偏好'],
        },
        {
          id: 'A2',
          name: '时间戳可解析率',
          enabled: true,
          measure: 'ratio',
          weight: 0.2,
          description: '时间戳可解析为整数的比例。',
          limitations: '',
          unverifiable: [],
        },
      ],
    },
  ],
  composite: {
    enabled: true,
    weights: { Accurate: 0.25, Complete: 0.25, Unique: 0.2 },
    description: '五维加权综合分。',
    edit_rules: '修改须登记新版本。',
  },
}

const cleaningContent = {
  config_type: 'cleaning_rules',
  scheme_id: 'ml1m-cleaning-default',
  version: '1.0.0',
  status: 'registered_default',
  description: 'MovieLens 1M 默认清洗方案。',
  engine_compat: {
    operator_library: 'ml1m-ops.v1',
    action_engine: 'cleaning-engine.v1',
    notes: '',
  },
  pipeline: [{ stage: 'parse', rules: ['P1', 'P2'] }],
  processing_conventions: [{ id: 'C-ENC', description: '按 ISO-8859-1 读取' }],
  rules: [
    {
      id: 'P1',
      name: '文本编码修复',
      stage: 'parse',
      table: 'all',
      enabled: true,
      severity: 'error',
      action: 'fix',
      policy: { ref: 'text_repair', value: 'fix_in_place', options: ['fix_in_place'] },
      description: '检测双重编码乱码与 HTML 实体。',
    },
    {
      id: 'R2',
      name: '评分值域校验',
      stage: 'validate_repair',
      table: 'ratings',
      enabled: true,
      severity: 'error',
      action: 'quarantine',
      policy: { ref: 'invalid_rating', value: 'quarantine_row' },
      description: '评分必须为 1–5 的整数。',
    },
  ],
}

function mountScheme(content: unknown): VueWrapper {
  return mount(SchemeView, { props: { content } }) as VueWrapper
}

afterEach(() => {
  // 无需清理，保持与其他测试一致的风格
})

describe('FI.2 · 评分方案渲染', () => {
  it('渲染维度/指标/权重/适用范围/局限/无法验证项', () => {
    const wrapper = mountScheme(scoringContent)

    const dim = wrapper.find('[data-testid="basis-dimension-Accurate"]')
    expect(dim.exists()).toBe(true)
    expect(dim.text()).toContain('准确性')
    expect(dim.text()).toContain('0.25')

    const metric = wrapper.find('[data-testid="basis-metric-A1"]')
    expect(metric.exists()).toBe(true)
    expect(metric.text()).toContain('评分值域合法率')
    expect(
      wrapper.find('[data-testid="basis-metric-A1-limitation"]').text(),
    ).toContain('格式合法不代表评分真实')
    expect(
      wrapper.find('[data-testid="basis-metric-A1-unverifiable"]').text(),
    ).toContain('评分是否真实反映用户偏好')

    const scope = wrapper.find('[data-testid="basis-scope"]')
    expect(scope.exists()).toBe(true)
    expect(scope.text()).toContain('同一 (rule_version, scoring_scheme_version) 内')
    expect(wrapper.find('[data-testid="basis-quarantine-note"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="basis-composite"]').exists()).toBe(true)
  })

  it('缺失字段如实标注：无局限显示占位、无无法验证项显示「无」', () => {
    const wrapper = mountScheme(scoringContent)
    expect(
      wrapper.find('[data-testid="basis-metric-A2-limitation"]').text(),
    ).toBe('—')
    expect(
      wrapper.find('[data-testid="basis-metric-A2-unverifiable"]').text(),
    ).toBe('无')
  })
})

describe('FI.2 · 清洗规则方案渲染', () => {
  it('渲染规则清单与流水线、处理约定', () => {
    const wrapper = mountScheme(cleaningContent)
    expect(wrapper.find('[data-testid="basis-rules"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="basis-pipeline"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="basis-conventions"]').text()).toContain(
      'ISO-8859-1',
    )

    const rule = wrapper.find('[data-testid="basis-rule-R2"]')
    expect(rule.exists()).toBe(true)
    expect(rule.text()).toContain('评分值域校验')
    expect(rule.text()).toContain('quarantine_row')
  })
})

describe('FI.2 · 非对象内容如实回落', () => {
  it('非对象内容渲染原始 JSON，不造内容', () => {
    const wrapper = mountScheme('not-an-object')
    expect(wrapper.find('[data-testid="basis-raw"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="basis-raw"]').text()).toContain(
      'not-an-object',
    )
  })
})
