/* 五维。红线：缺失值不记 0；D2 —— 点维度过滤指标明细。 */
import { el, clear } from '../core/dom.js';
import { fixed, dimZh, metricName } from '../core/format.js';
import { panel } from '../ui/panel.js';
import { radarScale, radarSvg, axesFor, GEOM } from '../ui/radar.js';
import { deltaSegment } from '../ui/bars.js';
import { renderState } from '../ui/state.js';
import { highlightKey } from '../ui/highlight.js';

export default {
  id: 'scores', title: '五维', order: 20,
  css: 'css/views/scores.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 2 14 6.5v6L8 14 2 12.5v-6z" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root; this.ctx = ctx; this.dimFilter = null;
    clear(root);

    this.note = el('p', 'view__note', '');
    root.appendChild(this.note);

    const row = el('div', 'cols');
    /* 裁定 R47：`panel()` 的 `.panel__aside` 现在常驻，所以 `panel({title})` 之后
       直接 `head.querySelector('.panel__aside')` 就能拿到容器 —— 不需要 `note: ' '` 占位。 */
    const pRadar = panel({ title: '五维质量' });
    this.radarNote = pRadar.head.querySelector('.panel__aside') || pRadar.head;
    this.radar = pRadar.body;
    this.legend = el('div', 'legend');
    pRadar.root.appendChild(this.legend);
    row.appendChild(pRadar.root);

    const pDim = panel({ title: '维度得分与变化' });
    this.dims = pDim.body;
    row.appendChild(pDim.root);
    root.appendChild(row);

    const pMetrics = panel({ title: '指标明细' });
    this.metricsNote = pMetrics.head.querySelector('.panel__aside') || pMetrics.head;
    this.metrics = pMetrics.body;
    root.appendChild(pMetrics.root);

    /* 裁定 R29/R12：首次导航只 mount 不派 update，渲染数据的视图必须自绘一次。 */
    this.update(ctx.store.get());
  },

  update(state) {
    const r = state.result;
    const scoring = state.scoringCfg;
    this.note.textContent = '清洗前后为同一份评分方案。';
    /* 两侧说明的文字写在 `.panel__note` 里（字号/颜色靠这个类），所以每次都重建 span，
       而不是清空 aside 塞裸文本 —— 那样会掉到 13.5px 的默认正文档。 */
    const say = (host, text) => { clear(host); host.appendChild(el('span', 'panel__note', text)); };

    if (!r || !r.scores) {
      say(this.radarNote, '');                         // 空态不留下上一轮的刻度文字
      clear(this.legend);                              // R46：结果为空时图例不留残影
      /* spec §5.7：取不到 /result 时要给出原因，不能让四块地方各留一个「—」装作"没有数据"。 */
      const fail = state.resultError;
      if (fail) {
        for (const host of [this.radar, this.dims, this.metrics]) {
          renderState(host, { kind: 'error', title: '取不到任务结果', body: fail.message });
        }
        return;
      }
      this.radar.textContent = '';
      this.radar.appendChild(el('p', 'void', '—'));
      clear(this.dims); this.dims.appendChild(el('p', 'void', '—'));
      clear(this.metrics); this.metrics.appendChild(el('p', 'void', '—'));
      return;
    }

    const dimKeys = Object.keys(r.scores.before).filter(k => k !== 'composite');
    const before = dimKeys.map(k => r.scores.before[k]);
    const after = dimKeys.map(k => r.scores.after[k]);
    const scale = radarScale([...before, ...after]);
    const axes = axesFor(dimKeys);

    say(this.radarNote, scale.min === 85 ? '刻度 85–100' : '刻度 0–100');
    clear(this.radar);
    const box = el('div', 'radar');
    box.innerHTML = radarSvg({ axes, before, after, scale, geom: GEOM });   // 内容是自家生成的 SVG 字符串
    this.radar.appendChild(box);
    clear(this.legend);
    this.legend.appendChild(legendItem('清洗前', 'var(--viz-before)'));
    this.legend.appendChild(legendItem('清洗后', 'var(--viz-after)'));

    clear(this.dims);
    axes.forEach((ax, i) => {
      const seg = deltaSegment(before[i], after[i], scale);
      const row = el('div', 'dim');
      row.dataset.hl = ax.key;                       // D3 落点：报告里的五维值指到这一行
      if (this.dimFilter === ax.key) row.classList.add('is-active');
      const top = el('div', 'dim__top');
      const name = el('button', 'dim__name', dimZh(ax.key));
      name.type = 'button';
      name.setAttribute('aria-pressed', String(this.dimFilter === ax.key));
      name.addEventListener('click', () => {
        this.dimFilter = this.dimFilter === ax.key ? null : ax.key;
        this.update(this.ctx.store.get());
      });
      top.appendChild(name);
      top.appendChild(el('span', 'dim__vals num', `${fixed(before[i])} → ${fixed(after[i])}`));
      row.appendChild(top);

      const bar = el('div', 'dim__bar');
      const base = el('span', 'dim__base'); base.style.width = seg.basePct + '%';
      const grow = el('span', 'dim__grow'); grow.style.left = seg.basePct + '%'; grow.style.width = seg.widthPct + '%';
      bar.appendChild(base); bar.appendChild(grow);
      row.appendChild(bar);
      row.appendChild(el('span', 'dim__delta num', `+${fixed(r.scores.delta[ax.key])}`));
      this.dims.appendChild(row);
    });

    /* D2：点维度 → 指标明细只显示构成它的指标 */
    const metrics = (r.scores.metrics && r.scores.metrics.before) || {};
    const afterMetrics = (r.scores.metrics && r.scores.metrics.after) || {};
    let allowed = null;
    if (this.dimFilter && scoring) {
      const dim = (scoring.dimensions || []).find(d => d.id === this.dimFilter || d.name_zh === dimZh(this.dimFilter));
      if (dim) allowed = new Set((dim.metrics || []).map(m => m.id));
    }
    say(this.metricsNote, !scoring
      ? '评分方案没读到，暂时不能按维度筛选'
      : (this.dimFilter ? `只看${dimZh(this.dimFilter)} · 点维度名可清除` : '点维度名可只看它的指标'));

    clear(this.metrics);
    const ids = Object.keys(metrics).filter(id => !allowed || allowed.has(id));
    if (ids.length === 0) this.metrics.appendChild(el('p', 'void', '—'));
    else {
      const ul = el('ul', 'metrics');
      for (const id of ids) {
        const li = el('li', 'metric');
        li.appendChild(el('span', 'metric__id mono', id));
        /* 指标名从配置读（G14）；配置缺失时 metricName() 返回 id，此时不渲染名字列
           （否则与 ID 列重复成「A1 A1」），说明已经在 R44 里讲清楚了。 */
        const name = metricName(scoring, id);
        if (name && name !== id) li.appendChild(el('span', 'metric__name', name));
        li.appendChild(el('span', 'metric__before num', fixed(metrics[id])));
        li.appendChild(el('span', 'metric__after num', fixed(afterMetrics[id])));
        ul.appendChild(li);
      }
      this.metrics.appendChild(ul);
    }

    /* D3（spec §4.4）：从报告点过来时 `#/scores?hl=Accurate` —— 高亮那个维度行。 */
    highlightKey(this.root, state.viewParams && state.viewParams.hl);
  },
};

function legendItem(label, color) {
  const item = el('span', 'legend__item');
  const dot = el('span', 'legend__dot');
  dot.style.background = color;
  item.appendChild(dot);
  item.appendChild(el('span', null, label));
  return item;
}
