import { test } from 'node:test';
import assert from 'node:assert/strict';
import { judgeRun, runBadges, runBanners } from '../js/core/run-state.js';

test('published_dir 为 null → 本地引擎（这是权威判据，不用 exec_mode 猜）', () => {
  assert.equal(judgeRun({ opts: {}, publishedDir: null }).mode, 'local');
});

test('published_dir 是路径 → Hadoop 集群', () => {
  assert.equal(judgeRun({ opts: {}, publishedDir: '/data/published/ml1m-clean-v1' }).mode, 'cluster');
});

test('还没取到结果（undefined）→ 执行环境未知', () => {
  assert.equal(judgeRun({ opts: {} }).mode, 'unknown');
  assert.equal(judgeRun({}).mode, 'unknown');
});

test('抽样只看 opts.scope', () => {
  assert.equal(judgeRun({ opts: { scope: 'full' } }).scope, 'full');
  assert.equal(judgeRun({ opts: { scope: 'sample' } }).scope, 'sample');
});

test('opts 丢了 → 口径未知，不猜', () => {
  assert.equal(judgeRun({}).scope, 'unknown');
  assert.equal(judgeRun({ opts: {} }).scope, 'unknown');
  assert.equal(judgeRun({ opts: { scope: 'weird' } }).scope, 'unknown');
});

test('绝不使用硬编码全量基准', () => {
  // 传入一个像全量的行数也不改变判定 —— 判定不看行数
  assert.equal(judgeRun({ opts: {}, counts: { input: { ratings_lines: 1150241 } } }).scope, 'unknown');
});

test('徽标：口径未知时不显示绿色全量徽标', () => {
  const badges = runBadges(judgeRun({}));
  assert.equal(badges.some(b => b.kind === 'ok'), false);
});

test('徽标：集群 + 全量 → 一条 ok 徽标', () => {
  const badges = runBadges(judgeRun({ opts: { scope: 'full' }, publishedDir: '/x' }));
  assert.deepEqual(badges.map(b => b.text), ['Hadoop 集群', '全量']);
});

test('横幅：本地引擎说事实，不说用途', () => {
  const bs = runBanners({ judged: judgeRun({ opts: { scope: 'full' }, publishedDir: null }), healthOk: true });
  const local = bs.find(b => b.key === 'local');
  assert.ok(local);
  assert.match(local.text, /没有发布到 HDFS/);
  assert.doesNotMatch(local.text, /汇报|演示|正式口径/);
});

test('横幅：非全量或口径未知时不出现绿色全量横幅', () => {
  const a = runBanners({ judged: judgeRun({ opts: { scope: 'sample' }, publishedDir: '/x' }), healthOk: true });
  assert.equal(a.some(b => b.kind === 'ok'), false);
  const b = runBanners({ judged: judgeRun({}), healthOk: true });
  assert.equal(b.some(x => x.kind === 'ok'), false);
  assert.ok(b.some(x => x.key === 'scope-unknown'));
});

test('横幅：后端离线时给出可执行的下一步', () => {
  const bs = runBanners({ judged: judgeRun({}), healthOk: false });
  const off = bs.find(b => b.key === 'offline');
  assert.ok(off);
  assert.match(off.text, /8765/);
});
