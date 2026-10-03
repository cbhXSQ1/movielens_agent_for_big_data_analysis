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

export function runBanners({ judged, healthOk }) {
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
  if (judged && judged.scope === 'unknown') {
    out.push({
      key: 'scope-unknown', kind: 'muted',
      title: '本次的运行设置未知',
      text: '页面重新载入后拿不到这一轮的参数。',
    });
  }
  if (judged && judged.scope === 'full' && judged.mode === 'cluster') {
    out.push({ key: 'full', kind: 'ok', title: '全量 · Hadoop 集群执行', text: '' });
  }
  return out;
}
