// 迭代二（FE-ML-3）· 展示层共享的百分比格式化。
// 契约 §0：比率类指标一律 0–1 小数、保留四位；界面显示百分比时自行 ×100、默认保留两位。
// 缺失 / 非数 → 空字符串（由调用方决定占位符），绝不造数字。

/** 0–1 小数 → 百分比文本（×100，默认两位小数）。 */
export function formatPercent(
  value: number | string | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || value === '') return ''
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return ''
  return `${(n * 100).toFixed(digits)}%`
}
