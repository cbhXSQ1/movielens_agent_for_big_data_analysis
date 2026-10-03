/* 样例与报告。报告可打印（@media print 只输出报告区）。 */
import { el, clear } from '../core/dom.js';
import { renderTable } from '../ui/table.js';
import { renderReport, linkifyNumbers } from '../core/report.js';
import { renderState } from '../ui/state.js';
import { panel } from '../ui/panel.js';
import { dimZh } from '../core/format.js';

const N_CHOICES = [5, 20, 50];

export default {
  id: 'evidence', title: '证据', order: 40,
  css: 'css/views/evidence.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 2h7l3 3v9H3z M10 2v3h3" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root; this.ctx = ctx; this.kind = 'quarantine'; this.table = 'ratings'; this.n = 20;
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
    const state$ = this.ctx.store.get();
    /* 裁定 R16：G10 要求「任务未成功时页面上的数字个数 = 0」，而条数选择项自带 5/20/50
       三个数字（还是 `.num` 节点）。没有结果时把整条控件收起来，有结果时照常显示。 */
    this.ctrl.hidden = !state$.result;
    clear(this.tableHost);
    if (!state$.task.id) { this.tableHost.appendChild(el('p', 'void', '—')); }
    else this.loadSamples();
    this.loadReport();
  },

  async loadSamples() {
    const s = this.ctx.store.get();
    const res = await this.ctx.api.samples({ taskId: s.task.id, type: this.kind, table: this.table, n: this.n });
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
  },

  async loadReport() {
    const s = this.ctx.store.get();
    clear(this.reportHost);
    /* 裁定 R54：每次取报告前先禁掉，取到了才放开（失败 / 没有任务时保持禁用）。 */
    this.printBtn.disabled = true;
    if (!s.task.id) { this.reportHost.appendChild(el('p', 'void', '—')); return; }
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
      this.ctx.go(a.dataset.goto);
    });
    this.reportHost.appendChild(box);
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
  const rules = [
    { re: /(?:综合(?:质量分|分)?|composite)[^0-9]{0,16}(\d+\.\d+)/g, target: 'overview' },
    { re: /隔离[^0-9]{0,16}([\d,]{3,})/g, target: 'cleaning' },
    { re: /输入[^0-9]{0,16}([\d,]{3,})/g, target: 'cleaning' },
  ];
  for (const k of dims) {
    const zh = dimZh(k);
    rules.push({ re: new RegExp(`(?:${re0(zh)}|${re0(k)})[^0-9]{0,16}(\\d+\\.\\d+)`, 'g'), target: 'scores' });
  }
  return rules;
}

function re0(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

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
