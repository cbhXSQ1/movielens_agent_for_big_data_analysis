import { createStore } from './core/store.js';
import { createApi } from './core/api.js';
import { createConfig } from './core/config.js';
import { createRouter } from './shell/router.js';
import { createTaskbar } from './shell/taskbar.js';
import { createComposer } from './shell/composer.js';
import { createBanners } from './shell/banners.js';
import { createTimeline } from './shell/timeline.js';
import { createSplitter } from './shell/splitter.js';
import { createSettings } from './shell/settings.js';

import overview from './views/overview.js';
import scores from './views/scores.js';
import cleaning from './views/cleaning.js';
import evidence from './views/evidence.js';
import basis from './views/basis.js';

export const VIEWS = [overview, scores, cleaning, evidence, basis].sort((a, b) => a.order - b.order);

const store = createStore({
  health: { ok: null, llmSupported: false, llmConfigured: false },
  llm: readLlm(),
  task: { id: null, status: null, stage: null, stageIndex: 0, stageTotal: 9, percent: 0, startedAt: null, finishedAt: null, opts: {}, errors: [] },
  /* 裁定 R53 / spec §11-R4：正在轮询的任务号单独存 —— 「切换任务」看历史结果时
     它还在，但已经不接轮询，所以「回到当前任务」才有得可回。 */
  liveTaskId: null,
  result: null,
  /* 取不到 /result 时的真实原因（spec §5.7：错误不得折叠成空态）。null = 没失败。 */
  resultError: null,
  timeline: [],
  evidence: { type: 'quarantine', table: 'ratings', n: 20, samples: [], totalAvailable: null, ruleFilter: null },
  report: { md: null },
  view: 'overview',
  viewParams: {},
  scoringCfg: null,
  configError: null,
  ui: { railWidth: readRailWidth(), banners: [] },
});

const api = createApi({});
const config = createConfig({});

/* 裁定 R1 / R11：`createRouter` 的签名是 `{ views, navHost, viewHost, store, ctx }`，
   ctx 由这里一次性传进去，router 会在每次 mount 时把它交给视图 —— 
   **不要**再写"给每个视图包一层 mount 把 ctx 塞进去"的猴补丁，那是隐式全局注入。 */
const ctx = { store, api, config, go: (id, params) => router.go(id, params) };
window.__APP__ = ctx;          // 仅调试用；生产逻辑不依赖它（任务 15 的验收会用到）

const router = createRouter({
  views: VIEWS,
  navHost: document.getElementById('nav'),
  viewHost: document.getElementById('view-host'),
  store,
  ctx,
});

const taskbar = createTaskbar({
  host: document.getElementById('taskbar'),
  store, api,
  onPickTask: showTask,          // 裁定 R53：不再是空壳，见下面 showTask
  loadTasks,                     // 首次展开「切换任务」时拉 /api/tasks
});
const composer = createComposer({
  form: document.getElementById('composer'),
  store, api,
  onSent: entry => {
    store.set(s => ({ timeline: [...s.timeline, entry] }));
    if (entry.taskId && entry.taskStarted) startPolling(entry.taskId, entry.opts);
  },
});
const banners = createBanners({ host: document.getElementById('banner-stack'), store });
const timeline = createTimeline({ host: document.getElementById('timeline'), store });

/* 任务 14：分隔条（键盘可达 + 视觉引导，spec §5.4 / 裁定 R30）与设置弹窗。 */
const splitter = createSplitter({ node: document.getElementById('splitter'), store });
splitter.start(store.get().ui.railWidth);

const settingsBtn = document.getElementById('btn-settings');
createSettings({ modal: document.getElementById('settings-modal'), store, api, opener: settingsBtn })
  .start(settingsBtn);

store.subscribe(state => {
  taskbar.render(state);
  banners.render(state);
});
/* 裁定 R12/R29：router 的首次导航只跑 mount、不派 update，
   所以渲染数据的模块一律在装配时自绘一次（下面 taskbar / banners 同理）。 */
store.subscribe(state => timeline.render(state));
timeline.render(store.get());

/* 裁定 R16：spec 第 163 行「顶栏 品牌 · 数据版本 · 后端状态 · [设置]」与 brief Step 5
   「/health 成功后，右上角绿点 + 「后端已连接」」都要求顶栏那个元素跟着 health 走，
   但 Step 4 给的代码只写 store —— index.html 的 #health 与 shell.css 的
   `.health[data-ok]` 全树没有任何生产者。最小补齐：一处订阅，只驱动这一个元素。 */
const healthChip = document.getElementById('health');
store.subscribe(state => {
  const ok = state.health.ok;                    // 三态：null 还没探过 → 灰点
  healthChip.dataset.ok = String(ok);
  healthChip.querySelector('.health__text').textContent =
    ok === null ? '检测后端…' : (ok ? '后端已连接' : '后端未连接');
});

composer.start();
taskbar.render(store.get());
banners.render(store.get());

async function checkHealth() {
  const res = await api.health();
  store.set({ health: res.ok
    ? { ok: true, llmSupported: !!res.data.llm.supported, llmConfigured: !!res.data.llm.configured }
    : { ok: false, llmSupported: false, llmConfigured: false } });
}
checkHealth();
setInterval(checkHealth, 15000);

/* 任务 12 Step 3：五维的 D2 下钻要知道"哪些指标构成哪个维度"，
   映射一律从 config/scoring_scheme.v1.json 读（约束 G14），不写进代码。 */
config.loadScoring().then(res => {
  if (res.ok) store.set({ scoringCfg: res.data });
  else store.set({ configError: res.error.message });
});

/* 正在跑的轮询表。切到历史任务只是停表，不动 liveTaskId —— 否则「回到当前任务」就没得回。 */
let pollTimer = null;

function startPolling(taskId, opts) {
  try { localStorage.setItem('mlgov.lastTaskId', taskId); } catch { /* 隐私模式下忽略 */ }
  store.set(s => ({ liveTaskId: taskId, task: { ...s.task, id: taskId, status: 'queued', opts: opts || {}, startedAt: Date.now(), finishedAt: null } }));
  clearTimer();
  pollTimer = setInterval(() => tick(taskId), 3000);
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

function clearTimer() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

/* 轮询停止 = 任务到了终态，也就不再有「当前任务」。 */
function stopPolling() {
  clearTimer();
  if (store.get().liveTaskId !== null) store.set({ liveTaskId: null });
}

/* 「切换任务」的取数（裁定 R53）：失败也要带回去，让列表里如实显示。 */
async function loadTasks() {
  const res = await api.tasks();
  return res.ok
    ? { ok: true, tasks: res.data.tasks || [], error: null }
    : { ok: false, tasks: [], error: res.error.message };
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
  if (id === store.get().liveTaskId && d.status !== 'succeeded' && d.status !== 'failed' && !pollTimer) pollTimer = setInterval(() => tick(id), 3000);
  await loadResult(id);
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
(async function resumeLastTask() {
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
})();

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

router.start();

function readLlm() {
  try { return JSON.parse(localStorage.getItem('mlgov.llm') || 'null') || defaults(); }
  catch { return defaults(); }
  function defaults() { return { enabled: false, api_base: '', api_key: '', model: '', mode: 'on' }; }
}
function readRailWidth() {
  const v = Number(localStorage.getItem('mlgov.railWidth'));
  return Number.isFinite(v) && v >= 280 && v <= 720 ? v : 380;
}
