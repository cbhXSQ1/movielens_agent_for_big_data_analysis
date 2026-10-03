import { el, clear } from '../core/dom.js';
import { runBanners, judgeRun } from '../core/run-state.js';

/* 全局横幅：文案说事实，不说用途（约束 G11）。
   aria-live 容器先存在于 DOM（index.html 里已有 #banner-stack），内容后注入。 */
const ICON = {
  warn: 'M8 1.6 15 14H1L8 1.6z M8 6v3.6 M8 11.6v.8',
  danger: 'M8 1.6 15 14H1L8 1.6z M8 6v3.6 M8 11.6v.8',
  ok: 'M2 8.5 6 12.5 14 4',
  muted: 'M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 6v5',
};

function iconFor(kind) {
  const NS = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('width', '14'); s.setAttribute('height', '14');
  s.setAttribute('viewBox', '0 0 16 16'); s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', ICON[kind] || ICON.muted);
  p.setAttribute('fill', 'none'); p.setAttribute('stroke', 'currentColor');
  p.setAttribute('stroke-width', '1.7'); p.setAttribute('stroke-linejoin', 'round');
  s.appendChild(p);
  return s;
}

export function createBanners({ host, store }) {
  function render(state) {
    /* 裁定 R2：两个分支原先完全相同 → judged 恒为 unknown，绿色"全量"横幅永不出现。
       判据必须来自任务 4 的 judgeRun：执行环境看 published_dir，抽样看 opts.scope。 */
    const judged = judgeRun({
      opts: (state.task && state.task.opts) || {},
      publishedDir: state.result ? state.result.publishedDir : undefined,
    });
    const list = runBanners({
      judged,
      healthOk: state.health.ok === null ? null : state.health.ok,
    });
    clear(host);
    for (const b of list) {
      const box = el('div', `banner banner--${b.kind}`);
      box.dataset.key = b.key;
      box.appendChild(iconFor(b.kind));
      const text = el('div', 'banner__text');
      text.appendChild(el('span', 'banner__title', b.title));
      if (b.text) text.appendChild(document.createTextNode(' ' + b.text));
      box.appendChild(text);
      host.appendChild(box);
    }
    /* 裁定 R32：shell.css 的 `.shell` 高度用 `calc(100vh - 顶栏 - 任务条 - var(--banner-h, 0px))`，
       但 `--banner-h` 全树没有任何生产者 → 横幅一出现，外壳就比视口高出一个横幅，
       输入条被挤出视口。这里把它写实。 */
    document.documentElement.style.setProperty('--banner-h', host.offsetHeight + 'px');
  }
  return { render };
}
