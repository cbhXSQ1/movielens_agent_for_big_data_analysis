/* llm.steps 步骤流（迭代二阶段 6 的那块空白，spec §3.1-2、§11-R5）。
   只渲染 tool / args / ok / summary；完整 envelope 可能很大（agent/llm_loop.py:279 每步带完整信封），
   一律折叠在「查看原始返回」里按需展开，不默认渲染。 */
import { el, clear } from '../core/dom.js';

const ARG_MAX = 120;
const SUM_MAX = 200;

function clip(s, n) {
  const t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
}

export function summarizeSteps(steps) {
  if (!Array.isArray(steps)) return [];
  return steps.map((s, i) => ({
    index: i + 1,
    tool: String(s && s.tool || '(未知工具)'),
    argsText: clip(JSON.stringify((s && s.args) || {}), ARG_MAX),
    ok: !(s && s.ok === false),
    summary: clip(s && s.summary, SUM_MAX),
  }));
}

export function renderSteps(host, steps) {
  clear(host);
  const list = summarizeSteps(steps);
  if (list.length === 0) return false;
  const raw = Array.isArray(steps) ? steps : [];

  const wrap = el('details', 'steps');
  const sum = el('summary', 'steps__summary');
  sum.appendChild(el('span', 'steps__count', `这次做了 ${list.length} 步`));
  wrap.appendChild(sum);

  const ol = el('ol', 'steps__list');
  list.forEach((s, i) => {
    const li = el('li', `step${s.ok ? ' step--ok' : ' step--fail'}`);
    li.appendChild(el('span', 'step__no num', String(s.index)));
    li.appendChild(el('span', 'step__tool', s.tool));
    const args = el('span', 'step__args', s.argsText);
    args.title = s.argsText;
    li.appendChild(args);
    li.appendChild(el('span', 'step__state', s.ok ? '成功' : '失败'));
    li.appendChild(el('span', 'step__say', s.summary));

    /* spec §11-R5：完整信封**不默认渲染**，但必须能按需展开（信封可能很大，所以折叠）。 */
    const env = raw[i] && raw[i].envelope;
    if (env !== undefined) {
      const d = el('details', 'step__raw');
      d.appendChild(el('summary', 'step__raw-summary', '查看原始返回'));
      const pre = el('pre', 'step__raw-body');
      try { pre.textContent = JSON.stringify(env, null, 2); } catch { pre.textContent = String(env); }
      d.appendChild(pre);
      li.appendChild(d);
    }
    ol.appendChild(li);
  });
  wrap.appendChild(ol);
  host.appendChild(wrap);
  return true;
}
