import { el, clear } from '../core/dom.js';

export function sortRows(rows, key, dir = 'asc') {
  const sign = dir === 'desc' ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    const xm = x == null || x === '', ym = y == null || y === '';
    if (xm && ym) return 0;
    if (xm) return 1;                    // 缺失值永远排末尾
    if (ym) return -1;
    if (typeof x === 'number' && typeof y === 'number') return sign * (x - y);
    return sign * String(x).localeCompare(String(y), 'zh-Hans-CN');
  });
}

export function filterByRule(rows, ruleId) {
  if (!ruleId) return rows;
  return rows.filter(r => r.rule_id === ruleId);
}

/* columns: [{ key, label, align, mono, render(row) }] */
export function renderTable(host, { columns, rows, caption, emptyText = '没有记录。' }) {
  clear(host);
  if (!rows || rows.length === 0) {
    host.appendChild(el('p', 'table__empty', emptyText));
    return;
  }
  const wrap = el('div', 'table__wrap');
  const table = el('table', 'table');
  if (caption) table.appendChild(el('caption', 'visually-hidden', caption));

  const thead = el('thead');
  const htr = el('tr');
  for (const c of columns) {
    const th = el('th', c.align === 'right' ? 'is-right' : null, c.label);
    th.scope = 'col';
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = el('tbody');
  for (const row of rows) {
    const tr = el('tr');
    for (const c of columns) {
      const td = el('td', [c.align === 'right' ? 'is-right' : '', c.mono ? 'mono' : ''].filter(Boolean).join(' ') || null);
      if (c.render) {
        const out = c.render(row);
        if (out instanceof Node) td.appendChild(out); else td.textContent = out == null ? '—' : String(out);
      } else {
        const v = row[c.key];
        td.textContent = v == null ? '—' : String(v);
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
}
