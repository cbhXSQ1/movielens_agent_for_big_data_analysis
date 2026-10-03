import { el } from '../core/dom.js';

export function panel({ title, note, actions, id } = {}) {
  const root = el('section', 'panel');
  if (id) root.id = id;

  const head = el('div', 'panel__head');
  if (title) head.appendChild(el('h2', 'panel__title', title));
  /* 裁定 R47：aside 常驻。早先只在 note/actions 为真时才建，
     于是 `panel({title}) ` 之后再 `querySelector('.panel__aside')?.prepend(...)`
     会被 `?.` 静默吞掉 —— 内容凭空消失且不报错。 */
  const right = el('div', 'panel__aside');
  if (note) right.appendChild(el('span', 'panel__note', note));
  for (const a of actions || []) right.appendChild(a);
  head.appendChild(right);
  if (head.childNodes.length) root.appendChild(head);

  const body = el('div', 'panel__body');
  root.appendChild(body);
  return { root, body, head };
}
