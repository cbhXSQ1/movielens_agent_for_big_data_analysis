/* 总览：一眼看结论。红线 R4 —— 综合分与数据量变化必须同屏。 */
import { el, clear } from '../core/dom.js';
import { int, fixed, duration, pctPart, DASH } from '../core/format.js';
import { panel } from '../ui/panel.js';
import { kpi } from '../ui/kpi.js';
import { renderState } from '../ui/state.js';
import { highlightKey } from '../ui/highlight.js';
import { bootGuide, runningNote } from '../core/run-state.js';

export default {
  id: 'overview', title: '总览', order: 10,
  css: 'css/views/overview.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="2" y="2" width="5" height="12" fill="none" stroke="currentColor" stroke-width="1.7"/><rect x="9" y="2" width="5" height="7" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    /* B5：折叠状态跨 update 保留 —— 15s 的健康轮询会重绘，每次都重建 <details> 的话
       用户展开的说明会自己收回去。 */
    this.explainOpen = false;
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
      /* T2 第 2 点 / T5：三种情况各一句，互斥。
         - 任务在跑（没结果）：说明完成后这里会出现什么（不含任何任务产出的数据）
         - 没有任务且没有结果：说一句怎么开始（T2）
         - 取数失败：说出真实原因（spec §5.7，错误不得折叠成空态）
         只写在「数据量变化」里：同一句话重复三遍是噪音，而 R50 只看数字、不看「—」，
         另两块保持空态的「—」，整页的占位规则不变。 */
      const note = runningNote(state.task, '这里会显示综合质量分、数据量变化和评价局限。');
      const guide = note ? '' : bootGuide(state, state.task);
      const both = (host, text, cls) => {
        clear(host);
        if (text) host.appendChild(el('p', cls, text));
        else if (state.resultError) {
          renderState(host, { kind: 'error', title: '取不到任务结果', body: state.resultError.message });
        } else host.appendChild(el('p', 'void', '—'));
      };
      /* 红线段先清干净：kpis 里的上一轮数字不能跨状态留下来（切任务时 store 的
         result 不一定同时变 null）。但**不放运行中说明** —— 说明写在「数据量变化」里
         （R50 量的正是 .panel__body），同一句话在 KPI 行里再出现一遍是噪音。 */
      both(this.volumes, note || guide, note ? 'running' : 'guide');
      both(this.explain, '', '');
      both(this.limits, '', '');
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

    /* T5：任务在 queued / running 时，五块地方原本只有一个「—」，看着像坏了。说明在上面的
       空分支里（运行中的任务没有结果）；这里不再重复插一次，避免同一句话出现两遍。 */

    /* 红线 R4：数据量变化与综合分同屏 —— 所以它排在 Agent 的说明之前。 */
    for (const row of volumeRows(r)) this.volumes.appendChild(row);

    /* B5：941 字的说明一整块会占掉近一屏，把「数据量变化」往下挤。默认折到 6 行 +
       「展开全部 / 收起」。
       裁定：**不用 `<details>`** —— 实测闭合的 `<details>` 有 `content-visibility: hidden`，
       里面的 6 行预览**根本不会被绘制**（几何量得到 128px，屏幕上一个字都没有；
       probe-explain 的对照实验见报告）。改成 button + hidden 的内容块：
       折叠态是真的渲染出来的预览，`aria-expanded` + `aria-controls` 把状态说明白。
       状态写在 this.explainOpen，15s 的健康轮询重绘不会把它收回去。
       这里**不改渲染顺序** —— R4 要的是分数与数据量同屏，说明始终排在它们之后。 */
    if (r.explanation) {
      const body = el('div', 'explain-box__body');
      body.id = 'explain-body';
      /* 折叠态**显示** 6 行预览，展开态显示全文 —— 用 `is-open` 这个类切换，
         **不用 `[hidden]`**：hidden 是"这个元素不渲染"的语义，展开时套上它
         整块说明就消失了（实测到过这一幕）。夹取只在没有 is-open 时生效。 */
      body.classList.toggle('is-open', !!this.explainOpen);
      const pre = el('pre', 'prose explain');
      pre.textContent = r.explanation;
      body.appendChild(pre);
      const btn = el('button', 'explain-box__toggle');
      btn.type = 'button';
      btn.setAttribute('aria-controls', 'explain-body');
      btn.addEventListener('click', () => {
        this.explainOpen = !this.explainOpen;
        paintToggle(btn, this.explainOpen);
        body.classList.toggle('is-open', this.explainOpen);
      });
      paintToggle(btn, this.explainOpen);
      this.explain.appendChild(btn);
      this.explain.appendChild(body);
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

/* B5：折叠控件的文案与 aria 状态一处写完（首次渲染与每次点击都走它）。
   折叠时是「展开全部」，展开时是「收起」；`aria-expanded` 让读屏软件也知道当前态。 */
function paintToggle(btn, open) {
  btn.textContent = open ? '▾ 收起' : '▸ 展开全部';
  btn.setAttribute('aria-expanded', String(!!open));
}

/* 裁定 R63：`pctPart` 缺失时返回 '—'，直接拼符号与百分号会得到「−—%」这种乱码。
   缺失就只渲染一个「—」（与 format.js 的口径一致），有值才带符号。 */
function dropText(b, a) {
  const p = pctPart(b, a);
  return p === DASH ? DASH : `−${p}%`;
}
