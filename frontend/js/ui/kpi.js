import { el } from '../core/dom.js';

export function kpi({ label, value, sub, tone = 'default', hl }) {
  const box = el('div', `kpi${tone === 'accent' ? ' kpi--accent' : ''}`);
  /* D3 的落点标记（spec §4.4）：目标视图按 data-hl 找这张卡去高亮。 */
  if (hl) box.dataset.hl = hl;
  box.appendChild(el('div', 'kpi__label', label));
  const v = el('div', 'kpi__value', value == null ? '—' : String(value));
  box.appendChild(v);
  if (sub) box.appendChild(el('div', 'kpi__sub', sub));
  return box;
}
