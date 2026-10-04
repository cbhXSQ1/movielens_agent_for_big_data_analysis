import { createStore } from './core/store.js';
import { createApi } from './core/api.js';
import { createConfig } from './core/config.js';
import { createRouter } from './shell/router.js';
import { createTaskbar } from './shell/taskbar.js';
import { createComposer } from './shell/composer.js';
import { createTasks } from './shell/tasks.js';
import { createBanners } from './shell/banners.js';
import { createTimeline } from './shell/timeline.js';
import { createSplitter } from './shell/splitter.js';
import { createSettings } from './shell/settings.js';
import { createTopbar } from './shell/topbar.js';

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
window.__APP__ = { ...ctx, views: VIEWS };   // 仅调试用；生产逻辑不依赖它（验收脚本按 id 取视图）

const router = createRouter({
  views: VIEWS,
  navHost: document.getElementById('nav'),
  viewHost: document.getElementById('view-host'),
  store,
  ctx,
});

/* 任务会话（轮询 / 切任务 / 新任务 / 刷新接回）在 `shell/tasks.js`：`main.js` 贴着
   §10.13 的 250 行上限，任务 14 报告 §10.1 已点名这个落点。 */
const tasks = createTasks({ store, api });

const taskbar = createTaskbar({
  host: document.getElementById('taskbar'),
  store,
  onPickTask: tasks.showTask,    // 裁定 R53：不再是空壳
  onNewTask: () => { tasks.startNewTask(); composer.focusInput(); },   // §1.4：只清"正在看的"，不启动
  loadTasks: tasks.loadTasks,    // 首次展开「历史任务」时拉 /api/tasks
  isBusy: tasks.isBusy,          // F1：「新任务」与发送键同一判据
});
const composer = createComposer({
  form: document.getElementById('composer'),
  store, api,
  onSent: entry => {
    store.set(s => ({ timeline: [...s.timeline, entry] }));
    if (entry.taskId && entry.taskStarted) tasks.startPolling(entry.taskId, entry.opts);
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

/* 裁定 R16/R63：顶栏的后端状态点与「数据版本」chip 各自一个订阅 —— 这块搬进
   `shell/topbar.js`，本轮加「数据版本」的生产者时 main.js 已经贴着 §10.13 的 250 行上限。 */
createTopbar({ store }).start();

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

router.start();

/* spec §6.5、§11-R3：刷新后接回任务。放在 `router.start()` 之后 —— 与搬迁前一致，
   首个视图已经 mount 过一轮，接回来的任务照常走 store 订阅重绘。 */
tasks.resumeLastTask();

function readLlm() {
  try { return JSON.parse(localStorage.getItem('mlgov.llm') || 'null') || defaults(); }
  catch { return defaults(); }
  function defaults() { return { enabled: false, api_base: '', api_key: '', model: '', mode: 'on' }; }
}
function readRailWidth() {
  const v = Number(localStorage.getItem('mlgov.railWidth'));
  return Number.isFinite(v) && v >= 280 && v <= 720 ? v : 380;
}
