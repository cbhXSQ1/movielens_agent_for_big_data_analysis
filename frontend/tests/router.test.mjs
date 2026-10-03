import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseRoute, resolveRoute, routeHref } from '../js/shell/router.js';

const VIEWS = [{ id: 'overview', order: 10 }, { id: 'scores', order: 20 }];

test('空 hash 落到 overview', () => {
  assert.deepEqual(parseRoute(''), { id: 'overview', params: {} });
  assert.deepEqual(parseRoute('#/'), { id: 'overview', params: {} });
});

test('解析视图 id', () => {
  assert.deepEqual(parseRoute('#/scores'), { id: 'scores', params: {} });
});

test('解析查询参数', () => {
  assert.deepEqual(parseRoute('#/cleaning?rule=R2&table=ratings'),
                   { id: 'cleaning', params: { rule: 'R2', table: 'ratings' } });
});

test('resolveRoute 命中已注册视图', () => {
  assert.equal(resolveRoute({ id: 'scores' }, VIEWS).id, 'scores');
});

test('resolveRoute 对未知路由回落 overview', () => {
  assert.equal(resolveRoute({ id: 'nope' }, VIEWS).id, 'overview');
});

test('routeHref 生成可分享的地址', () => {
  assert.equal(routeHref('scores'), '#/scores');
  assert.equal(routeHref('cleaning', { rule: 'R2' }), '#/cleaning?rule=R2');
});
