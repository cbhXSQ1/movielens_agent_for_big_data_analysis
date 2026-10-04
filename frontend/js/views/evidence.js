/* 样例与报告。报告可打印（@media print 只输出报告区）。 */
import { el, clear } from '../core/dom.js';
import { renderTable } from '../ui/table.js';
import { renderReport, linkifyNumbers } from '../core/report.js';
import { renderState, loadingAfter } from '../ui/state.js';
import { panel } from '../ui/panel.js';
import { dimZh } from '../core/format.js';
import { bootGuide, runningNote } from '../core/run-state.js';

const N_CHOICES = [5, 20, 50];

/* 「还没有任何可展示的取数结果」的哨兵。必须是一个**真实任务号不可能等于**的值：
   store 初始的 `task.id` 是 null，若拿 null 播种，首次 update 就会命中守卫直接返回 ——
   空态与「—」占位永远画不出来。种成 `null` 时 `!== null` 为真，第一次一定会走到下面。 */
const NO_TASK = Symbol('no-task');

export default {
  id: 'evidence', title: '证据', order: 40,
  css: 'css/views/evidence.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 2h7l3 3v9H3z M10 2v3h3" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root; this.ctx = ctx; this.kind = 'quarantine'; this.table = 'ratings'; this.n = 20;
    this.loadedTaskId = NO_TASK;   // 已经取过样例与报告的任务号（见 update 的守卫）
    clear(root);

    const p1 = panel({ title: '样例' });
    const ctrl = el('div', 'seg');
    this.ctrl = ctrl;
    p1.head.querySelector('.panel__aside')?.appendChild(ctrl);
    this.tableHost = p1.body;
    root.appendChild(p1.root);

    const p2 = panel({ title: '评估报告' });
    this.reportHost = p2.body;
    const btn = el('button', 'btn btn--ghost btn--sm', '打印 / 存 PDF');
    btn.type = 'button';
    /* 裁定 R54：报告没加载成功之前不让点 —— 无报告时 @media print 会把所有 panel 藏掉，
       点了只会得到白页（实测可见 panel = 0）。 */
    btn.disabled = true;
    btn.addEventListener('click', () => window.print());
    this.printBtn = btn;
    p2.head.querySelector('.panel__aside')?.appendChild(btn);
    root.appendChild(p2.root);

    buildSeg(ctrl, this);

    /* 裁定 R29/R12：首次导航只 mount 不派 update，渲染数据的视图必须自绘一次。 */
    this.update(ctx.store.get());
  },

  update(state) {
    const s = this.ctx.store.get();
    /* 裁定 R16/R64：`ctrl.hidden` 每次 update 都要判，且必须在守卫**之前** ——
       result 是在同一个任务号下才落地的（任务号先写、await loadResult 后到），
       放进守卫里的话控件就永远解不开 hidden（G10 的 5/20/50 三个数字也就永远藏着）。
       它是纯赋值、不碰 DOM 内容，所以让 15s 的健康轮询白跑一次也无所谓。 */
    this.ctrl.hidden = !s.result;
    /* 裁定 R63：15s 的 /health 轮询每次都会造一个新的 health 对象，store 的浅比较挡不住它，
       router 于是每 15s（任务在跑时每 3s）调一次 update。样例与报告按**任务号 + 状态**认一次
       就够 —— 否则每 15s 重取并 clear 一次报告，三页报告会可见地闪、滚动位置回顶、
       打印按钮重禁用。裁定 R64：守卫只挡「重取 + 重画」，上面那次 ctrl.hidden 与
       下面的空态都不归它管。
       T5 新增的「任务没跑完」分支必须算进 key 里：只认任务号的话，同一个任务号从
       running 走到 succeeded 时守卫会拦下重绘，运行中的说明就永远留在屏幕上
       （正是 §4.6 里"终态没有回到页面"那一类）。 */
    const running = runningNote(s.task, '这里可以浏览隔离区与清洗后的样例，并打印评估报告。');
    const key = taskKey(s);
    if (this.loadedTaskId === key) return;
    this.loadedTaskId = key;
    /* 换任务 / 换状态：先清掉上一轮的样例与报告，并把打印按钮收回禁用 ——
       不等取数回来再清，否则切任务的一瞬间屏上是上一个任务的表（裁定 R16/R54/R63）。 */
    clear(this.tableHost);
    clear(this.reportHost);
    this.printBtn.disabled = true;
    if (running) {
      /* T5：任务没跑完时两个面板原本各一个「—」，什么也不说。
         说明只写在「样例」这块 —— 同一句话在两块里各印一遍是噪音。 */
      this.tableHost.appendChild(el('p', 'running', running));
      this.reportHost.appendChild(el('p', 'void', '—'));
      return;
    }
    /* 裁定 R16/R64：没有任务时不取数（取数只会拿到空态或错误，把「—」换成别的），
       但两个面板都要**画一次**占位符 —— 空态是被守卫放行的第一次 update 画的。
       T2 第 2 点：只有"没有任务**且**没有结果"时才换成首屏引导那一句。 */
    if (!s.task.id) {
      const guide = bootGuide(s, s.task);
      this.tableHost.appendChild(el('p', guide ? 'guide' : 'void', guide || '—'));
      this.reportHost.appendChild(el('p', 'void', '—'));
      return;
    }
    this.loadSamples();
    this.loadReport();
  },

  async loadSamples() {
    const s = this.ctx.store.get();
    const key = taskKey(s);
    /* 裁定 R62：本地取数常在 300ms 内落地，无条件骨架就是 spec §5.7 禁止的闪烁。 */
    const stopLoading = loadingAfter(this.tableHost);
    try {
      const res = await this.ctx.api.samples({ taskId: s.task.id, type: this.kind, table: this.table, n: this.n });
      /* 结果回来时用户还在看同一个任务（且它还是同一个状态）吗？不在就把这次结果丢掉：
         否则切走之后回来的旧响应会盖掉新任务的表 —— 同 R61 的「已经在路上的那次 /status」。
         丢掉时也重画一次运行中说明：骨架可能已经上屏（>300ms），留着它切回来就是幽灵加载态。 */
      if (this.loadedTaskId !== taskKey(this.ctx.store.get())) {
        const now = runningNote(this.ctx.store.get().task, '这里可以浏览隔离区与清洗后的样例，并打印评估报告。');
        if (now) { clear(this.tableHost); this.tableHost.appendChild(el('p', 'running', now)); }
        return;
      }
      clear(this.tableHost);
      if (!res.ok) { renderState(this.tableHost, { kind: 'error', title: '取不到样例', body: res.error.message }); return; }

      const data = res.data.samples || [];
      /* 关键：quarantine 用 line_no/rule_id；cleaned 只有 line，按 type 分支（spec §7.3） */
      const columns = this.kind === 'quarantine'
        ? [{ key: 'line_no', label: '行号', align: 'right', mono: true },
           { key: 'raw_line', label: '原始行', mono: true },
           { key: 'rule_id', label: '规则', mono: true },
           { key: 'reason', label: '原因' }]
        : [{ key: 'line', label: '行号', align: 'right', mono: true },
           { key: 'raw_line', label: '原始行', mono: true }];

      renderTable(this.tableHost, { columns, rows: data,
        caption: `${this.kind === 'quarantine' ? '隔离区' : '清洗后'}样例`,
        emptyText: '没有记录。' });
    } finally {
      stopLoading();
    }
  },

  async loadReport() {
    const s = this.ctx.store.get();
    if (!s.task.id) { clear(this.reportHost); this.reportHost.appendChild(el('p', 'void', '—')); return; }
    /* 裁定 R62：骨架必须挂在 await 之前那次 clear 之后 —— 下面成功分支还有一次
       `clear(this.reportHost)`，它跑在 await 之后，正好把骨架换成报告；失败分支同理。 */
    const stopLoading = loadingAfter(this.reportHost);
    try {
      const res = await this.ctx.api.reportText(s.task.id);
      this.printBtn.disabled = !res.ok;
      clear(this.reportHost);
      if (!res.ok) { renderState(this.reportHost, { kind: 'error', title: '取不到报告', body: res.error.message }); return; }
      const box = el('article', 'report prose');
      box.innerHTML = linkifyNumbers(renderReport(res.text), gotoRules(s));   // 已先转义再替换
      box.addEventListener('click', e => {
        const a = e.target.closest('a[data-goto]');
        if (!a) return;
        e.preventDefault();
        /* spec §4.4 D3：跳转**并高亮**对应卡片 —— 目标卡片用 hl 指认，由目标视图自己高亮。 */
        this.ctx.go(a.dataset.goto, { hl: a.dataset.hl });
      });
      this.reportHost.appendChild(box);
    } finally {
      stopLoading();
    }
  },
};

