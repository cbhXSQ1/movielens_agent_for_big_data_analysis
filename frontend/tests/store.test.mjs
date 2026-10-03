import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../js/core/store.js';

test('get 返回初始状态', () => {
  const s = createStore({ a: 1 });
  assert.equal(s.get().a, 1);
});

test('set 支持对象补丁，未提及的键保留', () => {
  const s = createStore({ a: 1, b: 2 });
  s.set({ b: 3 });
  assert.deepEqual(s.get(), { a: 1, b: 3 });
});

test('set 支持函数补丁，收到旧状态', () => {
  const s = createStore({ n: 1 });
  s.set(prev => ({ n: prev.n + 1 }));
  assert.equal(s.get().n, 2);
});

test('订阅者在 set 时收到新状态；取消订阅后不再收到', () => {
  const s = createStore({ n: 0 });
  const seen = [];
  const off = s.subscribe(st => seen.push(st.n));
  s.set({ n: 1 });
  off();
  s.set({ n: 2 });
  assert.deepEqual(seen, [1]);
});

test('状态是冻结的，直接改会抛错', () => {
  const s = createStore({ n: 1 });
  assert.throws(() => { 'use strict'; s.get().n = 9; }, TypeError);
});

test('set 成相同内容不触发订阅', () => {
  const s = createStore({ a: 1, b: 2 });
  let hits = 0;
  s.subscribe(() => hits++);
  s.set({ a: 1 });
  assert.equal(hits, 0);
});
