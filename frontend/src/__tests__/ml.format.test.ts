import { describe, it, expect } from 'vitest'
import { errorCodeText } from '@/api/http'
import { formatPercent } from '@/utils/format'

describe('FE-ML-3 · 错误码 MODEL_NOT_FOUND', () => {
  it('给自然中文，并指向「模型库」核对编号', () => {
    const text = errorCodeText('MODEL_NOT_FOUND')
    expect(text).toContain('模型编号不存在')
    expect(text).toContain('模型库')
  })

  it('其它 ML 相关错误码保持可读', () => {
    expect(errorCodeText('DEPENDENCY_MISSING')).toContain('依赖')
    expect(errorCodeText('TASK_ALREADY_RUNNING')).toContain('运行')
    expect(errorCodeText('TASK_FAILED')).toContain('失败')
    expect(errorCodeText('WAIT_LIMIT_REACHED')).toContain('等待')
    expect(errorCodeText('USAGE')).toBeTruthy()
  })
})

describe('FE-ML-3 · 百分比换算（0–1 → ×100，两位）', () => {
  it('把 0–1 小数换算成百分比文本', () => {
    expect(formatPercent(0.7846)).toBe('78.46%')
    expect(formatPercent(0.5)).toBe('50.00%')
    expect(formatPercent(1)).toBe('100.00%')
    expect(formatPercent(0)).toBe('0.00%')
    expect(formatPercent('0.7012')).toBe('70.12%')
  })

  it('支持自定义小数位', () => {
    expect(formatPercent(0.78456, 1)).toBe('78.5%')
    expect(formatPercent(0.78456, 3)).toBe('78.456%')
  })

  it('缺失 / 非数给空串，不造数字', () => {
    expect(formatPercent(null)).toBe('')
    expect(formatPercent(undefined)).toBe('')
    expect(formatPercent('')).toBe('')
    expect(formatPercent('nope')).toBe('')
    expect(formatPercent(Number.NaN)).toBe('')
    expect(formatPercent(Number.POSITIVE_INFINITY)).toBe('')
  })
})
