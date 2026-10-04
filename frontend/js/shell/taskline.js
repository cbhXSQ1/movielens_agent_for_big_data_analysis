/* 左栏时间线上的**任务系统行**（plan §2.2-B6）。这一族原先根本没有：左栏只有用户气泡与
   Agent 气泡，「任务已提交 / 开始跑 / 完成 / 失败」一个字都没有，用户看不出"我发的这句话"
   和任务条上那个任务是不是同一件事。

   为什么单独一个文件而不是塞进 `timeline.js`：
   ① 这里的判据（哪些状态才配一行、同一个任务绝不重复出同一行）是**纯函数**，一行 DOM 都不碰 ——
      放在同一个文件里就只能靠"装配一棵 DOM 再数行数"来验，说不清是判据对还是渲染碰巧对；
   ② `timeline.js` 只管"把 state 画成 DOM"，这里只管"两拍 state 之间该多出哪几行"，
      两件事的输入输出形状完全不同，混在一起后改任何一边都要重读另一边。

   红线 R1 的精神：这些行里**没有任何数据数字**。任务号可以（它是标识不是数据，与任务条、
   历史列表里显示的是同一串）；阶段名一律走 `core/format.js` 的 `stageZh()`，不在这里写死中文。 */
import { stageZh } from '../core/format.js';

/* 措辞与行首符号（符号是**状态**标记，不是数据数字）。
   `queued` 不配单独一行：它只是"提交了"与"真的开跑"之间的过渡态，两者在同一拍附近。 */
const OK_ZH = { running: '开始跑', succeeded: '已完成', failed: '失败' };
const MARK = { submitted: '·', running: '▸', succeeded: '✓', failed: '×' };

const line = (key, mark, text, id, live) => ({ key, mark, text, id, live });

/* 任务号的行内文本：跟在「任务」后面。 */
function head(id) {
  return id ? `任务 ${id}` : '任务';
}

/* 这一对 (prev, task) 之间该多出哪几行。返回数组的**顺序**就是它们出现的顺序。
   `seeded` = 抽行之前时间线上已经有过系统行；没有过的时候（刷新后接回一个已终态的任务）
   不补「已提交 / 已完成」——见调用方 `timeline.js` 的说明，否则每刷新一次就凭空多出两行。
   返回的 `key` 在一条任务内唯一：它同时是"同一行绝不出现两次"的可断言之物。 */
export function taskLines(prev, task, seeded) {
  const id = (task && task.id) || null;
  const out = [];
  /* 刷新后接回：`id` 在装载时就已存在，这一对不是"刚提交"，只是页面第一次看到这个任务。 */
  const submittedNow = !!id && !prev.id;
  let suppress = !seeded && !submittedNow;

  const push = l => { out.push(l); suppress = false; };
  const ok = l => { if (!suppress) push(l); };

  if (id !== prev.id) {
    /* 换了任务号 = 一轮新任务（历史轮次的行已被调用方清掉，见 `timeline.js` 的 `sysId`）。 */
    if (submittedNow) push(line('submitted', MARK.submitted, '已提交', id, true));
  } else if (id) {
    const status = (task && task.status) || null;
    const stage = (task && task.stage) || null;
    const first = !prev.id;                 // prev.id 为空而 id 有值 = 提交那一拍，开跑那行留给下一拍
    if (!first && status && status !== prev.status) {
      if (status === 'running') ok(line('started', MARK.running, OK_ZH.running, id, true));
      else if (status === 'succeeded') ok(line('succeeded', MARK.succeeded, OK_ZH.succeeded, id, true));
      /* 失败时把停在哪个阶段一并说出来 —— 这是"错在哪"的第一个线索，而阶段名同样是中文映射出来的。 */
      else if (status === 'failed') {
        ok(line('failed', MARK.failed, stage ? `失败在「${stageZh(stage)}」` : OK_ZH.failed, id, true));
      }
    }
    /* 阶段推进：一条任务每个阶段最多一行。判据是"阶段变了"，**不是**"轮询到了" ——
       3 秒一次的状态刷新里阶段没变时一个节点都不产出，所以刷屏在判据上就不可能。
       另一个前提是这一拍**状态本身没变**：状态变了的那一拍已经有它自己的一行了（开始跑 /
       已完成 / 失败），再补一条阶段行就是同义重复 —— 终态尤其明显：真信封里 `succeeded`
       那条的 `stage` 正是 `done`，`stageZh('done')` = 「完成」，于是任务会以
       「✓ 已完成」后面再跟一条「▸ 进入「完成」」结尾（domcheck 第一版就是这么抓到的）。 */
    if (!first && status === 'running' && status === prev.status && stage && stage !== prev.stage) {
      ok(line(`stage:${stage}`, MARK.running, `进入「${stageZh(stage)}」`, id, false));
    }
  }

  return { lines: out,
    next: { id, status: (task && task.status) || null,
      stage: (task && task.stage) || null, seeded: seeded || out.length > 0 } };
}

/* 行的文案与无障碍属性。`live: false`（阶段推进）显式写 `aria-live="off"`：
   它是 `role="log"` + `polite` 容器里的一个**静默**节点 —— 读屏软件照样能读到它，
   但每 3 秒一次的阶段推进不会变成播报噪音。提交 / 开跑 / 完成 / 失败这四种保留播报。 */
export function lineText(l) {
  return `${head(l.id)} · ${l.text}`;
}

export function lineAttrs(l) {
  const cls = `timeline__sys timeline__sys--${l.live ? 'live' : 'quiet'}`;
  return { cls, mark: l.mark, text: lineText(l), live: l.live ? 'polite' : 'off' };
}
