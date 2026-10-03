/* 对比条几何（纯函数）。
   两种形态：单条按刻度换算；增量段 = 灰底段 + 紧跟其后的绿色增量段（不叠放，所以不会互相遮挡）。 */
export function singleBarPct(value, scale) {
  const span = scale.max - scale.min || 1;
  return Math.max(0, Math.min(100, ((value - scale.min) / span) * 100));
}

export function deltaSegment(before, after, scale) {
  const basePct = singleBarPct(before, scale);
  const afterPct = singleBarPct(after, scale);
  return { basePct, widthPct: Math.max(0, afterPct - basePct) };
}
