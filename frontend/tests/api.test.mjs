import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApi, errorMessage } from '../js/core/api.js';

function fakeFetch(handler) {
  return async (url, init) => {
    const r = await handler(url, init);
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      headers: { get: (k) => (k.toLowerCase() === 'content-type' ? (r.ctype || 'application/json') : null) },
      text: async () => (typeof r.body === 'string' ? r.body : JSON.stringify(r.body)),
      json: async () => (typeof r.body === 'string' ? JSON.parse(r.body) : r.body),
    };
  };
}

test('errorMessage 覆盖文档里的错误码', () => {
  assert.match(errorMessage('TASK_NOT_FINISHED'), /还没跑完/);
  assert.match(errorMessage('TASK_FAILED'), /失败/);
  assert.match(errorMessage('TASK_NOT_FOUND'), /找不到/);
  assert.match(errorMessage('TASK_ALREADY_RUNNING'), /已有任务/);
  assert.match(errorMessage('DRIVER_UNREACHABLE'), /后端/);
  assert.equal(errorMessage('SOMETHING_NEW', '兜底文案'), '兜底文案');
  assert.equal(errorMessage(undefined, '兜底文案'), '兜底文案');
});

test('chat 把 scope 传成 null（让后端按 task_id 快照补口径）', async () => {
  let sent = null;
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async (url, init) => { sent = JSON.parse(init.body); return { body: { ok: true } }; }),
  });
  await api.chat({ text: '你好', execMode: 'local' });
  assert.equal(sent.scope, null);
  assert.equal(sent.exec_mode, 'local');
  assert.equal(sent.text, '你好');
});

test('chat 不传 task_id 时该键不出现', async () => {
  let sent = null;
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async (url, init) => { sent = JSON.parse(init.body); return { body: { ok: true } }; }),
  });
  await api.chat({ text: 'hi' });
  assert.equal('task_id' in sent, false);
});

test('chat 追问时带上 task_id', async () => {
  let sent = null;
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async (url, init) => { sent = JSON.parse(init.body); return { body: { ok: true } }; }),
  });
  await api.chat({ text: '跑到哪了', taskId: 'T1' });
  assert.equal(sent.task_id, 'T1');
});

test('reportText 走纯文本分支，不尝试解析 JSON', async () => {
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async () => ({ status: 200, ctype: 'text/plain; charset=utf-8', body: '# 报告\n正文' })),
  });
  const r = await api.reportText('T1');
  assert.equal(r.ok, true);
  assert.equal(r.text, '# 报告\n正文');
});

test('reportText 永远显式传 format=md', async () => {
  let url = '';
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async (u) => { url = u; return { ctype: 'text/plain', body: 'x' }; }),
  });
  await api.reportText('T1');
  assert.match(url, /format=md/);
});

test('reportText 失败时解析 JSON 信封拿错误码', async () => {
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async () => ({ status: 404, body: { ok: false, error: { code: 'TASK_NOT_FOUND', message: '没有这个任务' } } })),
  });
  const r = await api.reportText('T1');
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'TASK_NOT_FOUND');
});

test('reportText 把「HTTP 200 + JSON 错误信封」当成失败，而不是正文', async () => {
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async () => ({
      status: 200,
      ctype: 'application/json',
      body: { ok: false, error: { code: 'TASK_NOT_FINISHED', message: '任务尚未完成' } },
    })),
  });
  const r = await api.reportText('T1');
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'TASK_NOT_FINISHED');
});

test('网络异常不抛出，转成 DRIVER_UNREACHABLE', async () => {
  const api = createApi({
    base: 'http://x',
    fetchImpl: async () => { throw new Error('boom'); },
  });
  const r = await api.health();
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'DRIVER_UNREACHABLE');
});

test('samples 的 n 只接受合法值', async () => {
  let url = '';
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async (u) => { url = u; return { body: { ok: true, samples: [] } }; }),
  });
  await api.samples({ taskId: 'T1', type: 'quarantine', table: 'ratings', n: 999 });
  assert.match(url, /n=20/);   // 非法值回落到 20，不把 500 留给后端
});

test('result 传 explain=1', async () => {
  let url = '';
  const api = createApi({
    base: 'http://x',
    fetchImpl: fakeFetch(async (u) => { url = u; return { body: { ok: true } }; }),
  });
  await api.result('T1', { explain: true });
  assert.match(url, /explain=1/);
});
