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
    /* B3：/status 与 /api/tasks 都不回传 scope（B12 记录在案、本轮不动后端），
       所以刷新与切历史任务一定会走到这里。措辞必须读起来是"正常"，而不是"出错了"；
       也**绝不**替它猜一个「全量」贴上去（spec §7.4：宁可少说不可错说）。 */
    out.push({
      key: 'scope-unknown', kind: 'muted',
      title: '历史任务不记录运行设置',
      text: '这是正常的：运行设置只随这一轮的执行过程回传。页面重新载入或切换到历史任务后，它就拿不到了。',
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
      title: failureTitle(task),
      text: first.message || '没有更多信息。',
      code: first.code || task.id || '',
    });
  }
  return out;
}

/* B4 / spec §4.6：失败横幅先给一句"这是哪一类失败"，原始异常留作次要小字。
   标题里两件事都要有：**在哪个阶段失败**（spec §4.6 的硬要求，也是 R50 之外唯一的定位信息）
   与**归到哪一类**（B4）。用 ` ｜ ` 分隔，读起来是
   「任务在「评分表清洗」阶段失败 ｜ 后端启动 Hadoop 失败」。
   归类只看 `StageError`（agent/errors.py）真正会抛的东西（FileNotFoundError /
   subprocess 的 OSError / 连接错误）；认不出的异常不改写、不猜类别，只留阶段那半句。
   原始异常一个字都不删，整条留在 text 里。 */
function failureTitle(task) {
  const stage = task.stage ? `任务在「${stageZh(task.stage)}」阶段失败` : '任务失败';
  const kind = failureKind(String(((task.errors || [])[0] || {}).message || ''));
  return kind ? `${stage} ｜ ${kind}` : stage;
}

function failureKind(msg) {
  if (/FileNotFoundError|No such file|WinError 2|cannot find the (?:file|path)|系统找不到/i.test(msg)) {
    return '后端启动 Hadoop 失败';
  }
  if (/ConnectionError|Connection refused|Max retries|10061/i.test(msg)) return '后端连不上 Hadoop 或数据库';
  if (/OSError|Command '.*' returned non-zero|CalledProcessError|returned non-zero/i.test(msg)) {
    return '后端调用外部程序失败';
  }
  if (/ModuleNotFoundError|ImportError|No module named/i.test(msg)) return '后端缺少依赖';
  return '';
}

/* T2 第 2 点：首屏引导。只为"没有任务**且**没有结果"准备 —— 这个条件写在这里一次，
   五个视图都调它，不各自重写判据（也不会出现两处判得不一样）。
   返回值是给 `textContent` 的字符串；调用方自己决定往哪个 `.panel__body` 里放。 */
export const BOOT_GUIDE = '在左栏对 Agent 说一句话就开始，比如「用默认规则清洗并评估五维质量」；也可以从「历史任务」里挑一次已有结果看。';

export function bootGuide(app, task) {
  const idle = !task || !task.id;
  return idle && !(app && app.result) ? BOOT_GUIDE : '';
}

/* T5：任务没跑完时，五个视图各自说一句"这里以后会出现什么"。
   红线 R1 不破：句子里没有任何任务产出的数据数字 —— 只有任务状态（阶段名与阶段序号，
   任务条本来就在显示同一份信息）。阶段名一律走 stageZh()，中文不写在视图里。 */
export function runningNote(task, tail) {
  if (!task || (task.status !== 'queued' && task.status !== 'running')) return '';
  const at = task.stage ? `「${stageZh(task.stage)}」阶段（${task.stageIndex || 0}/${task.stageTotal || 0}）` : '处理中';
  return `任务正在${at}。完成后${tail}`;
}
