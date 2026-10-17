// 结果渲染件单测（原 FE.1/FE.4 中仍可复用的部分）。
// 固定结果标签已改为动态产物标签（§3.12），ScoreView / EvidenceView / ReportView /
// PredictView / FilePreviewView 已随固定标签一并移除；此处保留**仍被复用**的
// blocks 规范化与 Markdown 解析单测（ArtifactView 的 md 渲染仍依赖它们）。

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import {
  normalizeDataVolume,
  normalizeLimitations,
  normalizeQuarantineSamples,
  normalizeReport,
  normalizeScoreComparison,
} from '@/components/results/blocks'
import { parseMarkdown } from '@/components/results/markdown'
import ResultsPanel from '@/components/results/ResultsPanel.vue'
import { useTasksStore } from '@/stores/tasks'
import type { ResultPayload, TaskView } from '@/api/types'

describe('FE.1 · blocks 规范化', () => {
  it('score_comparison：缺 delta 时由 before/after 现算', () => {
    const c = normalizeScoreComparison({
      before: { Accurate: 90, composite: 90 },
      after: { Accurate: 99, composite: 95 },
    })
    expect(c).not.toBeNull()
    expect(c?.delta.Accurate).toBe(9)
    expect(c?.delta.composite).toBe(5)
    expect(c?.rows.map((r) => r.key)).toEqual(['Accurate', 'composite'])
  })

  it('data_volume：by_rule 求和兜底 total，非数字项丢弃', () => {
    const v = normalizeDataVolume({
      input: { ratings_lines: 10, note: 'x' },
      quarantine: { by_rule: { R2: 3, P2: 2 } },
    })
    expect(v?.input).toEqual([
      { key: 'ratings_lines', label: '评分行数', value: 10 },
    ])
    expect(v?.quarantineTotal).toBe(5)
  })

  it('形状不符时返回 null / 空数组（不造数字）', () => {
    expect(normalizeScoreComparison('nope')).toBeNull()
    expect(normalizeDataVolume(null)).toBeNull()
    expect(normalizeQuarantineSamples({})).toEqual([])
    expect(normalizeLimitations(['隔离 ≠ 修复', 1, { text: '时效性局限' }])).toEqual([
      '隔离 ≠ 修复',
      '时效性局限',
    ])
    expect(normalizeReport({ markdown: '# 标题' })).toBe('# 标题')
  })
})

describe('结果外壳 · 任务徽标状态标签统一入口', () => {
  function payload(over: Partial<TaskView>): ResultPayload {
    const task: TaskView = {
      agent_task_id: 'a1',
      hadoop_task_id: null,
      name: '',
      summary: '',
      status: 'aborted',
      status_cn: '已中止',
      stage: '',
      progress_percent: 60,
      estimated_seconds: null,
      updated_at: 0,
      ...over,
    }
    return { ok: true, task, blocks: {} }
  }

  it('status_cn 缺失时经 statusLabel 兜底显示「已中止」', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useTasksStore().applyResultPayload(payload({ status_cn: '' }))
    const wrapper = mount(ResultsPanel, {
      props: { title: '结果', testid: 'results-report' },
      global: { plugins: [pinia] },
    })
    expect(wrapper.find('[data-testid="results-task"]').text()).toContain('已中止')
  })
})

describe('markdown 解析（ArtifactView 的 md 渲染依赖）', () => {
  it('解析标题 / 列表 / 表格 / 代码块', () => {
    const blocks = parseMarkdown(
      ['## 小标题', '', '```json', '{}', '```', '', '1. 一', '2. 二'].join('\n'),
    )
    expect(blocks[0]).toMatchObject({ type: 'heading', level: 2, text: '小标题' })
    expect(blocks.some((b) => b.type === 'code')).toBe(true)
    const list = blocks.find((b) => b.type === 'list')
    expect(list).toMatchObject({ type: 'list', ordered: true })
  })
})
