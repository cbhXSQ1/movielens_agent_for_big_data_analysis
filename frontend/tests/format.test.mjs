import { test } from 'node:test';
import assert from 'node:assert/strict';
import { int, fixed, duration, stageZh, dimZh, pctPart } from '../js/core/format.js';

test('int 千分位', () => {
  assert.equal(int(1150241), '1,150,241');
  assert.equal(int(0), '0');
});

test('int 对缺失值返回短横，绝不返回 0', () => {
  assert.equal(int(null), '—');
  assert.equal(int(undefined), '—');
  assert.equal(int(NaN), '—');
  assert.equal(int(''), '—');
});

test('fixed 保留两位', () => {
  assert.equal(fixed(99.795, 2), '99.80');
  assert.equal(fixed(100, 2), '100.00');
});

test('duration 秒 → 分:秒', () => {
  assert.equal(duration(0), '0:00');
  assert.equal(duration(521), '8:41');
  assert.equal(duration(3661), '61:01');
  assert.equal(duration(null), '—');
});

test('stageZh 覆盖 9 个工作阶段', () => {
  assert.equal(stageZh('clean_users'), '用户表清洗');
  assert.equal(stageZh('done'), '完成');
  assert.equal(stageZh('queued'), '排队中');
  assert.equal(stageZh('unknown_stage'), 'unknown_stage');  // 不认识就原样显示，不猜
});

test('dimZh 覆盖五维，未知原样返回', () => {
  assert.equal(dimZh('Accurate'), '准确性');
  assert.equal(dimZh('Up-to-date'), '时效性');
  assert.equal(dimZh('Whatever'), 'Whatever');
});

test('pctPart 算变化百分比', () => {
  assert.equal(pctPart(1150241, 1000209), '13.0');
  assert.equal(pctPart(null, 1), '—');
  assert.equal(pctPart(0, 1), '—');
});
