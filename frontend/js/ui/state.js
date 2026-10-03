/* 五态统一容器（spec §5.7）。
   aria-live 容器必须先存在于 DOM 再注入内容；焦点不移动到指示器。 */
import { el, clear } from '../core/dom.js';

export function renderState(host, { kind, title, body, actionLabel, onAction }) {
  clear(host);
  host.dataset.state = kind;

  const box = el('div', `state state--${kind}`);
  if (kind === 'loading') {
    box.setAttribute('role', 'status');
    const sk = el('div', 'state__skeleton');
    sk.setAttribute('aria-hidden', 'true');
    box.appendChild(sk);
  }
  if (title) box.appendChild(el('p', 'state__title', title));
  if (body) box.appendChild(el('p', 'state__body', body));

  if (actionLabel && onAction) {
    const btn = el('button', 'btn btn--ghost btn--sm', actionLabel);
    btn.type = 'button';
    btn.addEventListener('click', onAction);
    box.appendChild(btn);
  }
  host.appendChild(box);
  return box;
}
