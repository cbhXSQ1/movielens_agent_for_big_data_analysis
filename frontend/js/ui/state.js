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

/* 裁定 R62：加载指示按时长分档（spec §5.7）——0–300ms 不给指示，超过才显示骨架。
   返回 cancel()，取数落地时调用，避免慢请求把骨架留在屏幕上。 */
export function loadingAfter(host, ms = 300, title = '正在读取…') {
  let shown = false;
  const timer = setTimeout(() => { shown = true; renderState(host, { kind: 'loading', title }); }, ms);
  return () => {
    clearTimeout(timer);
    /* 取消时若骨架已经上屏：它此刻已被 clear(host) 换成了数据 / 错误态，
       但宿主的 data-state 还写着 "loading" —— 不收回，量到的"数据落地后仍是加载态"就是假的。 */
    if (shown && host.dataset.state === 'loading' && !host.querySelector('.state--loading')) {
      host.dataset.state = 'data';
    }
  };
}
