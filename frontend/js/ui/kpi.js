import { el } from '../core/dom.js';

export function kpi({ label, value, sub, tone = 'default' }) {
  const box = el('div', `kpi${tone === 'accent' ? ' kpi--accent' : ''}`);
  box.appendChild(el('div', 'kpi__label', label));
  const v = el('div', 'kpi__value', value == null ? '—' : String(value));
  box.appendChild(v);
  if (sub) box.appendChild(el('div', 'kpi__sub', sub));
  return box;
}
