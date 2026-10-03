import { createStore } from './core/store.js';
import { createApi } from './core/api.js';
import { createConfig } from './core/config.js';
import { createRouter } from './shell/router.js';

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
