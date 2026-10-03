import { el, clear } from '../core/dom.js';
import { duration, stageZh, DASH } from '../core/format.js';   // 裁定 R2：不用 int()，去掉未使用的导入
import { judgeRun, runBadges } from '../core/run-state.js';

/* 任务条：任务号 · 状态 · 口径徽标 · 进度 · 用时 · 切换任务。
   阶段进度从"左栏竖排 9 行"搬到这里，纵向省出约 300px（spec §4.1）。 */
const STATUS_ZH = { queued: '排队中', running: '进行中', succeeded: '已完成', failed: '失败' };

export function createTaskbar({ host, store, onPickTask, loadTasks }) {
  /* 裁定 R53：弹层是**常驻节点**。render() 每次都 clear(host)，若每次重建 <details>，
     运行中的任务每 3 秒一次 store.set 就会把用户刚展开的列表关掉。 */
  const pick = el('details', 'taskpick');
  const summary = el('summary', 'btn btn--ghost btn--sm', '切换任务');
  pick.appendChild(summary);
  const list = el('ul', 'taskpick__list');
  pick.appendChild(list);

  let opened = false;        // 首次展开才拉 /api/tasks（spec §4.1）
  let rows = null;           // 拉回来的列表
  let error = null;          // 拉取失败的原因：必须显示在列表里，不能静默留空
  let loading = false;

  pick.addEventListener('toggle', async () => {
    if (!pick.open) return;
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
    clear(list);
    if (loading) { list.appendChild(el('li', 'taskpick__note', '正在读取任务列表…')); return; }
    if (error) { list.appendChild(el('li', 'taskpick__note is-error', `读不到任务列表：${error}`)); return; }
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
    if (tasks.length === 0) { list.appendChild(el('li', 'taskpick__note', '还没有历史任务。')); return; }
    for (const t of tasks) {
      const li = el('li', 'taskpick__row');
      const item = el('button', 'taskpick__item');
      item.type = 'button';
      if (t.task_id === state.task.id) item.setAttribute('aria-current', 'true');
      item.appendChild(el('span', 'taskpick__id mono', t.task_id));
      item.appendChild(el('span', 'taskpick__status', STATUS_ZH[t.status] || '未知'));
      item.appendChild(el('span', 'taskpick__time num', startedText(t.started_at)));
      item.addEventListener('click', () => { pick.open = false; onPickTask(t.task_id); });
      li.appendChild(item);
      list.appendChild(li);
    }
  }

  function render(state) {
    clear(host);
    const t = state.task || {};

    if (!t.id) {
      /* 裁定 R58：空白首屏也要给"切换任务" —— /api/tasks 一直都在，
         没有当前任务时把入口一起撤掉，历史任务就再也没有别的门了。 */
      host.appendChild(el('span', 'taskbar__id', '还没有任务'));
      host.appendChild(pick);
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
      host.appendChild(bar);
      host.appendChild(el('span', 'taskbar__pct num', `${t.percent || 0}%`));
    }

    if (t.startedAt) {
      /* 用时 = started_at → updated_at（终态即完成时刻），不是任务年龄。 */
      const endAt = t.finishedAt || Date.now();
      const secs = Math.max(0, (endAt - t.startedAt) / 1000);
      host.appendChild(el('span', 'taskbar__time num', duration(secs)));
    }

    host.appendChild(pick);                 // 常驻节点，open 状态跟着走
    if (pick.open) renderList(state);       // 展开时就地刷新"当前项"标记
  }
  return { render };
}

/* 列表里的时刻按本机时区显示（status.json 存的是 UTC），与任务号里的本地时刻对得上。 */
function startedText(iso) {
  if (!iso) return DASH;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return String(iso);
  const p = n => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
}
