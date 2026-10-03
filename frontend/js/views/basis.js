/* 依据：术语与口径的唯一容身处（spec §4.5 白名单）。
   规则 / 权重 / 指标名全部从 config 读，不硬编码。 */
import { el, clear } from '../core/dom.js';
import { panel } from '../ui/panel.js';
import { renderState, loadingAfter } from '../ui/state.js';

export default {
  id: 'basis', title: '依据', order: 50,
  css: 'css/views/basis.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 2h8v12H4z M6 5h4 M6 8h4 M6 11h2" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root; this.ctx = ctx;
    this.cfgLoaded = false;          // 裁定 R62：配置还没回来之前，「没配置」不等于「读不到配置」
    clear(root);

    const pDim = panel({ title: '维度与指标' }); this.dims = pDim.body; root.appendChild(pDim.root);
    const pRule = panel({ title: '清洗规则清单' }); this.rules = pRule.body; root.appendChild(pRule.root);
    const pNote = panel({ title: '口径差异说明' }); this.notes = pNote.body; root.appendChild(pNote.root);
    const pBound = panel({ title: '时间边界与版本' }); this.bounds = pBound.body; root.appendChild(pBound.root);

    this.load();
    this.update(ctx.store.get());
  },

  async load() {
    /* 裁定 R62：两个面板各等一次同源配置（Promise.all），骨架按 300ms 分档挂到各自的宿主上；
       下面 `this.update()` 会把它们 clear 成真内容（或错误态），finally 里再收回计时器。 */
    const stopDims = loadingAfter(this.dims);
    const stopRules = loadingAfter(this.rules);
    try {
      const s = this.ctx.store.get();
      const [scoring, rules] = await Promise.all([this.ctx.config.loadScoring(), this.ctx.config.loadRules()]);
      this.scoring = scoring.ok ? scoring.data : null;
      this.ruleCfg = rules.ok ? rules.data : null;
      /* F-E：两个面板各报各的失败原因。合成一个 cfgError 会让清洗规则面板
         在只有它失败时说出另一半的原因（甚至说成「正在读取…」）。 */
      this.cfgError = scoring.ok ? null : scoring.error.message;
      this.rulesError = rules.ok ? null : rules.error.message;
      this.cfgLoaded = true;
      this.update(this.ctx.store.get());
    } finally {
      stopDims();
      stopRules();
    }
  },

  update(state) {
    const cfg = this.scoring || state.scoringCfg;
    clear(this.dims);
    if (!cfg) {
      /* 裁定 R62：首帧的「没配置」不是错误 —— 以前这里直接画错误态（标题「读不到评分方案」、
         正文「正在读取…」），于是每次进 #/basis 都先闪一下红字，`data-state` 还停在 "error"，
         配置明明加载成功了也一直是 "error"。0–300ms 不该给任何指示、更不该给错误：
         先给一个中性占位，超过 300ms 才由 loadingAfter 换成骨架，真失败时（cfgLoaded）才报错。 */
      if (this.cfgLoaded) renderState(this.dims, { kind: 'error', title: '读不到评分方案', body: this.cfgError || '没有更多信息。' });
      else this.dims.appendChild(el('p', 'void', '—'));
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
    if (!doc) {
      /* 同上：清洗方案清单的首帧同样只是「还没读到」，不是错误。
         F-E：正文必须是真实的失败原因，不能说成「正在读取…」（自己打自己的脸）。 */
      if (this.cfgLoaded) renderState(this.rules, { kind: 'error', title: '读不到清洗方案', body: this.rulesError || '没有更多信息。' });
      else this.rules.appendChild(el('p', 'void', '—'));
    } else {
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
    /* 裁定 R56：「没有差异」和「没有结果」是两件事。任务失败（result === null）时说成
       「本次没有需要说明的差异」，等于把"未产出"说成了"已核对过、无差异"（同 R49 一族）。 */
    const rn = state.result && state.result.ruleNotes;
    if (!state.result) this.notes.appendChild(el('p', 'void', '还没有任务结果。'));
    else if (!rn) this.notes.appendChild(el('p', 'void', '本次没有需要说明的差异。'));
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
