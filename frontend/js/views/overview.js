/* 总览：一眼看结论。红线 R4 —— 综合分与数据量变化必须同屏。 */
import { el, clear } from '../core/dom.js';
import { int, fixed, duration, pctPart, DASH } from '../core/format.js';
import { panel } from '../ui/panel.js';
import { kpi } from '../ui/kpi.js';
import { renderState } from '../ui/state.js';
import { highlightKey } from '../ui/highlight.js';

export default {
  id: 'overview', title: '总览', order: 10,
  css: 'css/views/overview.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="2" y="2" width="5" height="12" fill="none" stroke="currentColor" stroke-width="1.7"/><rect x="9" y="2" width="5" height="7" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    clear(root);

    this.kpis = el('div', 'kpis');
    root.appendChild(this.kpis);

    const pVol = panel({ title: '数据量变化', note: '灰 = 清洗前 · 绿 = 清洗后' });
    this.volumes = pVol.body;
    root.appendChild(pVol.root);

    const pExplain = panel({ title: 'Agent 的说明' });
    this.explain = pExplain.body;
    root.appendChild(pExplain.root);

    const pLim = panel({ title: '评价局限' });
    this.limits = pLim.body;
    root.appendChild(pLim.root);

    /* 裁定 R29/R12：首次导航只 mount 不派 update，渲染数据的视图必须自绘一次。 */
    this.update(ctx.store.get());
  },

  update(state) {
    const r = state.result;
    clear(this.kpis);
    clear(this.volumes);
    clear(this.explain);
    clear(this.limits);

    /* 红线 R1：任务没成功时一个数字都不显示 */
    if (!r) {
      /* spec §5.7：取数失败要说出原因，不能折叠成空态（三个「—」）。
         `resultError` 由 main.js 的 loadResult 在 !res.ok 时写入。 */
      const fail = state.resultError;
      const put = host => (fail
        ? renderState(host, { kind: 'error', title: '取不到任务结果', body: fail.message })
        : host.appendChild(el('p', 'void', '—')));
      put(this.volumes); put(this.explain); put(this.limits);
      return;
    }

    const s = r.scores || {};
    const before = s.before || {};
    const after = s.after || {};
    const delta = s.delta || {};
    const c = (r.counts && r.counts.input) || {};
    const out = (r.counts && r.counts.output) || {};
    const q = (r.counts && r.counts.quarantine) || {};
    const dedupe = (r.counts && r.counts.dedupe) || {};
    const fix = (r.counts && r.counts.fix) || {};
    const fixTotal = Object.values(fix).reduce((a, b) => a + (Number(b) || 0), 0);
    /* 用时 = started_at → updated_at（终态即完成时刻），不是任务年龄。 */
    const t = state.task || {};
    const endAt = t.finishedAt || Date.now();
    const secs = t.startedAt ? (endAt - t.startedAt) / 1000 : null;

    this.kpis.appendChild(kpi({ label: '综合质量分', value: fixed(after.composite, 2), tone: 'accent',
      sub: `清洗前 ${fixed(before.composite, 2)} · +${fixed(delta.composite, 2)}`,
      hl: 'composite' }));
    this.kpis.appendChild(kpi({ label: '输入评分行', value: int(c.ratings_lines),
      sub: `输出 ${int(out.ratings)} 行` }));
    this.kpis.appendChild(kpi({ label: '隔离', value: int(q.total),
      sub: `去重 ${int(dedupe.ratings)} · 修复 ${int(fixTotal)}` }));
    this.kpis.appendChild(kpi({ label: '用时', value: duration(secs),
      sub: `${t.stageTotal || 9} 个阶段` }));

    /* 红线 R4：数据量变化与综合分同屏 —— 所以它排在 Agent 的说明之前。 */
    for (const row of volumeRows(r)) this.volumes.appendChild(row);

    if (r.explanation) {
      const pre = el('pre', 'prose explain');
      pre.textContent = r.explanation;
      this.explain.appendChild(pre);
    } else {
      this.explain.appendChild(el('p', 'void', '—'));
    }

    const limits = r.limitations || [];
    if (limits.length === 0) {
      this.limits.appendChild(el('p', 'void', '—'));
    } else {
      const ul = el('ul', 'limits prose');
      for (const line of limits) ul.appendChild(el('li', null, line));
      this.limits.appendChild(ul);
    }

    /* D3（spec §4.4）：从报告点过来时 `#/overview?hl=composite` —— 高亮那一张卡片。 */
    highlightKey(this.root, state.viewParams && state.viewParams.hl);
  },
};

function volumeRows(r) {
  const c = r.counts || {};
  const rows = [
    ['评分', c.input && c.input.ratings_lines, c.output && c.output.ratings],
    ['用户', c.input && c.input.users_lines, c.output && c.output.users],
    ['电影', c.input && c.input.movies_lines, c.output && c.output.movies],
  ];
  return rows.map(([name, b, a]) => {
    const row = el('div', 'vol');
    row.dataset.hl = 'volumes';                    // D3 落点：报告里的「输入行数 / 隔离数」也算这块
    row.appendChild(el('span', 'vol__name', name));
    const bars = el('span', 'vol__bars');
    for (const [cls, v] of [['is-before', b], ['is-after', a]]) {
      const track = el('span', 'vol__track');
      const fill = el('span', `vol__fill vol__fill--${cls}`);
      fill.style.width = (cls === 'is-before' ? 100 : (b ? (a / b * 100) : 0)) + '%';
      track.appendChild(fill);
      bars.appendChild(track);
    }
    row.appendChild(bars);
    const nums = el('span', 'vol__nums');
    nums.appendChild(el('span', 'vol__before num', int(b)));
    nums.appendChild(el('span', 'vol__after num', int(a)));
    row.appendChild(nums);
    row.appendChild(el('span', 'vol__drop num', dropText(b, a)));
    return row;
  });
}

/* 裁定 R63：`pctPart` 缺失时返回 '—'，直接拼符号与百分号会得到「−—%」这种乱码。
   缺失就只渲染一个「—」（与 format.js 的口径一致），有值才带符号。 */
function dropText(b, a) {
  const p = pctPart(b, a);
  return p === DASH ? DASH : `−${p}%`;
}
