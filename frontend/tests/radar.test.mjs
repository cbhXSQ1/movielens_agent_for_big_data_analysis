import { test } from 'node:test';
import assert from 'node:assert/strict';
import { radarScale, polarPoint, radarSvg, GEOM } from '../js/ui/radar.js';

const BEFORE = [97.71, 98.82, 90.90, 98.22, 89.65];
const AFTER  = [99.79, 99.99, 100.0, 100.0, 100.0];

test('全部 >= 85 时用 85–100 档，四环', () => {
  const s = radarScale([...BEFORE, ...AFTER]);
  assert.equal(s.min, 85);
  assert.equal(s.max, 100);
  assert.deepEqual(s.rings, [85, 90, 95, 100]);
});

test('有值 < 85 时回落 0–100 档，五环', () => {
  const s = radarScale([80, 99.9, 100, 100, 100]);
  assert.equal(s.min, 0);
  assert.equal(s.max, 100);
  assert.deepEqual(s.rings, [0, 25, 50, 75, 100]);
});

test('空数组不崩', () => {
  const s = radarScale([]);
  assert.equal(s.min, 0);
  assert.equal(s.max, 100);
});

test('满值落在满半径上，中心值落在圆心', () => {
  const s = radarScale([...BEFORE, ...AFTER]);
  const top = polarPoint(-90, 100, s, GEOM);
  assert.ok(Math.abs(top[0] - GEOM.cx) < 0.01);
  assert.ok(Math.abs(top[1] - (GEOM.cy - GEOM.r)) < 0.01);
  const mid = polarPoint(-90, 85, s, GEOM);
  assert.ok(Math.abs(mid[0] - GEOM.cx) < 0.01 && Math.abs(mid[1] - GEOM.cy) < 0.01);
});

test('半径随值单调递增', () => {
  const s = radarScale([0, 100]);
  const r = v => Math.hypot(polarPoint(0, v, s, GEOM)[0] - GEOM.cx, polarPoint(0, v, s, GEOM)[1] - GEOM.cy);
  assert.ok(r(100) > r(75) && r(75) > r(50) && r(50) > r(0));
});

const AXES = [
  { key: 'Accurate', zh: '准确性', angle: -90 },
  { key: 'Complete', zh: '完整性', angle: -18 },
  { key: 'Unique',   zh: '唯一性', angle: 54 },
  { key: 'Up-to-date', zh: '时效性', angle: 126 },
  { key: 'Consistent', zh: '一致性', angle: 198 },
];

test('radarSvg 输出合法 SVG，含 4 环 + 2 数据面 + 10 个顶点', () => {
  const s = radarScale([...BEFORE, ...AFTER]);
  const svg = radarSvg({ axes: AXES, before: BEFORE, after: AFTER, scale: s });
  assert.match(svg, /^<svg /);
  assert.equal((svg.match(/<polygon/g) || []).length, 6);
  assert.equal((svg.match(/<circle/g) || []).length, 10);
});

test('radarSvg 不带任何裸颜色，全走 CSS 变量', () => {
  const s = radarScale([...BEFORE, ...AFTER]);
  const svg = radarSvg({ axes: AXES, before: BEFORE, after: AFTER, scale: s });
  assert.equal(/#[0-9a-fA-F]{3,6}/.test(svg), false);
  assert.equal(/rgb\(/.test(svg), false);
});

test('radarSvg 带 role=img / aria-label / title / desc', () => {
  const s = radarScale([...BEFORE, ...AFTER]);
  const svg = radarSvg({ axes: AXES, before: BEFORE, after: AFTER, scale: s });
  assert.match(svg, /role="img"/);
  assert.match(svg, /aria-label="/);
  assert.match(svg, /<title>/);
  assert.match(svg, /<desc>/);
});

test('清洗后半径在 5 个维度上都 >= 清洗前', () => {
  const s = radarScale([...BEFORE, ...AFTER]);
  for (let i = 0; i < 5; i++) {
    const b = polarPoint(AXES[i].angle, BEFORE[i], s, GEOM);
    const a = polarPoint(AXES[i].angle, AFTER[i], s, GEOM);
    const rb = Math.hypot(b[0] - GEOM.cx, b[1] - GEOM.cy);
    const ra = Math.hypot(a[0] - GEOM.cx, a[1] - GEOM.cy);
    assert.ok(ra >= rb - 0.01, `轴 ${i} 半径应不减小`);
  }
});
