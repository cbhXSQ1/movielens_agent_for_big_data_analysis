import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sortRows, filterByRule } from '../js/ui/table.js';

const ROWS = [
  { line_no: 11, rule_id: 'R2', raw_line: 'a' },
  { line_no: 3,  rule_id: 'P3', raw_line: 'b' },
  { line_no: 27, rule_id: 'R2', raw_line: 'c' },
];

test('sortRows 升序', () => {
  assert.deepEqual(sortRows(ROWS, 'line_no', 'asc').map(r => r.line_no), [3, 11, 27]);
});

test('sortRows 降序', () => {
  assert.deepEqual(sortRows(ROWS, 'line_no', 'desc').map(r => r.line_no), [27, 11, 3]);
});

test('sortRows 不改原数组', () => {
  const before = ROWS.map(r => r.line_no);
  sortRows(ROWS, 'line_no', 'desc');
  assert.deepEqual(ROWS.map(r => r.line_no), before);
});

test('sortRows 对缺失值不崩，排在末尾', () => {
  const rows = [{ n: 2 }, { n: null }, { n: 1 }];
  assert.deepEqual(sortRows(rows, 'n', 'asc').map(r => r.n), [1, 2, null]);
});

test('filterByRule 命中', () => {
  assert.deepEqual(filterByRule(ROWS, 'R2').map(r => r.line_no), [11, 27]);
});

test('filterByRule 传空则原样返回', () => {
  assert.equal(filterByRule(ROWS, '').length, 3);
  assert.equal(filterByRule(ROWS, null).length, 3);
});
