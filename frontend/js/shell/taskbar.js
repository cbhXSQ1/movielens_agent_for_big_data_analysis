import { el, clear } from '../core/dom.js';
import { duration, stageZh, DASH } from '../core/format.js';   // 裁定 R2：不用 int()，去掉未使用的导入
import { judgeRun, runBadges } from '../core/run-state.js';
import { createTaskpick, STATUS_ZH } from './taskpick.js';

/* 任务条：任务号 · 状态 · 口径徽标 · 进度 · 用时 · 新任务 · 历史任务。
   阶段进度从"左栏竖排 9 行"搬到这里，纵向省出约 300px（spec §4.1）。
   批次 D：历史任务弹层（details/summary、列表渲染、点外关闭、渐隐、计数、相对时间）
   整块**纯搬迁**到 `taskpick.js` —— S1 要给列表项加 scope 徽标，而这里只剩 1 行余量
   （§10.13 上限 250）。本文件现在只剩任务条本身的渲染，外加 B10 的复制回执。 */
/* F1：运行中「新任务」也要灰掉 —— 与发送键同源（B1 的裁定：运行中不许起新任务）。 */
const BUSY_TITLE = '任务正在跑，跑完才能清空当前结果';

export function createTaskbar({ host, store, onPickTask, onNewTask, loadTasks, isBusy }) {
  /* 裁定 R53：弹层是**常驻节点**（`render()` 每次都 clear(host)，重建会把展开状态关掉）。
     构造交给 taskpick.js，这里只把它挂进 `acts`、并在重绘时顺手刷新它。 */
  const picker = createTaskpick({ store, onPickTask, loadTasks });

  /* T2（plan §1.4）：「新任务」与「历史任务」并排。它**只清空"你正在看的"**，
     不启动任何任务 —— 启动永远只有"左栏发一句话"这一个动作。焦点由 main.js 转给输入框。 */
  const newBtn = el('button', 'btn btn--ghost btn--sm', '新任务');
  newBtn.type = 'button';
  newBtn.title = '清空当前结果，然后在下面对 Agent 说话';
  newBtn.addEventListener('click', () => onNewTask());
  const acts = el('span', 'taskbar__acts');     // R58 的空态外边距挂在这一组上（shell.css）
  acts.appendChild(newBtn);
  acts.appendChild(picker.node);

  /* B10：`#toast` 从重建起就是个死元素（HTML 有、CSS 没有、JS 没有生产者）。这一批把它用起来
     —— 失败横幅那串可复制的错误 ID（批次 B 的 `.banner__code`）点一下就是"复制 + 回执"。 */
  wireCopyToast();

  function render(state) {
    clear(host);
    const t = state.task || {};
    /* F1（Critical，死锁）：任务在跑（queued / running）时「新任务」必须**可用**判据、
       不可点。它清展示时会把 `liveTaskId` 一起清掉，而发送键的灰态把 `liveTaskId`
       也算作"忙" —— 运行中点它就会留下一个再也没人去解的灰键
       （`liveTaskId` 只有 stopPolling 会清，而它先被 clearTimer() 掐断了）。
       `isBusy` 与 composer 的发送键是同一个判据，两处不会各说各的。 */
    const busy = !!(isBusy && isBusy());
    newBtn.disabled = busy;
    newBtn.title = busy ? BUSY_TITLE : '清空当前结果，然后在下面对 Agent 说话';
    /* T5①（plan §1.5①）：用时 = started_at → updated_at（终态即完成时刻），不是任务年龄。
       只算一次，任务条右端的用时与进度条的悬停提示共用。 */
    const secs = t.startedAt ? Math.max(0, ((t.finishedAt || Date.now()) - t.startedAt) / 1000) : null;

    if (!t.id) {
      /* 裁定 R58：空白首屏也要给"历史任务" —— /api/tasks 一直都在，
         没有当前任务时把入口一起撤掉，历史任务就再也没有别的门了。 */
      host.appendChild(el('span', 'taskbar__id', '还没有任务'));
    } else {
      host.appendChild(el('span', 'taskbar__id', t.id));

      const statusText = STATUS_ZH[t.status] || '未知';
      host.appendChild(el('span', 'taskbar__status', statusText));

      /* S1（批次 D）：这里的 `t.opts` 现在来自真实信封 —— `tasks.js` 的 `taskFrom` 会从
         `/status` 的 `scope` 取值（`/api/chat` 的回显优先），所以刷新接回的历史任务
         也能打出「全量」/「抽样」徽标，而不是一律"未知"。信封里没有 scope 的老任务仍是空
         `opts` → `judgeRun` 给 'unknown' → 一个徽标都不出（§7.4，绝不猜）。 */
      const judged = judgeRun({ opts: t.opts, publishedDir: state.result ? state.result.publishedDir : undefined });
      for (const b of runBadges(judged)) {
        const chip = el('span', `badge badge--${b.kind}`, b.text);
        host.appendChild(chip);
      }

      if (t.status === 'running' || t.status === 'queued') {
        host.appendChild(el('span', 'taskbar__stage', stageZh(t.stage)));
        const bar = el('span', 'taskbar__bar');
        const fill = el('span', 'taskbar__fill');
        fill.style.width = `${Math.max(0, Math.min(100, t.percent || 0))}%`;
        bar.appendChild(fill);
        /* T5②（plan §1.5③）：悬停看阶段 + 已用时，**不含百分比**（用户明确要求）。 */
        bar.title = secs == null ? `${stageZh(t.stage)} ${stepText(t)}`
          : `${stageZh(t.stage)} ${stepText(t)} · 已跑 ${spentText(secs)}`;
        host.appendChild(bar);
        /* T5①：去掉百分比，改离散诚实的 `4/9`（各阶段耗时不同，百分比会让人误判"快好了"）。 */
        host.appendChild(el('span', 'taskbar__pct num', stepText(t)));
        /* B2（plan §2.1）：后端没有取消接口，界面上得说清楚，否则用户会一直找。 */
        host.appendChild(el('span', 'taskbar__note', '本版不支持取消，跑完即可'));
      }

      if (secs != null) host.appendChild(el('span', 'taskbar__time num', duration(secs)));
    }

    host.appendChild(acts);                 // 常驻节点，open 状态跟着走
    picker.refresh(state);                  // 展开时就地刷新"当前项"标记（没展开什么都不做）
  }
  return { render };
}

