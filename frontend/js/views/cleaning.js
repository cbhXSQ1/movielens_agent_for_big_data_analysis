/* 清洗结果。红线 R3：修复 / 去重 / 隔离三张独立卡，绝不合并；顶部常驻"隔离 ≠ 修复"。 */
import { el, clear } from '../core/dom.js';
import { int } from '../core/format.js';
import { panel } from '../ui/panel.js';
import { renderTable, sortRows, filterByRule } from '../ui/table.js';

export default {
  id: 'cleaning', title: '清洗', order: 30,
  css: 'css/views/cleaning.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2 4h12M2 8h8M2 12h5" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root; this.ctx = ctx;
    this.ruleFilter = null; this.samples = []; this.samplesMeta = null;
    clear(root);

    this.note = el('p', 'notice', '');
    root.appendChild(this.note);

    const pFlow = panel({ title: '输入 → 输出' }); this.flow = pFlow.body; root.appendChild(pFlow.root);

    const trio = el('div', 'trio');
    for (const [key, title, sub] of [
      ['fix', '修复', '改写值，保留在正表'],
      ['dedupe', '去重', '同一业务键保留一条'],
      ['quarantine', '隔离', '移出正表，可回溯'],
    ]) {
      const p = panel({ title, note: sub });
      this[key] = p.body;
      trio.appendChild(p.root);
    }
    root.appendChild(trio);

    const pRules = panel({ title: '按规则命中', note: '点一行看它的记录' });
    this.rules = pRules.body;
    root.appendChild(pRules.root);

    /* 裁定 R47：aside 现在常驻，这里的 prepend 不再被 `?.` 静默吞掉。 */
    const pSamp = panel({ title: '被隔离的记录' });
    this.sampNote = el('span', 'panel__note', '');
    pSamp.head.querySelector('.panel__aside')?.prepend(this.sampNote);
    this.samplesHost = pSamp.body;
    root.appendChild(pSamp.root);

    /* 裁定 R29/R12：首次导航只 mount 不派 update，渲染数据的视图必须自绘一次。 */
    this.update(ctx.store.get());
  },

  update(state) {
    const r = state.result;
    const redline = el('strong', null, '隔离不等于修复');
    this.note.textContent = '';
    this.note.appendChild(redline);
    this.note.appendChild(document.createTextNode(
      '：被隔离的记录是移出正表，不是被改对了。分数提升有一部分来自分母变小。'));

    for (const key of ['flow', 'fix', 'dedupe', 'quarantine', 'rules', 'samplesHost']) {
      clear(this[key]);
      this[key].appendChild(el('p', 'void', '—'));
    }
    if (!r) return;

    /* 首次进入本视图、以及换了任务时，把隔离记录拉进来（否则记录表永远是空的、或者是上一个任务的）。
       裁定 R16：brief 只判「samples 为空」，实测把 task.id 换成另一个任务之后，
       记录表仍是上一个任务的 50 行（performance 里 /samples 请求数 11 → 11，没有重新取），
       而上面三张卡已经换成新任务的数字 —— 同一屏混了两次运行的数据。最小修正：认住 taskId。 */
    if ((!this.samples || this.samples.length === 0 || this.samplesTaskId !== state.task.id) && !this.loadingSamples) {
      this.loadingSamples = true;
      this.samplesTaskId = state.task.id;
      this.loadSamples().finally(() => { this.loadingSamples = false; });
    }

    const c = r.counts || {};
    const fixTotal = Object.values(c.fix || {}).reduce((a, b) => a + (Number(b) || 0), 0);
    const dedupeTotal = Object.values(c.dedupe || {}).reduce((a, b) => a + (Number(b) || 0), 0);

    clear(this.flow);
    this.flow.appendChild(kv('评分表', `${int(c.input && c.input.ratings_lines)} → ${int(c.output && c.output.ratings)}`));
    this.flow.appendChild(kv('用户表', `${int(c.input && c.input.users_lines)} → ${int(c.output && c.output.users)}`));
    this.flow.appendChild(kv('电影表', `${int(c.input && c.input.movies_lines)} → ${int(c.output && c.output.movies)}`));

    clear(this.fix);
    for (const [k, v] of Object.entries(c.fix || {})) this.fix.appendChild(kv(k, int(v)));
    if (fixTotal === 0) this.fix.appendChild(el('p', 'void', '本次没有修复计数'));

    clear(this.dedupe);
    for (const [k, v] of Object.entries(c.dedupe || {})) this.dedupe.appendChild(kv(k, int(v)));
    if (dedupeTotal === 0) this.dedupe.appendChild(el('p', 'void', '本次没有去重计数'));

    clear(this.quarantine);
    this.quarantine.appendChild(kv('总计', int(c.quarantine && c.quarantine.total)));
    for (const [k, v] of Object.entries((c.quarantine && c.quarantine.by_rule) || {})) {
      this.quarantine.appendChild(kv(k, int(v)));
    }

    clear(this.rules);
    const byRule = (c.quarantine && c.quarantine.by_rule) || {};
    const rows = Object.entries(byRule).map(([rule_id, n]) => ({ rule_id, n }))
      .sort((a, b) => b.n - a.n);
    if (rows.length === 0) this.rules.appendChild(el('p', 'void', '—'));
    else {
      const list = el('div', 'rulelist');
      for (const row of rows) {
        const b = el('button', 'rulelist__row', '');
        b.type = 'button';
        b.setAttribute('aria-pressed', String(this.ruleFilter === row.rule_id));
        b.appendChild(el('span', 'rulelist__id mono', row.rule_id));
        b.appendChild(el('span', 'rulelist__n num', int(row.n)));
        b.addEventListener('click', () => {
          this.ruleFilter = this.ruleFilter === row.rule_id ? null : row.rule_id;
          /* 裁定 R16：brief 这里只调 loadSamples()，于是重绘的是记录表，
             而规则行的 aria-pressed 停在建成时算出来的值 —— 实测点完 R2 之后
             10 个规则行全是 aria-pressed="false"，选中样式永远不亮。
             改调 update()：重算一遍（不重新取数，samples 非空）就把状态带上了。 */
          this.update(this.ctx.store.get());
        });
        list.appendChild(b);
      }
      this.rules.appendChild(list);
    }
    this.renderSamples();
  },

  async loadSamples() {
    const state = this.ctx.store.get();
    if (!state.task.id) return;
    const res = await this.ctx.api.samples({ taskId: state.task.id, type: 'quarantine', table: 'ratings', n: 50 });
    this.samples = res.ok ? (res.data.samples || []) : [];
    this.samplesMeta = res.ok ? { total: res.data.total_available, error: null } : { error: res.error.message };
    this.renderSamples();
  },

  renderSamples() {
    clear(this.samplesHost);
    if (!this.samples || this.samples.length === 0) {
      this.sampNote.textContent = '';
      this.samplesHost.appendChild(el('p', 'void', '—'));
      return;
    }
    const rows = filterByRule(this.samples, this.ruleFilter);
    this.sampNote.textContent = this.ruleFilter
      ? `只看 ${this.ruleFilter} · ${rows.length} 条 · 点规则可清除`
      : `共 ${this.samplesMeta && this.samplesMeta.total != null ? int(this.samplesMeta.total) : '—'} 行`;

    renderTable(this.samplesHost, {
      caption: '被隔离的评分记录',
      emptyText: this.ruleFilter ? `这个规则本次没有记录。` : '没有记录。',
      columns: [
        { key: 'line_no', label: '行号', align: 'right', mono: true },
        { key: 'raw_line', label: '原始行', mono: true },
        { key: 'rule_id', label: '规则', mono: true },
        { key: 'stage', label: '阶段' },
        { key: 'reason', label: '原因' },
      ],
      rows: sortRows(rows, 'line_no', 'asc'),
    });
  },
};

function kv(k, v) {
  const row = el('div', 'kv');
  row.appendChild(el('span', 'kv__k', k));
  row.appendChild(el('span', 'kv__v num', v));
  return row;
}
