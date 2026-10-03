import { el } from '../core/dom.js';

export function panel({ title, note, actions, id } = {}) {
  const root = el('section', 'panel');
  if (id) root.id = id;

  const head = el('div', 'panel__head');
  if (title) {
    const h = el('h2', 'panel__title', title);
    head.appendChild(h);
  }
  const right = el('div', 'panel__aside');
  if (note) right.appendChild(el('span', 'panel__note', note));
  for (const a of actions || []) right.appendChild(a);
  if (right.childNodes.length) head.appendChild(right);
  if (head.childNodes.length) root.appendChild(head);

  const body = el('div', 'panel__body');
  root.appendChild(body);
  return { root, body, head };
}
