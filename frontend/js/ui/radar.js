/* 雷达几何（纯函数，可单测）。
   刻度自适应且永远显式标注：全部 >= 85 用 85–100（否则五个 90+ 的维度会完全重合，图上看不出差异）。 */
export const GEOM = { cx: 165, cy: 168, r: 104, labelGap: 8.5, viewBox: '0 0 340 340' };

const AUTO_HI_MIN = 85;

export function radarScale(values) {
  const nums = (values || []).filter(v => typeof v === 'number' && Number.isFinite(v));
  if (nums.length === 0) return { min: 0, max: 100, rings: [0, 25, 50, 75, 100] };
  const lo = Math.min(...nums);
  if (lo >= AUTO_HI_MIN) return { min: 85, max: 100, rings: [85, 90, 95, 100] };
  return { min: 0, max: 100, rings: [0, 25, 50, 75, 100] };
}

export function polarPoint(angleDeg, value, scale, geom = GEOM) {
  const span = scale.max - scale.min || 1;
  const t = Math.max(0, Math.min(1, (value - scale.min) / span));
  const rad = (angleDeg * Math.PI) / 180;
  return [geom.cx + t * geom.r * Math.cos(rad), geom.cy + t * geom.r * Math.sin(rad)];
}

const fx = (n) => n.toFixed(2);

function ringPoints(angleDegs, value, scale, geom) {
  return angleDegs.map(a => { const p = polarPoint(a, value, scale, geom); return `${fx(p[0])},${fx(p[1])}`; }).join(' ');
}

export function radarSvg({ axes, before, after, scale, geom = GEOM, ariaLabel, title = '五维质量雷达' }) {
  const angles = axes.map(a => a.angle);
  const out = [];
  const label = ariaLabel || `五维质量雷达，刻度 ${scale.min} 到 ${scale.max}。` +
    axes.map((a, i) => `${a.zh}：清洗前 ${before[i]}，清洗后 ${after[i]}`).join('；');

  out.push(`<svg viewBox="${geom.viewBox}" role="img" aria-label="${label.replace(/"/g, '')}">`);
  out.push(`<title>${title}</title>`);
  out.push(`<desc>${label.replace(/</g, '')}</desc>`);

  for (const ring of scale.rings) {
    out.push(`<polygon points="${ringPoints(angles, ring, scale, geom)}" fill="none" stroke="var(--viz-grid)" stroke-width="1"/>`);
  }

  axes.forEach((a, i) => {
    const end = polarPoint(a.angle, scale.max, scale, geom);
    out.push(`<line x1="${geom.cx}" y1="${geom.cy}" x2="${fx(end[0])}" y2="${fx(end[1])}" stroke="var(--viz-grid)" stroke-width="1"/>`);
    const lp = polarPoint(a.angle, scale.max, { min: scale.min, max: scale.max - geom.labelGap }, geom);
    const anchor = Math.abs(lp[0] - geom.cx) < 1 ? 'middle' : (lp[0] > geom.cx ? 'start' : 'end');
    out.push(`<text x="${fx(lp[0])}" y="${fx(lp[1] + 4)}" text-anchor="${anchor}" font-size="13" fill="var(--fg-2)">${a.zh}</text>`);
    out.push(`<text x="${fx(lp[0])}" y="${fx(lp[1] + 16)}" text-anchor="${anchor}" font-size="11" fill="var(--fg-3)" font-family="var(--font-mono)">${a.key}</text>`);
  });

  const beforePts = angles.map((ang, i) => { const p = polarPoint(ang, before[i], scale, geom); return `${fx(p[0])},${fx(p[1])}`; }).join(' ');
  const afterPts  = angles.map((ang, i) => { const p = polarPoint(ang, after[i], scale, geom); return `${fx(p[0])},${fx(p[1])}`; }).join(' ');
  out.push(`<polygon points="${beforePts}" fill="var(--viz-before)" fill-opacity=".22" stroke="var(--viz-before)" stroke-width="1.4"/>`);
  out.push(`<polygon points="${afterPts}" fill="var(--viz-after)" fill-opacity=".20" stroke="var(--viz-after)" stroke-width="1.8"/>`);

  angles.forEach((ang, i) => {
    const pb = polarPoint(ang, before[i], scale, geom);
    const pa = polarPoint(ang, after[i], scale, geom);
    out.push(`<circle cx="${fx(pb[0])}" cy="${fx(pb[1])}" r="2.6" fill="var(--viz-before)"/>`);
    out.push(`<circle cx="${fx(pa[0])}" cy="${fx(pa[1])}" r="3.2" fill="var(--viz-after)"/>`);
  });

  out.push('</svg>');
  return out.join('');
}

/* 五维轴定义：中文名 + 英文原名 + 角度。角度顺序与评分方案的维度顺序对应。 */
export function axesFor(dimKeys) {
  const ANGLES = [-90, -18, 54, 126, 198];
  const ZH = { Accurate: '准确性', Complete: '完整性', Unique: '唯一性', 'Up-to-date': '时效性', Consistent: '一致性' };
  return dimKeys.slice(0, 5).map((key, i) => ({ key, zh: ZH[key] || key, angle: ANGLES[i] }));
}
