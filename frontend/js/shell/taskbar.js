import { el, clear } from '../core/dom.js';
import { duration, stageZh } from '../core/format.js';   // 裁定 R2：不用 int()，去掉未使用的导入
import { judgeRun, runBadges } from '../core/run-state.js';

/* 任务条：任务号 · 状态 · 口径徽标 · 进度 · 用时 · 切换任务。
   阶段进度从"左栏竖排 9 行"搬到这里，纵向省出约 300px（spec §4.1）。 */
export function createTaskbar({ host, store, api, onPickTask }) {
  function render(state) {
    clear(host);
    const t = state.task || {};

    if (!t.id) {
      host.appendChild(el('span', 'taskbar__id', '还没有任务'));
      return;
    }

    host.appendChild(el('span', 'taskbar__id', t.id));

    const statusText = { queued: '排队中', running: '进行中', succeeded: '已完成', failed: '失败' }[t.status] || '未知';
    host.appendChild(el('span', 'taskbar__status', statusText));

    const judged = judgeRun({ opts: t.opts, publishedDir: state.result ? state.result.publishedDir : undefined });
    for (const b of runBadges(judged)) {
      const chip = el('span', `badge badge--${b.kind}`, b.text);
      host.appendChild(chip);
    }

    if (t.status === 'running' || t.status === 'queued') {
      host.appendChild(el('span', 'taskbar__stage', stageZh(t.stage)));
      const bar = el('span', 'taskbar__bar');
      const fill = el('span', 'taskbar__fill');
      fill.style.width = `${Math.max(0, Math.min(100, t.percent || 0))}%`;
      bar.appendChild(fill);
      host.appendChild(bar);
      host.appendChild(el('span', 'taskbar__pct num', `${t.percent || 0}%`));
    }

    if (t.startedAt) {
      const secs = Math.max(0, (Date.now() - t.startedAt) / 1000);
      host.appendChild(el('span', 'taskbar__time num', duration(secs)));
    }

    const pick = el('button', 'btn btn--ghost btn--sm', '切换任务');
    pick.type = 'button';
    pick.addEventListener('click', onPickTask);
    host.appendChild(pick);
  }
  return { render };
}
