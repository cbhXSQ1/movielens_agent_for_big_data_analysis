import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderReport, linkifyNumbers } from '../js/core/report.js';

test('标题渲染成 h3', () => {
  assert.match(renderReport('# 评估报告'), /<h3[^>]*>评估报告<\/h3>/);
});

test('列表渲染', () => {
  const html = renderReport('- 第一条\n- 第二条');
  assert.equal((html.match(/<li>/g) || []).length, 2);
});

test('粗体渲染', () => {
  assert.match(renderReport('这是**重点**内容'), /<strong>重点<\/strong>/);
});

test('表格渲染成 table 且表头带 scope', () => {
  const md = '| a | b |\n|---|---|\n| 1 | 2 |';
  const html = renderReport(md);
  assert.match(html, /<table>/);
  assert.match(html, /<th scope="col">a<\/th>/);
});

test('HTML 被转义，脚本注入不成立', () => {
  const html = renderReport('<script>alert(1)</script>');
  assert.equal(html.includes('<script>'), false);
  assert.match(html, /&lt;script&gt;/);
});

test('未知语法降级为 pre，不猜', () => {
  const html = renderReport('| 这不是表格\n| 第二行');
  assert.match(html, /<pre/);
});

test('D3：数字被包成可点链接', () => {
  const html = linkifyNumbers('综合质量分 99.95，隔离 100,830 行', [
    { re: /综合(?:质量分|分)?[^0-9]{0,6}(\d+\.\d+)/g, target: 'overview' },
    { re: /隔离[^0-9]{0,10}([\d,]{3,})/g, target: 'cleaning' },
  ]);
  assert.match(html, /data-goto="overview"/);
  assert.match(html, /data-goto="cleaning"/);
});

test('D3：维度名也被链到 #/scores', () => {
  const html = linkifyNumbers('准确性 97.71 → 99.79', [
    { re: new RegExp('准确性' + '[^0-9]{0,16}(\\d+\\.\\d+)', 'g'), target: 'scores' },
  ]);
  assert.match(html, /data-goto="scores"/);
});
