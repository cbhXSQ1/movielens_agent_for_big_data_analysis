import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clampWidth, nextWidth } from '../js/shell/splitter.js';

test('clampWidth 夹在范围内', () => {
  assert.equal(clampWidth(100, 280, 720), 280);
  assert.equal(clampWidth(999, 280, 720), 720);
  assert.equal(clampWidth(400, 280, 720), 400);
});

test('方向键微调 16px', () => {
  assert.equal(nextWidth(400, 'ArrowRight', false, 280, 720), 416);
  assert.equal(nextWidth(400, 'ArrowLeft', false, 280, 720), 384);
});

test('Shift 大步长 48px', () => {
  assert.equal(nextWidth(400, 'ArrowRight', true, 280, 720), 448);
});

test('Home / End 到两端', () => {
  assert.equal(nextWidth(400, 'Home', false, 280, 720), 280);
  assert.equal(nextWidth(400, 'End', false, 280, 720), 720);
});

test('Enter 复位到默认 380', () => {
  assert.equal(nextWidth(500, 'Enter', false, 280, 720), 380);
});

test('不认识的键返回原值', () => {
  assert.equal(nextWidth(400, 'KeyA', false, 280, 720), 400);
});
