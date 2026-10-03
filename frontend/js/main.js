import { createStore } from './core/store.js';
import { createApi } from './core/api.js';
import { createConfig } from './core/config.js';
import { createRouter } from './shell/router.js';
import { createTaskbar } from './shell/taskbar.js';
import { createComposer } from './shell/composer.js';
import { createBanners } from './shell/banners.js';
import { createTimeline } from './shell/timeline.js';

import overview from './views/overview.js';
import scores from './views/scores.js';
import cleaning from './views/cleaning.js';
import evidence from './views/evidence.js';
import basis from './views/basis.js';

export const VIEWS = [overview, scores, cleaning, evidence, basis].sort((a, b) => a.order - b.order);

const store = createStore({
  health: { ok: null, llmSupported: false, llmConfigured: false },
  llm: readLlm(),
  task: { id: null, status: null, stage: null, stageIndex: 0, stageTotal: 9, percent: 0, startedAt: null, opts: {}, errors: [] },
  result: null,
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
  onPickTask: () => { /* 任务 13 实现列表弹层 */ },
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

function startPolling(taskId, opts) {
  try { localStorage.setItem('mlgov.lastTaskId', taskId); } catch { /* 隐私模式下忽略 */ }
  store.set({ task: { ...store.get().task, id: taskId, status: 'queued', opts: opts || {}, startedAt: Date.now() } });
  const tick = async () => {
    const res = await api.status(taskId);
    if (!res.ok) { store.set(s => ({ task: { ...s.task, errors: [...s.task.errors, res.error.message] } })); return; }
    const d = res.data;
    store.set(s => ({ task: { ...s.task, id: d.task_id, status: d.status, stage: d.stage,
      stageIndex: d.stage_index, stageTotal: d.stage_total, percent: d.progress_percent, errors: d.errors || [] } }));
    if (d.status === 'succeeded' || d.status === 'failed') { stop(); if (d.status === 'succeeded') await loadResult(taskId); }
  };
  const timer = setInterval(tick, 3000);
  function stop() { clearInterval(timer); }
  tick();
  return stop;
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
    store.set(s => ({ task: { ...s.task, id: last, status: 'succeeded', stage: d.stage,
      stageIndex: d.stage_index, stageTotal: d.stage_total, percent: d.progress_percent,
      startedAt: d.started_at ? Date.parse(d.started_at) : null, errors: d.errors || [] } }));
    await loadResult(last);
  }
})();

async function loadResult(taskId) {
  const res = await api.result(taskId, { explain: true });
  if (!res.ok) return;
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
