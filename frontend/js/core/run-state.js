/* 口径判定。两条判据互相独立，绝不可混用：
     执行环境 → paths.published_dir（null 即"没走 Hadoop"，权威判据）
     抽样与否 → opts.scope（正在跑/刚发起的任务取 `/api/chat` 的回显值；刷新接回与历史任务
                自 9f4a798 起取 `/status` 信封的 `scope`，见 shell/tasks.js 的 taskFrom）
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
    /* B3 / F3：这一段是**后端没记录这一轮的口径时**的兜底 —— `status.json` 里没有 `scope`
       的老任务，以及任何拿不到运行设置的情形，都会走到这里。
       （批次 D 之前这里还有第二个来源：`/status` 与 `/api/tasks` 都不回传 `scope`（旧 B12），
       于是刷新接回任务、从「历史任务」切进来的任务必然落到这条；`9f4a798` 补上字段之后，
       那两条路径已经带着真实口径走别的分支了，这条分支只留给"确实没记录"的任务。）
       措辞因此一个字都不能提"历史"——那样会和任务条上的「进行中」当场打架，
       而"页面刚打开"正是最常见的画面。也不替它猜一个「全量」贴上去（spec §7.4：宁可少说不可错说）。
       正文第 2 句说的是**事实**而不是安慰：
       抽样只改 `scope` 这个参数（`agent/http_api.py` 的 `_resolve_scope`：只有显式
       `scope === 'sample'` 才走抽样，否则一律 `full`），**评分算法完全是同一条**
       （`clean_rate` → `score_before` → `score_after` → `aggregate`，与被评数据集多大无关）。
       所以"评分标准没变"成立；而"分数本身不受影响"**不成立**（抽样只跑评分表前 2,000 行，
       分母变小，分数当然会动），所以这句绝不写。 */
    out.push({
      key: 'scope-unknown', kind: 'muted',
      title: '运行设置未知',
      text: '这次运行没有留下运行设置（全量 / 抽样）的记录，所以这里不标注。评分标准与全量模式相同。',
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
