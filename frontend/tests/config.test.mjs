import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createConfig } from '../js/core/config.js';

const SCORING = { dimensions: [{ id: 'A', name_zh: '准确性', metrics: [{ id: 'A1', name: '非空率' }] }], composite: { weights: {} } };
const RULES = { rules: [{ id: 'R2', name: '评分值域' }], pipeline: [{ stage: 'parse', rules: ['R2'] }] };

function fakeFetch(map) {
  return async (url) => {
    const body = map[url];
    if (!body) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => body };
  };
}

test('loadScoring / loadRules 各自返回数据', async () => {
  const cfg = createConfig({ base: '../config/', fetchImpl: fakeFetch({
    '../config/scoring_scheme.v1.json': SCORING,
    '../config/cleaning_rules.v1.json': RULES,
  }) });
  assert.equal((await cfg.loadScoring()).ok, true);
  assert.equal((await cfg.loadRules()).ok, true);
});

test('第二次调用走缓存，不再发请求', async () => {
  let calls = 0;
  const cfg = createConfig({ base: '../config/', fetchImpl: async (url) => {
    calls++;
    return { ok: true, status: 200, json: async () => (url.includes('scoring') ? SCORING : RULES) };
  } });
  await cfg.loadScoring();
  await cfg.loadScoring();
  assert.equal(calls, 1);
});

test('取不到时返回失败态而不是抛错或返回空对象', async () => {
  const cfg = createConfig({ base: '../config/', fetchImpl: fakeFetch({}) });
  const r = await cfg.loadScoring();
  assert.equal(r.ok, false);
  assert.ok(r.error.message.length > 0);
});

test('失败不进缓存：第二次调用会重新发请求，重试才有意义', async () => {
  let calls = 0;
  const cfg = createConfig({ base: '../config/', fetchImpl: async () => {
    calls++;
    return { ok: false, status: 404, json: async () => ({}) };
  } });
  const first = await cfg.loadScoring();
  const second = await cfg.loadScoring();
  assert.equal(first.ok, false);
  assert.equal(second.ok, false);
  assert.equal(calls, 2);
});
