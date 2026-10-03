/* 格式化层：页面上每一个数字都必须经过这里。缺失一律 '—'，绝不返回 0。 */
export const DASH = '—';

const STAGE_ZH = {
  queued: '排队中',
  clean_users: '用户表清洗',
  clean_movies: '电影表清洗',
  clean_ratings: '评分表清洗',
  stats_marks: '统计打标',
  score_before: '清洗前评分',
  score_after: '清洗后评分',
  finalize: '汇总',
  publish: '发布',
  done: '完成',
};

const DIM_ZH = {
  Accurate: '准确性',
  Complete: '完整性',
  Unique: '唯一性',
  'Up-to-date': '时效性',
  Consistent: '一致性',
};

export function isNum(n) {
  return typeof n === 'number' && Number.isFinite(n);
}

export function int(n) {
  return isNum(n) ? String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',') : DASH;
}

export function fixed(n, d = 2) {
  return isNum(n) ? n.toFixed(d) : DASH;
}

export function pctPart(before, after) {
  if (!isNum(before) || !isNum(after) || before === 0) return DASH;
  return ((before - after) / before * 100).toFixed(1);
}

export function duration(sec) {
  if (!isNum(sec) || sec < 0) return DASH;
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function stageZh(stage) {
  return STAGE_ZH[stage] || String(stage == null ? DASH : stage);
}

export function dimZh(id) {
  return DIM_ZH[id] || String(id == null ? DASH : id);
}

/* 指标名从评分方案读，不硬编码（约束 G14） */
export function metricName(scoringCfg, id) {
  if (!scoringCfg || !Array.isArray(scoringCfg.dimensions)) return id;
  for (const dim of scoringCfg.dimensions) {
    for (const m of dim.metrics || []) if (m.id === id) return m.name || id;
  }
  return id;
}
