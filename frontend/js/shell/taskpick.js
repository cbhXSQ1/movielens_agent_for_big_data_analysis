/* 「历史任务」弹层：`<details>` + 列表渲染 + 点外关闭 + 渐隐 + 计数 + 相对时间。
   批次 D 从 `taskbar.js` **纯搬迁**而来 —— 搬迁这一手本身一行行为都没动（逐行等价），
   理由是 S1 要往列表里标出每一项的 scope，而 `taskbar.js` 已贴着 §10.13 的 250 行上限
   （搬迁前 248 行、余量 1 行）。搬迁后 `taskbar.js` 只留任务条本身的渲染。
   裁定 R53：弹层是**常驻节点** —— `taskbar.render()` 每次都 `clear(host)`，
   若每次重建 `<details>`，运行中的任务每 3 秒一次 `store.set` 就会把用户刚展开的列表关掉。 */
import { el, clear } from '../core/dom.js';
import { DASH } from '../core/format.js';
import { judgeRun, runBadges } from '../core/run-state.js';

/* 任务状态的中文字面：任务条与列表共用一份（两处各写各的就迟早会不一致）。 */
export const STATUS_ZH = { queued: '排队中', running: '进行中', succeeded: '已完成', failed: '失败' };

export function createTaskpick({ store, onPickTask, loadTasks }) {
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
      /* S1（批次 D）：`/api/tasks` 自 9f4a798 起每项都回传 `scope`，这里如实标出来 ——
         「全量」/「抽样」的字面与徽标色**复用 run-state 的同一份判据**（runBadges），
         不在这里各写各的。信封里没有 scope 的**老任务什么都不标**：不写"未知"、
         不替它猜一个（spec §7.4：宁可少说不可错说）。 */
      const sc = runBadges(judgeRun({ opts: { scope: t.scope } }))[0];
      if (sc) item.appendChild(el('span', `taskpick__scope badge badge--${sc.kind}`, sc.text));
      /* T1⑥（plan §1.1）：有没有结果只由 `status === 'succeeded'` 推出 —— 那是唯一
         会产出结果的终态，别的不猜（spec §7.4 宁可少说不可错说）。 */
      const has = t.status === 'succeeded';
      item.appendChild(el('span', has ? 'taskpick__res' : 'taskpick__res is-none', has ? '有结果' : '无结果'));
      /* B8（plan T1）：一天以内显示时刻，超过一天显示「昨天 / N 天前」；完整时刻进 title。
         其它信息一个字没动，列表的行结构也不变。 */
      const when = el('span', 'taskpick__time num', startedText(t.started_at, Date.now()));
      when.title = t.started_at ? stamp(t.started_at) : '';
      item.appendChild(when);
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

  /* 任务条每 3 秒重绘一次（`render`），展开着的列表要跟着刷新"当前项"标记；
     没展开就一个像素都不动（这也是搬迁前的行为）。 */
  function refresh(state) { if (pick.open) renderList(state); }

  return { node: pick, refresh };
}

/* 列表里的时刻按本机时区显示（status.json 存的是 UTC），与任务号里的本地时刻对得上。 */
const pad = n => String(n).padStart(2, '0');
const DAY = 86400000;
const atMidnight = ms => { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); };

function stamp(iso) {
  const t = new Date(iso);
  if (!iso || Number.isNaN(t.getTime())) return iso ? String(iso) : DASH;
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())} `
    + `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

/* B8（plan T1 / §2.2）：一天以内照旧显示时刻（会盯着秒看的都是刚发生的），
   超过一天改说「昨天 / N 天前」—— 相对时间一眼能对上"多久以前"，
   而完整时刻始终能在 title 里查到（列表项调用处挂上）。 */
function startedText(iso, now) {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t) || now - t < DAY) return stamp(iso);
  const days = Math.round((atMidnight(now) - atMidnight(t)) / DAY);
  return days <= 1 ? '昨天' : `${days} 天前`;
}

/* T1④：底部计数里的「最早 MM-DD」。 */
function monthDay(ms) {
  const d = new Date(ms);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
