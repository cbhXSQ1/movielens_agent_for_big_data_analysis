import { test } from 'node:test';
import assert from 'node:assert/strict';
import { summarizeSteps } from '../js/ui/steps.js';

const RAW = [
  { tool: 'start_cleaning_task', args: { exec_mode: 'cluster', scope: 'full' }, ok: true,
    summary: '{"ok":true,"task_id":"20260925-184303-379a4b"}', envelope: { huge: 'x'.repeat(5000) } },
  { tool: 'get_task_status', args: { task_id: '2026' }, ok: true, summary: '{"status":"running"}', envelope: {} },
];

test('步骤序号从 1 开始', () => {
  assert.deepEqual(summarizeSteps(RAW).map(s => s.index), [1, 2]);
});

test('参数转成可读文本，超长截断', () => {
  const s = summarizeSteps(RAW)[0];
  assert.match(s.argsText, /exec_mode/);
  assert.ok(s.argsText.length <= 120);
});

test('摘要去掉多余空白并截断到 200 字', () => {
  const s = summarizeSteps([{ tool: 'x', args: {}, ok: true, summary: 'a\n\n  b' }])[0];
  assert.equal(s.summary, 'a b');
});

test('ok=false 保留为失败态', () => {
  const s = summarizeSteps([{ tool: 'x', args: {}, ok: false, summary: '出错了' }])[0];
  assert.equal(s.ok, false);
});

test('绝不把完整 envelope 带进摘要（它可能很大）', () => {
  const s = summarizeSteps(RAW);
  const joined = JSON.stringify(s);
  assert.equal(joined.includes('xxxxx'), false);
});

test('空数组与 null 都不崩', () => {
  assert.deepEqual(summarizeSteps([]), []);
  assert.deepEqual(summarizeSteps(null), []);
  assert.deepEqual(summarizeSteps(undefined), []);
});
