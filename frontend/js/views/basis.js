/* 依据：术语与口径的唯一容身处（spec §4.5 白名单）。
   规则 / 权重 / 指标名全部从 config 读，不硬编码。 */
import { el, clear } from '../core/dom.js';
import { panel } from '../ui/panel.js';
import { renderState } from '../ui/state.js';

export default {
  id: 'basis', title: '依据', order: 50,
  css: 'css/views/basis.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 2h8v12H4z M6 5h4 M6 8h4 M6 11h2" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root; this.ctx = ctx;
    clear(root);

    const pDim = panel({ title: '维度与指标' }); this.dims = pDim.body; root.appendChild(pDim.root);
    const pRule = panel({ title: '清洗规则清单' }); this.rules = pRule.body; root.appendChild(pRule.root);
    const pNote = panel({ title: '口径差异说明' }); this.notes = pNote.body; root.appendChild(pNote.root);
    const pBound = panel({ title: '时间边界与版本' }); this.bounds = pBound.body; root.appendChild(pBound.root);

    this.load();
    this.update(ctx.store.get());
  },

  async load() {
    const s = this.ctx.store.get();
    const [scoring, rules] = await Promise.all([this.ctx.config.loadScoring(), this.ctx.config.loadRules()]);
    this.scoring = scoring.ok ? scoring.data : null;
    this.ruleCfg = rules.ok ? rules.data : null;
    this.cfgError = scoring.ok ? null : scoring.error.message;
    this.update(this.ctx.store.get());
  },

  update(state) {
    const cfg = this.scoring || state.scoringCfg;
    clear(this.dims);
    if (!cfg) {
      renderState(this.dims, { kind: 'error', title: '读不到评分方案', body: this.cfgError || '正在读取…' });
    } else {
      for (const dim of cfg.dimensions || []) {
        const box = el('div', 'basis-dim');
        const h = el('div', 'basis-dim__head');
        h.appendChild(el('span', 'basis-dim__name', dim.name_zh || dim.id));
        h.appendChild(el('span', 'basis-dim__weight num', dim.weight == null ? '—' : String(dim.weight)));
        box.appendChild(h);
        if (dim.definition) box.appendChild(el('p', 'basis-dim__def', dim.definition));
        const ul = el('ul', 'basis-dim__metrics');
        for (const m of dim.metrics || []) {
          const li = el('li', 'basis-metric');
          li.appendChild(el('span', 'basis-metric__id mono', m.id));
          li.appendChild(el('span', 'basis-metric__name', m.name || ''));
          if (m.enabled === false) li.appendChild(el('span', 'badge badge--muted', '本次不参与'));
          if (Array.isArray(m.unverifiable) && m.unverifiable.length) {
            li.appendChild(el('span', 'basis-metric__unver', '无法验证：' + m.unverifiable.join('、')));
          }
          ul.appendChild(li);
        }
        box.appendChild(ul);
        this.dims.appendChild(box);
      }
    }

    clear(this.rules);
    const doc = this.ruleCfg;
    if (!doc) { renderState(this.rules, { kind: 'error', title: '读不到清洗方案', body: '正在读取…' }); }
    else {
      const byId = {};
      for (const r of doc.rules || []) byId[r.id] = r;
      for (const stage of doc.pipeline || []) {
        const box = el('div', 'basis-stage');
        box.appendChild(el('h3', 'basis-stage__name', `${stage.stage} · ${(stage.rules || []).length} 条`));
        const ul = el('ul', null);
        for (const id of stage.rules || []) {
          const r = byId[id];
          if (!r) continue;
          ul.appendChild(el('li', null, `${r.id} ${r.name || ''} —— ${r.description || r.action || ''}`));
        }
        box.appendChild(ul);
        this.rules.appendChild(box);
      }
    }

    clear(this.notes);
    const rn = state.result && state.result.ruleNotes;
    if (!rn) { this.notes.appendChild(el('p', 'void', '本次没有需要说明的差异。')); }
    else {
      this.notes.appendChild(el('p', 'prose', rn.headline || ''));
      const ul = el('ul', 'prose');
      for (const n of rn.notes || []) {
        ul.appendChild(el('li', null, `${n.rule_id} ${n.name || ''}：${n.why_different || ''}`));
      }
      this.notes.appendChild(ul);
    }

    clear(this.bounds);
    const tb = state.result && state.result.timeBoundaries;
    const v = state.result && state.result.versions;
    this.bounds.appendChild(kv('T1 训练期截止', tb ? tb.T1 : '—'));
    this.bounds.appendChild(kv('T2 验证期截止', tb ? tb.T2 : '—'));
    this.bounds.appendChild(kv('数据版本', state.result ? state.result.dataVersion : '—'));
    this.bounds.appendChild(kv('清洗规则版本', v && v.rule ? `${v.rule.version}（sha256 ${String(v.rule.sha256).slice(0, 12)}）` : '—'));
    this.bounds.appendChild(kv('评分方案版本', v && v.scoring ? `${v.scoring.version}（sha256 ${String(v.scoring.sha256).slice(0, 12)}）` : '—'));
    this.bounds.appendChild(kv('策略版本', v && v.policy ? String(v.policy.sha256).slice(0, 12) : '—'));
  },
};

function kv(k, v) {
  const row = el('div', 'kv');
  row.appendChild(el('span', 'kv__k', k));
  row.appendChild(el('span', 'kv__v mono', v));
  return row;
}