/* T5①：`4/9` 读的是 state.task 的 stageIndex / stageTotal，**不写死 9**；
   后端没给这两个字段时按"没有这个数"处理，返回 '—'（format 层的规矩）。 */
function stepText(t) {
  return Number.isFinite(t.stageIndex) && Number.isFinite(t.stageTotal)
    ? `${t.stageIndex}/${t.stageTotal}` : DASH;
}

/* T5②：悬停提示里的「已跑 1 分 12 秒」。数字仍出自 format.js 的 `duration()`
   （它给的是 `1:12`，这里只是拆开重排成口语）—— core/ 不在本批改动范围内。 */
function spentText(secs) {
  const [m, s] = String(duration(secs)).split(':');
  return m === DASH ? DASH : `${Number(m)} 分 ${s} 秒`;
}

/* ---- B10：`#toast` 的生产者（回执） -------------------------------------------------
   元素一直在 index.html（`role="status" aria-live="polite"`）、`--z-toast` 也一直在 tokens.css，
   缺的只是样式与生产者。这里补生产者，样式在 components.css（含 prefers-reduced-motion）。
   落点选失败横幅的错误 ID：`#banner-stack` 上挂一个**委托**监听（横幅每次重绘都换子节点，
   挂在父节点上才不会随重绘丢）。**没有**改 `shell/banners.js` —— 它不在本批的可改清单里，
   所以复制这件事不在渲染横幅的那一处接线（报告 §偏离 1 记了这笔）。 */
const TOAST_MS = 3200;                       // 回执停留时长，够读完一行字
const TOAST_COPIED = '已复制错误 ID';
const TOAST_MANUAL = '复制失败，请手动选中后再复制';

function wireCopyToast() {
  const toast = document.getElementById('toast');
  const stack = document.getElementById('banner-stack');
  if (!toast || !stack) return;              // 元素缺失就什么都不做，不抛
  let timer = null;
  const show = text => {
    toast.textContent = text;
    toast.classList.add('toast--visible');
    clearTimeout(timer);
    timer = setTimeout(() => toast.classList.remove('toast--visible'), TOAST_MS);
  };
  stack.addEventListener('click', e => {
    const code = e.target && e.target.closest ? e.target.closest('.banner__code') : null;
    if (!code) return;
    const text = (code.textContent || '').trim();
    if (!text) return;
    /* 剪贴板是浏览器原生能力，不给它加按钮也不加状态；写不进去（没权限/老引擎）就
       把那段文字选中，用户按 Ctrl+C 一样能复制 —— 回执如实说"请手动"。 */
    const clip = navigator.clipboard;
    if (clip && clip.writeText) {
      clip.writeText(text).then(() => show(TOAST_COPIED),
        () => { selectText(code); show(TOAST_MANUAL); });
    } else { selectText(code); show(TOAST_MANUAL); }
  });
}

function selectText(node) {
  const sel = window.getSelection && window.getSelection();
  if (!sel || !document.createRange) return;
  const range = document.createRange();
  range.selectNodeContents(node);
  sel.removeAllRanges();
  sel.addRange(range);
}
