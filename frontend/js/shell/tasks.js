/* 任务会话控制（任务 14 报告 §10.1 的建议落地点）。
   这一族原先长在 `main.js` 里：轮询表（startPolling / tick / stopPolling）、
   任务切换（showTask）、刷新接回（resumeLastTask）、清空展示（startNewTask）。
   本轮 F1 要在「新任务」上加一条判据，`main.js` 只剩 2 行余量，所以按 §10.1
   先做**纯搬迁**再改行为：这里的逻辑与搬迁前逐行等价，一行行为都没动。

   口径（三处共用一份，绝不各写各的）：
   - 正在轮询的**只有** `liveTaskId`。切到历史任务只停表、不丢它（裁定 R53/R4），
     「回到当前任务」才有得可回。
   - 轮询停止 = 任务到了终态，也就不再有「当前任务」。 */

const POLL_MS = 3000;

export function createTasks({ store, api }) {
  /* 正在跑的轮询表。切到历史任务只是停表，不动 liveTaskId —— 否则「回到当前任务」就没得回。 */
  let pollTimer = null;

  function clearTimer() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  function stopPolling() {
    clearTimer();
    if (store.get().liveTaskId !== null) store.set({ liveTaskId: null });
  }

  function isBusy() {
    const t = (store.get() || {}).task || {};
    return t.status === 'queued' || t.status === 'running';
  }

  function startPolling(taskId, opts) {
    try { localStorage.setItem('mlgov.lastTaskId', taskId); } catch { /* 隐私模式下忽略 */ }
    store.set(s => ({ liveTaskId: taskId, task: { ...s.task, id: taskId, status: 'queued', opts: opts || {}, startedAt: Date.now(), finishedAt: null } }));
    clearTimer();
    pollTimer = setInterval(() => tick(taskId), POLL_MS);
    tick(taskId);
    return stopPolling;
  }

  async function tick(taskId) {
    const res = await api.status(taskId);
    /* 裁定 R61：clearTimer() 停不掉**已经在路上**的那次 /status。用户切走后它才回来，
       照写会把任务号/状态/进度倒拨回上一个任务，终态时还会 loadResult 到别人头上。 */
    if (taskId !== store.get().task.id) return;   // 用户已切走，这次响应作废
    if (!res.ok) { store.set(s => ({ task: { ...s.task, errors: [...s.task.errors, res.error.message] } })); return; }
    const d = res.data;
    store.set(s => ({ task: taskFrom(d, { id: d.task_id, opts: s.task.opts }) }));
    if (d.status === 'succeeded' || d.status === 'failed') { stopPolling(); if (d.status === 'succeeded') await loadResult(taskId); }
  }

  /* 「切换任务」的取数（裁定 R53）：失败也要带回去，让列表里如实显示。 */
  async function loadTasks() {
    const res = await api.tasks();
    return res.ok
      ? { ok: true, tasks: res.data.tasks || [], error: null }
      : { ok: false, tasks: [], error: res.error.message };
  }

  /* §1.4「新任务」：只清空"你正在看的"，不启动任何任务 —— 启动永远是"左栏发一句话"。
     与切到历史任务同样停表、保留 liveTaskId（裁定 R53），「回到当前任务」还有得可回。 */
  function startNewTask() {
    clearTimer();
    /* F1：清展示就一并把 `liveTaskId` 清掉。判据上这条路径本不该在运行中被走到
       （「新任务」按钮此时是 disabled 的），但**死锁不能再有第二条路**：
       发送键的灰态把 `liveTaskId` 也算作"忙"，留着它就是一个刷不掉的灰键。 */
    store.set({ liveTaskId: null,
      /* 形状与 store 初始的 task 一致（含 stageTotal）：缺了它，任务条印 `—`
         而视图印 `0/0`，同一屏两个说法。 */
      task: { id: null, status: null, stage: null, stageIndex: 0, stageTotal: 9, percent: 0, startedAt: null, finishedAt: null, opts: {}, errors: [] },
      result: null, resultError: null });
  }

  /* 看历史任务：取 /status 再取 /result?explain=1，写法与 loadResult 一致 —— 但不接轮询（§11-R4）。
     先清 result：任务没成功时页面上不该留上一个任务的数字（红线 R1）。 */
  async function showTask(id) {
    if (!id || id === store.get().task.id) return;
    clearTimer();
    const res = await api.status(id);
    if (!res.ok) { store.set(s => ({ task: { ...s.task, errors: [...s.task.errors, res.error.message] } })); return; }
    const d = res.data;
    const terminal = d.status === 'succeeded' || d.status === 'failed';
    /* 裁定 R60：/api/tasks 与 /status 都不带 scope，历史任务的真实参数无从得知 ——
       留着 spread 过来的上一轮 opts，徽标与横幅就会替它作证（spec §7.4：宁可少说不可错说）。 */
    store.set(s => ({ liveTaskId: id === s.liveTaskId && terminal ? null : s.liveTaskId,
      task: taskFrom(d, { id, opts: {} }), result: null }));
    /* 回到还活着的那个任务：把轮询接回去（切走时只是停表，liveTaskId 一直留着）。 */
    if (id === store.get().liveTaskId && d.status !== 'succeeded' && d.status !== 'failed' && !pollTimer) pollTimer = setInterval(() => tick(id), POLL_MS);
    await loadResult(id);
  }

  async function loadResult(taskId) {
    const res = await api.result(taskId, { explain: true });
    /* spec §5.7：禁止把错误折叠成空态。取数失败时把真实原因写进 store，
       总览与五维据此画错误态，而不是留一个「—」装作"没有数据"。 */
    if (!res.ok) { store.set({ resultError: res.error }); return; }
    store.set({ resultError: null });
    const d = res.data;
    store.set({ result: {
      scores: d.scores || null,
      counts: d.counts || null,
      limitations: d.limitations || [],
      ruleNotes: d.rule_notes || null,
      versions: d.versions || null,
      dataVersion: d.data_version || null,
      timeBoundaries: d.time_boundaries || null,
      publishedDir: d.paths ? d.paths.published_dir : undefined,
      explanation: d._explanation || null,
    } });
  }

  /* /status 的响应 → store.task 的形状。三处（tick / showTask / resumeLastTask）用的是同一套字段，
     所以只写一份。`opts` 单独传：showTask 与 resumeLastTask 都拿不到这一轮的参数（§7.4，绝不猜）。 */
  function taskFrom(d, extra) {
    const terminal = d.status === 'succeeded' || d.status === 'failed';
    return { status: d.status, stage: d.stage, stageIndex: d.stage_index, stageTotal: d.stage_total,
      percent: d.progress_percent,
      startedAt: d.started_at ? Date.parse(d.started_at) : null,
      finishedAt: terminal && d.updated_at ? Date.parse(d.updated_at) : null,
      errors: d.errors || [],
      ...extra };
  }

  /* 刷新后接回任务（spec §6.5、§11-R3）。
     任务已被清理时（TASK_NOT_FOUND）清除该键并静默回到空态，不弹错误。 */
  async function resumeLastTask() {
    let last = null;
    try { last = localStorage.getItem('mlgov.lastTaskId'); } catch { last = null; }
    if (!last) return;
    const res = await api.status(last);
    if (!res.ok) {
      try { localStorage.removeItem('mlgov.lastTaskId'); } catch { /* 忽略 */ }
      return;
    }
    const d = res.data;
    if (d.status === 'queued' || d.status === 'running') {
      startPolling(last, {});                       // 口径拿不到了，走"运行设置未知"分支
    } else if (d.status === 'succeeded') {
      store.set(s => ({ task: taskFrom(d, { id: last, opts: s.task.opts }) }));
      await loadResult(last);
    } else if (d.status === 'failed') {
      /* spec §4.6：刷新后失败的任务必须回到页面上（任务条 + 红色失败横幅），
         而不是静默变成「还没有任务」。原因与错误 ID 来自 /status 的 errors，原样带过来。
         result 保持 null，**不** loadResult：失败的任务没有结果可取（红线 R1）。 */
      store.set(s => ({ task: taskFrom(d, { id: last, opts: {} }) }));   // §7.4：/status 不带 scope，不猜
    }
  }

  /* 对外交出这几个：装配处（main.js）用它们接线，其余（tick / stopPolling / clearTimer /
     taskFrom / loadResult）全是内部细节。`startPolling` 仍返回 `stopPolling`
     —— 搬迁前就是这个形状，调用方按老写法用。 */
  return { startPolling, showTask, startNewTask, resumeLastTask, loadTasks, isBusy };
}
