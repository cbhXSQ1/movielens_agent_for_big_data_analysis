/* 口径判定。两条判据互相独立，绝不可混用：
     执行环境 → paths.published_dir（null 即"没走 Hadoop"，权威判据）
     抽样与否 → opts.scope（后端 /api/chat 回显的真实生效值）
   两者都拿不到时一律 'unknown'，宁可少说不可错说。 */
export function judgeRun({ opts, publishedDir } = {}) {
  let mode = 'unknown';
  if (publishedDir === null) mode = 'local';
  else if (typeof publishedDir === 'string' && publishedDir.length > 0) mode = 'cluster';

  const raw = opts && opts.scope;
  const scope = (raw === 'full' || raw === 'sample') ? raw : 'unknown';

  return { mode, scope };
}

export function runBadges(judged) {
  const out = [];
  if (judged.mode === 'cluster') out.push({ kind: 'ok', text: 'Hadoop 集群' });
  if (judged.mode === 'local') out.push({ kind: 'muted', text: '本地引擎' });
  if (judged.scope === 'full') out.push({ kind: 'ok', text: '全量' });
  if (judged.scope === 'sample') out.push({ kind: 'warn', text: '抽样' });
  return out;
}

import { stageZh } from './format.js';

/* 每条横幅的形状：{ key, kind, title, text, code? }。
   code 只有"任务失败"这一条会带 —— 它是可复制的错误 ID（spec §4.6）。 */
export function runBanners({ judged, healthOk, hasTask = true, task = null }) {
  const out = [];
  if (healthOk === false) {
    out.push({
      key: 'offline', kind: 'danger',
      title: '连不上 Agent 服务',
      text: '后端地址 http://localhost:8765。启动后端后本页会自动恢复。',
    });
  }
  if (judged && judged.mode === 'local') {
    out.push({
      key: 'local', kind: 'warn',
      title: '本次没走 Hadoop',
      text: '结果没有发布到 HDFS。',
    });
  }
  if (judged && judged.scope === 'sample') {
    out.push({
      key: 'sample', kind: 'warn',
      title: '这是抽样结果',
      text: '评分表前 2,000 行。',
    });
  }
  if (judged && judged.scope === 'unknown' && hasTask) {
    out.push({
      key: 'scope-unknown', kind: 'muted',
      title: '本次的运行设置未知',
      text: '页面重新载入后拿不到这一轮的参数。',
    });
  }
  if (judged && judged.scope === 'full' && judged.mode === 'cluster') {
    out.push({ key: 'full', kind: 'ok', title: '全量 · Hadoop 集群执行', text: '' });
  }
  /* spec §4.6：任务失败要说清"在哪个阶段、真实原因、错误 ID"。
     原因取 `errors[0]`（/status 的真实信封），拿不到就说"没有更多信息"，
     绝不拿上一轮的数字或空白糊过去。 */
  if (task && task.status === 'failed') {
    const first = (task.errors || [])[0] || {};
    out.push({
      key: 'failed', kind: 'danger',
      title: task.stage ? `任务在「${stageZh(task.stage)}」阶段失败` : '任务失败',
      text: first.message || '没有更多信息。',
      code: first.code || task.id || '',
    });
  }
  return out;
}
