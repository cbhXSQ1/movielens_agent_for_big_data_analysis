import { el, clear } from '../core/dom.js';
import { duration, stageZh, DASH } from '../core/format.js';   // 裁定 R2：不用 int()，去掉未使用的导入
import { judgeRun, runBadges } from '../core/run-state.js';

/* 任务条：任务号 · 状态 · 口径徽标 · 进度 · 用时 · 新任务 · 历史任务。
   阶段进度从"左栏竖排 9 行"搬到这里，纵向省出约 300px（spec §4.1）。 */
const STATUS_ZH = { queued: '排队中', running: '进行中', succeeded: '已完成', failed: '失败' };

export function createTaskbar({ host, store, onPickTask, onNewTask, loadTasks }) {
  /* 裁定 R53：弹层是**常驻节点**。render() 每次都 clear(host)，若每次重建 <details>，
     运行中的任务每 3 秒一次 store.set 就会把用户刚展开的列表关掉。 */
  const pick = el('details', 'taskpick');
  /* T1①（plan §1.1）：文案「切换任务」→「历史任务」，summary 文本与 aria-label 同步。 */
  const summary = el('summary', 'btn btn--ghost btn--sm', '历史任务');
  summary.setAttribute('aria-label', '历史任务');
  pick.appendChild(summary);

  const pop = el('div', 'taskpick__pop');
  const list = el('ul', 'taskpick__list');
  const fadeTop = el('div', 'taskpick__fade is-top');
  const fadeBot = el('div', 'taskpick__fade is-bot');
  fadeTop.hidden = true; fadeBot.hidden = true;
  pop.appendChild(list); pop.appendChild(fadeTop); pop.appendChild(fadeBot);
  pick.appendChild(pop);

  /* T2（plan §1.4）：「新任务」与「历史任务」并排。它**只清空"你正在看的"**，
     不启动任何任务 —— 启动永远只有"左栏发一句话"这一个动作。焦点由 main.js 转给输入框。 */
  const newBtn = el('button', 'btn btn--ghost btn--sm', '新任务');
  newBtn.type = 'button';
  newBtn.title = '清空当前结果，然后在下面对 Agent 说话';
  newBtn.addEventListener('click', () => onNewTask());
  const acts = el('span', 'taskbar__acts');     // R58 的空态外边距挂在这一组上（shell.css）
  acts.appendChild(newBtn);
  acts.appendChild(pick);

  let opened = false;        // 首次展开才拉 /api/tasks（spec §4.1）
  let rows = null;           // 拉回来的列表
  let error = null;          // 拉取失败的原因：必须显示在列表里，不能静默留空
  let loading = false;

  /* T1③（plan §1.1）：6px 渐隐只在"真的还有内容"时出现 —— 内容没超过 60vh 时两条都不出现。
     判据取 scrollHeight 与实际可见高之差，读一次即触发同步布局，量到的是当下这一帧。 */
  function updateFades() {
    const more = list.scrollHeight - list.clientHeight;
    /* 4px 一类的零头不算"还有内容"：列表自带 8px 上下内边距，只溢出 4px 时其实什么也没藏住
       （实测 8 条真实任务就是 485 vs 481，那时不该出现"下面还有"的暗示）。 */
    const scrollable = more > 8;
    fadeTop.hidden = !(scrollable && list.scrollTop > 1);
    fadeBot.hidden = !(scrollable && list.scrollTop < more - 1);
  }
  list.addEventListener('scroll', updateFades);

  /* T1②（plan §1.1 与 §5）：点弹层外面任何地方都能关掉。
     监听**只在打开期间**挂载、关闭时移除，不长期占用 document。
     **禁用 `blur` 方案** —— 点列表项的第一下会先把焦点移走，导致点不中。 */
  function onDocClick(e) { if (!pick.contains(e.target)) pick.open = false; }
  function onDocKey(e) {
    if (e.key !== 'Escape') return;          // 不 preventDefault：Esc 还归别的组件（如设置弹窗）
    pick.open = false;
    summary.focus();                         // Esc 关闭要把焦点还给 summary
  }

  /* plan §1.1 验收：Tab 能进列表（都是原生 button），方向键在这里补上。
     只在一项已经拿到焦点时接管，不与 Esc / Tab 抢键。 */
  list.addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const items = [...list.querySelectorAll('.taskpick__item')];
    const i = items.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
  });

  pick.addEventListener('toggle', async () => {
    if (!pick.open) {
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onDocKey);
      return;
    }
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onDocKey);
    if (opened) { renderList(store.get()); return; }
    loading = true;
    renderList(store.get());
    let res;
    /* 取数失败有两种：错误信封（api.tasks 自己兜住的网络错）和真抛异常 ——
       loadTasks 里 `res.data.tasks` 撞上畸形 200（data 为 undefined）就会 reject。
       两种都要落到列表里的那行字上，不能停在"正在读取…"（裁定 R57）。 */
    try { res = await loadTasks(); }
    catch (err) { res = { ok: false, tasks: [], error: String((err && err.message) || err) }; }
    opened = true; loading = false;
    rows = res.tasks; error = res.ok ? null : res.error;
    renderList(store.get());
  });

  function renderList(state) {
    const keepTop = list.scrollTop;   // 运行中每 3 秒重绘一次，别把用户的滚动位置拽回顶部
    clear(list);
    if (loading) { list.appendChild(el('li', 'taskpick__note', '正在读取任务列表…')); return updateFades(); }
    if (error) { list.appendChild(el('li', 'taskpick__note is-error', `读不到任务列表：${error}`)); return updateFades(); }
    const live = state.liveTaskId;
    /* §11-R4：只在"正在轮询的任务"与"当前所看的任务"不同时出现 */
    if (live && live !== state.task.id) {
      const li = el('li', 'taskpick__row');
      const back = el('button', 'btn btn--ghost btn--sm', '回到当前任务');
      back.type = 'button';
      back.addEventListener('click', () => { pick.open = false; onPickTask(live); });
      li.appendChild(back);
      list.appendChild(li);
    }
    const tasks = rows || [];
    /* T1⑤（plan §1.1）：空列表要说话，不留白。 */
    if (tasks.length === 0) list.appendChild(el('li', 'taskpick__note', '还没有任何任务'));
    for (const t of tasks) {
      const li = el('li', 'taskpick__row');
      const item = el('button', 'taskpick__item');
      item.type = 'button';
      if (t.task_id === state.task.id) item.setAttribute('aria-current', 'true');
      item.appendChild(el('span', 'taskpick__id mono', t.task_id));
      item.appendChild(el('span', 'taskpick__status', STATUS_ZH[t.status] || '未知'));
      /* T1⑥（plan §1.1）：有没有结果只由 `status === 'succeeded'` 推出 —— 那是唯一
         会产出结果的终态，别的不猜（spec §7.4 宁可少说不可错说）。 */
      const has = t.status === 'succeeded';
      item.appendChild(el('span', has ? 'taskpick__res' : 'taskpick__res is-none', has ? '有结果' : '无结果'));
      item.appendChild(el('span', 'taskpick__time num', startedText(t.started_at)));
      item.addEventListener('click', () => { pick.open = false; onPickTask(t.task_id); });
      li.appendChild(item);
      list.appendChild(li);
    }
    /* T1④（plan §1.1）：底部固定一行「共 N 个任务 · 最早 MM-DD」，让"还有没有更多"有个明确答案。
       0 条时不写"最早"：那天一个任务都没有，没有日期可说。 */
    if (tasks.length > 0) list.appendChild(footRow(tasks));
    list.scrollTop = keepTop;
    updateFades();
  }

  function footRow(tasks) {
    const at = tasks.map(t => Date.parse(t.started_at)).filter(n => !Number.isNaN(n));
    const text = at.length
      ? `共 ${tasks.length} 个任务 · 最早 ${monthDay(Math.min(...at))}`
      : `共 ${tasks.length} 个任务`;
    return el('li', 'taskpick__foot', text);
  }

  function render(state) {
    clear(host);
    const t = state.task || {};
    /* T5①（plan §1.5①）：用时 = started_at → updated_at（终态即完成时刻），不是任务年龄。
       只算一次，任务条右端的用时与进度条的悬停提示共用。 */
    const secs = t.startedAt ? Math.max(0, ((t.finishedAt || Date.now()) - t.startedAt) / 1000) : null;

    if (!t.id) {
      /* 裁定 R58：空白首屏也要给"历史任务" —— /api/tasks 一直都在，
         没有当前任务时把入口一起撤掉，历史任务就再也没有别的门了。 */
      host.appendChild(el('span', 'taskbar__id', '还没有任务'));
      host.appendChild(acts);
      if (pick.open) renderList(state);
      return;
    }

    host.appendChild(el('span', 'taskbar__id', t.id));

    const statusText = STATUS_ZH[t.status] || '未知';
    host.appendChild(el('span', 'taskbar__status', statusText));

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

    host.appendChild(acts);                 // 常驻节点，open 状态跟着走
    if (pick.open) renderList(state);       // 展开时就地刷新"当前项"标记
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

/* 列表里的时刻按本机时区显示（status.json 存的是 UTC），与任务号里的本地时刻对得上。 */
const pad = n => String(n).padStart(2, '0');

function startedText(iso) {
  if (!iso) return DASH;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return String(iso);
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} `
    + `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

/* T1④：底部计数里的「最早 MM-DD」。 */
function monthDay(ms) {
  const d = new Date(ms);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