/* D3（spec §4.4）：综合分 → #/overview、五维任一项 → #/scores、输入行数/隔离数 → #/cleaning。
   裁定 R51：目标表在这里现算并传给 linkifyNumbers，核心模块里不留任何领域词（G14）。
   维度来自 store（`scores.before` 的键），报告里印的是维度 id（`| Accurate | 97.71 | …`），
   界面看的是中文名，所以 id 与 dimZh(id) 都当别名 —— 只写中文名的话真报告一个都命中不了。
   窗口沿用上一轮实测调宽的 `{0,16}`：「隔离总数：**100830**」渲染后关键词与数字之间隔 11 个字符，
   brief 的 `{0,10}` 收不到它（本轮实测：词规则命中 0 → 2）。 */
function gotoRules(state) {
  const before = state.result && state.result.scores && state.result.scores.before;
  const dims = before ? Object.keys(before).filter(k => k !== 'composite') : [];
  /* hl 是目标视图里那张卡片的 `data-hl`（spec §4.4 D3 的"高亮对应卡片"）：
     综合分 → 总览的综合分 KPI、五维 → 该维度那一行、行数 → 数据量变化面板。 */
  const rules = [
    { re: /(?:综合(?:质量分|分)?|composite)[^0-9]{0,16}(\d+\.\d+)/g, target: 'overview', hl: 'composite' },
    { re: /隔离[^0-9]{0,16}([\d,]{3,})/g, target: 'cleaning', hl: 'volumes' },
    { re: /输入[^0-9]{0,16}([\d,]{3,})/g, target: 'cleaning', hl: 'volumes' },
  ];
  for (const k of dims) {
    const zh = dimZh(k);
    rules.push({ re: new RegExp(`(?:${re0(zh)}|${re0(k)})[^0-9]{0,16}(\\d+\\.\\d+)`, 'g'), target: 'scores', hl: k });
  }
  return rules;
}

function re0(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

/* 取数落地时要用的 key：与 update 里那个守卫同一份算法（任务号 + 是否还没跑完）。
   只有它和自己相等时才把结果画上去，见 loadSamples 的第一行。 */
function taskKey(s) {
  const running = runningNote(s.task, '');
  return `${s.task.id}|${running ? 'running' : 'settled'}`;
}

function buildSeg(host, view) {
  clear(host);
  for (const [kind, label] of [['quarantine', '隔离区'], ['cleaned', '清洗后']]) {
    const b = el('button', 'seg__btn', label);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(view.kind === kind));
    b.addEventListener('click', () => { view.kind = kind; buildSeg(host, view); view.loadSamples(); });
    host.appendChild(b);
  }
  for (const n of N_CHOICES) {
    const b = el('button', 'seg__btn num', String(n));
    b.type = 'button';
    b.setAttribute('aria-pressed', String(view.n === n));
    b.addEventListener('click', () => { view.n = n; buildSeg(host, view); view.loadSamples(); });
    host.appendChild(b);
  }
}
