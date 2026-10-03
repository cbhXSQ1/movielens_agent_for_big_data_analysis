import { test } from 'node:test';
import assert from 'node:assert/strict';
import { singleBarPct, deltaSegment } from '../js/ui/bars.js';

test('singleBarPct 按刻度换算', () => {
  assert.equal(singleBarPct(100, { min: 85, max: 100 }), 100);
  assert.equal(singleBarPct(85,  { min: 85, max: 100 }), 0);
  assert.equal(singleBarPct(92.5, { min: 85, max: 100 }), 50);
});

test('singleBarPct 越界被夹住', () => {
  assert.equal(singleBarPct(120, { min: 85, max: 100 }), 100);
  assert.equal(singleBarPct(10,  { min: 85, max: 100 }), 0);
});

test('deltaSegment：灰段在前、绿段紧随其后，不重叠', () => {
  const s = { min: 85, max: 100 };
  const seg = deltaSegment(89.65, 100, s);
  assert.ok(Math.abs(seg.basePct - 31.0) < 0.1);
  assert.ok(Math.abs(seg.widthPct - 69.0) < 0.1);
  assert.ok(seg.basePct + seg.widthPct <= 100.001);
});

test('deltaSegment：没变化时绿段宽度为 0', () => {
  const seg = deltaSegment(99, 99, { min: 85, max: 100 });
  assert.equal(seg.widthPct, 0);
});

test('deltaSegment：变差也不出现负宽度', () => {
  const seg = deltaSegment(100, 90, { min: 85, max: 100 });
  assert.equal(seg.widthPct, 0);
});
